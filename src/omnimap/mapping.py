"""Declarative mapping engine: channel payload -> canonical records.

A channel is described entirely by a YAML file in ``mappings/`` (see docs/ADDING_A_CHANNEL.md).
Nothing in this module knows about Shopify, Amazon or TikTok.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import iter_root, resolve
from .schema import KINDS
from .transforms import coerce

MAPPINGS_DIR = Path(__file__).parent / "mappings"


class MappingError(ValueError):
    pass


@dataclass
class Record:
    kind: str                 # order | lines | shipment | tracking
    channel: str
    payload_type: str
    data: dict

    def to_dict(self) -> dict:
        return {"kind": self.kind, "channel": self.channel, "payload_type": self.payload_type, "data": self.data}


@dataclass
class MapResult:
    channel: str | None
    payload_type: str | None
    records: list[Record] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.payload_type is not None

    def to_dict(self) -> dict:
        return {"channel": self.channel, "payload_type": self.payload_type, "matched": self.matched,
                "records": [r.to_dict() for r in self.records], "errors": self.errors}


def _set(target: dict, dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    for k in keys[:-1]:
        target = target.setdefault(k, {})
    target[keys[-1]] = value


def _field_value(spec: Any, elem: Any, parents: list, ctx: dict, root: Any) -> Any:
    if isinstance(spec, str):
        spec = {"path": spec}
    if "const" in spec:
        value = spec["const"]
    else:
        paths = spec.get("paths") or [spec["path"]]
        value = None
        for p in paths:                     # first non-empty wins ("coalesce")
            value = resolve(p, elem, parents=parents, ctx=ctx, root=root)
            if value not in (None, "", []):
                break
        else:
            value = None
    if value is None and "default" in spec:
        value = spec["default"]
    if "map" in spec and value is not None:
        table = spec["map"]
        value = table.get(str(value), table.get(str(value).lower(), spec.get("map_default", value)))
    if "contains" in spec and value is not None:          # first case-insensitive substring match wins
        text = str(value).lower()
        value = next((v for k, v in spec["contains"].items() if str(k).lower() in text),
                     spec.get("contains_default", value))
    return coerce(value, spec.get("as"), spec)


class ChannelMapping:
    def __init__(self, doc: dict, source: Path | None = None):
        self.doc = doc
        self.source = source
        self.channel: str = doc["channel"]
        self.label: str = doc.get("label", self.channel)
        self.payloads: list[dict] = doc.get("payloads", [])
        self._check()

    def _check(self) -> None:
        for p in self.payloads:
            if "type" not in p or "emit" not in p or "detect" not in p:
                raise MappingError(f"{self.channel}: every payload needs type, detect and emit")
            for e in p["emit"]:
                if e.get("kind") not in KINDS:
                    raise MappingError(f"{self.channel}/{p['type']}: unknown kind {e.get('kind')!r}")
                allowed = KINDS[e["kind"]]
                for name in e.get("fields", {}):
                    if name not in allowed["fields"]:
                        raise MappingError(f"{self.channel}/{p['type']}: {name!r} is not a canonical "
                                           f"{e['kind']} field")
                for lname, lspec in e.get("lists", {}).items():
                    if lname not in allowed["lists"]:
                        raise MappingError(f"{self.channel}/{p['type']}: {lname!r} is not a canonical list")
                    for name in lspec.get("fields", {}):
                        if name not in allowed["lists"][lname]:
                            raise MappingError(f"{self.channel}/{p['type']}: {lname}[].{name} is not canonical")

    @classmethod
    def load(cls, path: Path) -> "ChannelMapping":
        return cls(yaml.safe_load(path.read_text()), path)

    # -- detection ---------------------------------------------------------------------------------
    @staticmethod
    def _cond_ok(cond: dict, payload: Any, ctx: dict) -> bool:
        for key, want in (cond.get("ctx") or {}).items():
            if str(ctx.get(key, "")).lower() != str(want).lower():
                return False
        for path in cond.get("has") or []:
            if resolve(path, payload, ctx=ctx) in (None, "", []):
                return False
        for path, want in (cond.get("equals") or {}).items():
            if resolve(path, payload, ctx=ctx) != want:
                return False
        return True

    def detect(self, payload: Any, ctx: dict) -> dict | None:
        for p in self.payloads:
            if any(self._cond_ok(c, payload, ctx) for c in p["detect"]):
                return p
        return None

    # -- mapping -----------------------------------------------------------------------------------
    def apply(self, spec: dict, payload: Any, ctx: dict) -> MapResult:
        result = MapResult(self.channel, spec["type"])
        for emit in spec["emit"]:
            for elem, parents in iter_root(payload, emit.get("root")):
                data: dict = {}
                try:
                    for name, fspec in emit.get("fields", {}).items():
                        _set(data, name, _field_value(fspec, elem, parents, ctx, payload))
                    for lname, lspec in emit.get("lists", {}).items():
                        items = []
                        for item, iparents in iter_root(elem, lspec.get("each", "@")):
                            chain = parents + iparents if item is not elem else parents
                            row = {}
                            for name, fspec in lspec.get("fields", {}).items():
                                _set(row, name, _field_value(fspec, item, chain, ctx, payload))
                            items.append(row)
                        data[lname] = items
                except (ValueError, KeyError) as exc:
                    result.errors.append(f"{spec['type']}: {exc}")
                    continue
                missing = [r for r in emit.get("require", []) if data.get(r) in (None, "")]
                if missing:
                    if not emit.get("skip_if_missing"):
                        result.errors.append(f"{spec['type']}: {emit['kind']} record missing {', '.join(missing)}")
                    continue
                result.records.append(Record(emit["kind"], self.channel, spec["type"], data))
        return result


class MappingSet:
    """All channel mappings, plus auto-detection across them."""

    def __init__(self, mappings: list[ChannelMapping]):
        self.by_channel = {m.channel: m for m in mappings}

    @classmethod
    def load_dir(cls, directory: Path = MAPPINGS_DIR) -> "MappingSet":
        files = sorted(p for p in directory.glob("*.yaml") if p.name != "carriers.yaml")
        return cls([ChannelMapping.load(p) for p in files])

    def map(self, payload: Any, ctx: dict | None = None, channel: str | None = None) -> MapResult:
        ctx = dict(ctx or {})
        ctx.update({k.lower(): v for k, v in ctx.items()})   # header names are case-insensitive
        if channel and channel not in self.by_channel:
            raise MappingError(f"unknown channel {channel!r}")
        candidates = [self.by_channel[channel]] if channel else list(self.by_channel.values())
        for m in candidates:
            spec = m.detect(payload, ctx)
            if spec:
                return m.apply(spec, payload, ctx)
        return MapResult(channel, None, errors=["no mapping rule recognised this payload"])
