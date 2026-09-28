"""Mapping coverage: which canonical fields each channel's rules declare, and how often they are filled."""
from __future__ import annotations

from .mapping import MappingSet
from .schema import canonical_field_paths


def _key(kind: str, path: str) -> str | None:
    """Fold the "lines" kind into order.lines: where the lines come from is a transport detail."""
    if kind == "lines":
        return None if path == "order_ref" else f"order:{path}"
    return f"{kind}:{path}"


def _declared(mappings: MappingSet) -> dict[str, dict[str, list[str]]]:
    """channel -> "kind:field" -> [payload types declaring it]."""
    out: dict[str, dict[str, list[str]]] = {}
    for ch, m in mappings.by_channel.items():
        d = out.setdefault(ch, {})
        for p in m.payloads:
            for e in p["emit"]:
                names = list(e.get("fields", {})) + [f"{ln}[].{c}" for ln, ls in e.get("lists", {}).items()
                                                     for c in ls.get("fields", {})]
                for name in names:
                    k = _key(e["kind"], name)
                    if k and p["type"] not in d.setdefault(k, []):
                        d[k].append(p["type"])
    return out


def _get(data: dict, dotted: str):
    for k in dotted.split("."):
        data = data.get(k) if isinstance(data, dict) else None
    return data


def coverage(mappings: MappingSet, sources=()) -> dict:
    declared = _declared(mappings)
    # fill rate is measured only over payload types whose rules declare the field: "when this payload
    # arrives, how often does it actually carry the value?"
    filled: dict[str, dict[str, list[int]]] = {}  # channel -> key -> [non-empty, total]
    for src in sources:
        for rec in src.records:
            f = filled.setdefault(rec.channel, {})
            for kind, path, _ in canonical_field_paths(include_lines_kind=True):
                key = _key(kind, path) if kind == rec.kind else None
                if not key or rec.payload_type not in declared.get(rec.channel, {}).get(key, []):
                    continue
                if "[]." in path:
                    lname, col = path.split("[].")
                    rows = rec.data.get(lname) or []
                    vals = [_get(r, col) for r in rows]
                else:
                    vals = [_get(rec.data, path)]
                cell = f.setdefault(key, [0, 0])
                cell[0] += sum(v not in (None, "", []) for v in vals)
                cell[1] += len(vals)
    channels = sorted(mappings.by_channel)
    rows = []
    for kind, path, desc in canonical_field_paths():
        key = f"{kind}:{path}"
        row = {"kind": kind, "field": path, "description": desc, "channels": {}}
        for ch in channels:
            types = declared.get(ch, {}).get(key, [])
            n, total = filled.get(ch, {}).get(key, [0, 0])
            row["channels"][ch] = {"declared": bool(types), "payload_types": types,
                                   "filled": n, "seen": total,
                                   "fill_rate": round(n / total, 3) if total else None}
        rows.append(row)
    summary = {ch: {"declared": sum(r["channels"][ch]["declared"] for r in rows), "total": len(rows)}
               for ch in channels}
    return {"channels": channels, "rows": rows, "summary": summary}
