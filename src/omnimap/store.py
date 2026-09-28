"""Workspace: keeps every ingested payload and folds the canonical records into unified orders.

The unified view is rebuilt from the full record list on every read, so the result does not depend on
arrival order: a fulfillment webhook that lands before its order, or tracking scans that arrive before
the shipment, still join up once the missing piece is ingested. Re-delivering the same webhook is
idempotent because lines, shipments and events are merged by id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .carriers import CarrierRegistry, default_registry
from .mapping import MappingSet, MapResult, Record

KIND_ORDER = {"order": 0, "lines": 1, "shipment": 2, "tracking": 3}


def norm_tracking(num: Any) -> str | None:
    if num is None:
        return None
    s = re.sub(r"\s+", "", str(num)).upper()
    return s or None


@dataclass
class Source:
    id: int
    label: str
    channel: str | None
    payload_type: str | None
    records: list[Record] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    payload: Any = None
    context: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"id": self.id, "label": self.label, "channel": self.channel, "payload_type": self.payload_type,
                "records": len(self.records), "errors": self.errors,
                "kinds": sorted({r.kind for r in self.records})}


def _fill(target: dict, data: dict, keys) -> None:
    for k in keys:
        v = data.get(k)
        if v not in (None, "", {}):
            if isinstance(v, dict) and isinstance(target.get(k), dict) and "utc" not in v:
                _fill(target[k], v, v.keys())
            else:
                target[k] = v


def _line_key(line: dict, idx: int) -> str:
    return str(line.get("line_id") or line.get("sku") or f"#{idx}")


class Workspace:
    def __init__(self, mappings: MappingSet | None = None, carriers: CarrierRegistry | None = None):
        self.mappings = mappings or MappingSet.load_dir()
        self.carriers = carriers or default_registry()
        self.sources: list[Source] = []

    # -- ingest ------------------------------------------------------------------------------------
    def map(self, payload: Any, context: dict | None = None, channel: str | None = None) -> MapResult:
        return self.mappings.map(payload, context, channel)

    def ingest(self, payload: Any, context: dict | None = None, channel: str | None = None,
               label: str | None = None) -> tuple[Source, MapResult]:
        result = self.map(payload, context, channel)
        src = Source(len(self.sources) + 1, label or result.payload_type or "unrecognised payload",
                     result.channel, result.payload_type, result.records, result.errors, payload, context or {})
        self.sources.append(src)
        return src, result

    def reset(self) -> None:
        self.sources.clear()

    def records(self) -> list[tuple[int, Record]]:
        out = [(s.id, r) for s in self.sources for r in s.records]
        return sorted(out, key=lambda sr: KIND_ORDER[sr[1].kind])  # stable: keeps arrival order per kind

    # -- unified view ------------------------------------------------------------------------------
    def build(self) -> dict:
        orders: dict[str, dict] = {}
        orphans: list[dict] = []
        by_tracking: dict[str, dict] = {}

        def order(channel: str, ext: Any) -> dict:
            key = f"{channel}:{ext}"
            if key not in orders:
                orders[key] = {"key": key, "channel": channel, "external_id": str(ext), "display_name": None,
                               "placed_at": None, "status": None, "updated_at": None, "currency": None, "total": None,
                               "customer": {}, "lines": {}, "shipments": {}, "unassigned_events": [],
                               "has_header": False, "sources": []}
            return orders[key]

        def merge_lines(target: dict, lines: list[dict]) -> None:
            for i, line in enumerate(lines or []):
                k = _line_key(line, i)
                target.setdefault(k, {})
                _fill(target[k], line, line.keys())

        for src_id, rec in self.records():
            d = rec.data
            if rec.kind == "order":
                o = order(rec.channel, d["external_id"])
                # a status-only update (e.g. a webhook) does not prove we have seen the order itself
                o["has_header"] = o["has_header"] or bool(d.get("placed_at") or d.get("lines"))
                _fill(o, d, ["display_name", "placed_at", "currency", "total", "customer"])
                # status: the newest update wins, whatever order the payloads arrived in
                new_at = (d.get("updated_at") or {}).get("utc") or ""
                if d.get("status") and new_at >= ((o.get("updated_at") or {}).get("utc") or ""):
                    o["status"] = d["status"]
                    o["updated_at"] = d.get("updated_at") or o.get("updated_at")
                merge_lines(o["lines"], d.get("lines"))
            elif rec.kind == "lines":
                o = order(rec.channel, d["order_ref"])
                merge_lines(o["lines"], d.get("lines"))
            elif rec.kind == "shipment":
                o = order(rec.channel, d["order_ref"])
                sid = str(d.get("shipment_id") or d.get("tracking_number") or f"pkg-{len(o['shipments']) + 1}")
                s = o["shipments"].setdefault(sid, {"shipment_id": sid, "name": None, "order_key": o["key"], "carrier_raw": None,
                                                    "carrier": None, "carrier_name": None, "service": None,
                                                    "tracking_number": None, "shipped_at": None, "origin": None,
                                                    "lines": {}, "events": []})
                _fill(s, d, ["name", "service", "tracking_number", "shipped_at", "origin"])
                if d.get("carrier"):
                    s["carrier_raw"] = d["carrier"]
                merge_lines(s["lines"], d.get("lines"))
            else:
                continue
            if src_id not in o["sources"]:
                o["sources"].append(src_id)

        for o in orders.values():
            for s in o["shipments"].values():
                s["carrier"] = self.carriers.normalize(s["carrier_raw"])
                s["carrier_name"] = self.carriers.name(s["carrier"])
                t = norm_tracking(s["tracking_number"])
                if t:
                    by_tracking[t] = s

        for src_id, rec in self.records():
            if rec.kind != "tracking":
                continue
            d = rec.data
            target = self._resolve_tracking(d, rec.channel, orders, by_tracking)
            events = [dict(e, source=src_id, channel=rec.channel) for e in d.get("events") or []]
            if isinstance(target, dict) and "shipment_id" in target:
                if d.get("carrier") and not target["carrier_raw"]:
                    target["carrier_raw"] = d["carrier"]
                    target["carrier"] = self.carriers.normalize(d["carrier"])
                    target["carrier_name"] = self.carriers.name(target["carrier"])
                self._add_events(target["events"], events)
                o = orders[target["order_key"]]
            elif isinstance(target, dict):              # an order with no single shipment to attach to
                self._add_events(target["unassigned_events"], events)
                o = target
            else:
                orphans.append({"channel": rec.channel, "source": src_id, "tracking_number": d.get("tracking_number"),
                                "shipment_ref": d.get("shipment_ref"), "order_ref": d.get("order_ref"),
                                "carrier_raw": d.get("carrier"), "events": events})
                continue
            if src_id not in o["sources"]:
                o["sources"].append(src_id)

        return {"orders": orders, "orphan_tracking": orphans}

    @staticmethod
    def _resolve_tracking(d: dict, channel: str, orders: dict, by_tracking: dict):
        t = norm_tracking(d.get("tracking_number"))
        if t and t in by_tracking:
            return by_tracking[t]
        ref = d.get("shipment_ref")
        if ref is not None:
            candidates = [o for o in orders.values() if o["channel"] == channel]
            if d.get("order_ref") is not None:
                candidates = [o for o in candidates if o["external_id"] == str(d["order_ref"])]
            for o in candidates:
                if str(ref) in o["shipments"]:
                    return o["shipments"][str(ref)]
        if d.get("order_ref") is not None:
            o = orders.get(f"{channel}:{d['order_ref']}")
            if o:
                ships = list(o["shipments"].values())
                return ships[0] if len(ships) == 1 else o
        return None

    @staticmethod
    def _add_events(target: list, events: list) -> None:
        seen = {(e.get("occurred_at") or {}).get("utc", "") + "|" + str(e.get("status")) + "|" +
                str(e.get("description")) for e in target}
        for e in events:
            k = (e.get("occurred_at") or {}).get("utc", "") + "|" + str(e.get("status")) + "|" + str(e.get("description"))
            if k not in seen:
                seen.add(k)
                target.append(e)
        target.sort(key=lambda e: (e.get("occurred_at") or {}).get("utc") or "9999")


def timeline(o: dict) -> list[dict]:
    """Every dated thing that happened to one order, merged across shipments and sorted by UTC instant."""
    entries = []
    if o.get("placed_at"):
        entries.append({"type": "order", "at": o["placed_at"], "shipment_id": None,
                        "title": f"Order {o.get('display_name') or o['external_id']} placed",
                        "detail": f"{sum(int(l.get('quantity') or 0) for l in o['lines'].values())} units, "
                                  f"{o.get('total') or '?'} {o.get('currency') or ''}".strip()})
    for s in o["shipments"].values():
        if s.get("shipped_at"):
            units = sum(int(l.get("quantity") or 0) for l in s["lines"].values())
            entries.append({"type": "shipment", "at": s["shipped_at"], "shipment_id": s["shipment_id"],
                            "title": f"Shipment {s.get('name') or s['shipment_id']} created",
                            "detail": f"{units} unit(s) via {s.get('carrier_name') or s.get('carrier_raw') or 'unknown carrier'}"
                                      f" - tracking {s.get('tracking_number') or 'MISSING'}"})
        for e in s["events"]:
            entries.append({"type": "tracking", "at": e.get("occurred_at"), "shipment_id": s["shipment_id"],
                            "channel": e.get("channel"),
                            "status": e.get("status"), "title": e.get("description") or e.get("status"),
                            "detail": ", ".join(x for x in (e.get("city"), e.get("region"), e.get("country")) if x)})
    for e in o.get("unassigned_events", []):
        entries.append({"type": "tracking", "at": e.get("occurred_at"), "shipment_id": None,
                        "channel": e.get("channel"),
                        "status": e.get("status"), "title": e.get("description") or e.get("status"),
                        "detail": "not linked to a specific package"})
    entries.sort(key=lambda x: (x["at"] or {}).get("utc") or "9999")
    return entries
