"""Validation rules over the unified view. Each rule is a plain function returning warnings.

Rules are about data quality a merchant (or an integration engineer onboarding one) should act on:
they never block ingestion.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

RULES: dict[str, dict] = {}


def rule(code: str, severity: str, title: str):
    def deco(fn: Callable):
        RULES[code] = {"code": code, "severity": severity, "title": title, "fn": fn}
        return fn
    return deco


def _w(code: str, message: str, order_key: str | None = None, shipment_id: str | None = None) -> dict:
    r = RULES[code]
    return {"rule": code, "severity": r["severity"], "title": r["title"], "message": message,
            "order_key": order_key, "shipment_id": shipment_id}


def _n(s: dict) -> str:
    return str(s.get("name") or s["shipment_id"])


def _utc(inst) -> str | None:
    return inst.get("utc") if isinstance(inst, dict) else None


@rule("missing_tracking_number", "error", "Missing tracking number")
def missing_tracking(view: dict) -> list[dict]:
    return [_w("missing_tracking_number",
               f"Shipment {_n(s)} ({s.get('carrier_raw') or 'no carrier'}) has no tracking number, "
               "so the buyer cannot be sent tracking updates.", o["key"], s["shipment_id"])
            for o in view["orders"].values() for s in o["shipments"].values() if not s.get("tracking_number")]


@rule("unknown_carrier", "warning", "Unknown carrier")
def unknown_carrier(view: dict) -> list[dict]:
    out = []
    for o in view["orders"].values():
        for s in o["shipments"].values():
            if s.get("carrier") is None and (s.get("carrier_raw") or s.get("tracking_number")):
                raw = s.get("carrier_raw")
                out.append(_w("unknown_carrier",
                              f"Carrier {raw!r} on shipment {_n(s)} is not in carriers.yaml; "
                              "add an alias so tracking can be routed." if raw else
                              f"Shipment {_n(s)} has a tracking number but no carrier name.",
                              o["key"], s["shipment_id"]))
    return out


@rule("unknown_order", "error", "Shipment for an unknown order")
def unknown_order(view: dict) -> list[dict]:
    out = []
    for o in view["orders"].values():
        if o["has_header"]:
            continue
        what = f"{len(o['shipments'])} shipment(s)" if o["shipments"] else "order lines"
        out.append(_w("unknown_order", f"{what} reference {o['channel']} order {o['external_id']}, "
                                       "which has not been ingested.", o["key"]))
    for t in view.get("orphan_tracking", []):
        ref = t.get("tracking_number") or t.get("shipment_ref") or t.get("order_ref")
        out.append(_w("unknown_order", f"{len(t['events'])} tracking event(s) for {ref!r} match no known "
                                       "shipment or order.", None))
    return out


@rule("over_shipped", "error", "Quantity shipped exceeds quantity ordered")
def over_shipped(view: dict) -> list[dict]:
    out = []
    for o in view["orders"].values():
        if not o["has_header"] and not o["lines"]:
            continue
        shipped: dict[str, int] = defaultdict(int)
        sku_to_line = {str(l.get("sku")): k for k, l in o["lines"].items() if l.get("sku")}
        for s in o["shipments"].values():
            for k, l in s["lines"].items():
                key = k if k in o["lines"] else sku_to_line.get(str(l.get("sku")), k)
                shipped[key] += int(l.get("quantity") or 0)
        for key, qty in shipped.items():
            line = o["lines"].get(key)
            if line is None:
                out.append(_w("over_shipped", f"{qty} unit(s) shipped for line {key}, which is not on the order.",
                              o["key"]))
            elif line.get("quantity") is not None and qty > int(line["quantity"]):
                out.append(_w("over_shipped", f"Line {line.get('sku') or key}: {qty} shipped but only "
                                              f"{line['quantity']} ordered.", o["key"]))
    return out


@rule("timestamps_out_of_order", "warning", "Timestamps out of order")
def timestamps_out_of_order(view: dict) -> list[dict]:
    out = []
    for o in view["orders"].values():
        placed = _utc(o.get("placed_at"))
        for s in o["shipments"].values():
            shipped = _utc(s.get("shipped_at"))
            if placed and shipped and shipped < placed:
                out.append(_w("timestamps_out_of_order", f"Shipment {_n(s)} is dated {shipped}, before the "
                                                         f"order was placed ({placed}).", o["key"], s["shipment_id"]))
            for e in s["events"]:
                at = _utc(e.get("occurred_at"))
                if at and shipped and at < shipped and e.get("status") not in ("label_created",):
                    out.append(_w("timestamps_out_of_order",
                                  f"'{e.get('description') or e.get('status')}' scan at {at} predates shipment "
                                  f"{_n(s)} ({shipped}).", o["key"], s["shipment_id"]))
            delivered = [_utc(e.get("occurred_at")) for e in s["events"] if e.get("status") == "delivered"]
            if delivered:
                later = [e for e in s["events"] if _utc(e.get("occurred_at")) and
                         _utc(e.get("occurred_at")) > min(delivered) and e.get("status") in ("in_transit",
                                                                                             "out_for_delivery")]
                if later:
                    out.append(_w("timestamps_out_of_order",
                                  f"Shipment {_n(s)} has {len(later)} transit scan(s) after it was "
                                  "marked delivered.", o["key"], s["shipment_id"]))
    return out


@rule("currency_mismatch", "warning", "Currency mismatch")
def currency_mismatch(view: dict) -> list[dict]:
    out = []
    for o in view["orders"].values():
        cur = o.get("currency")
        if not cur:
            continue
        for k, l in o["lines"].items():
            if l.get("currency") and l["currency"] != cur:
                out.append(_w("currency_mismatch", f"Line {l.get('sku') or k} is priced in {l['currency']} but the "
                                                   f"order total is in {cur}.", o["key"]))
    return out


@rule("mapping_error", "error", "Payload could not be fully mapped")
def _mapping_errors_placeholder(view: dict) -> list[dict]:
    return []  # produced from ingest results, see validate()


def validate(view: dict, sources=()) -> list[dict]:
    warnings: list[dict] = []
    for code, r in RULES.items():
        warnings.extend(r["fn"](view))
    for src in sources:
        for err in src.errors:
            w = _w("mapping_error", f"Source #{src.id} ({src.label}): {err}")
            w["source"] = src.id
            warnings.append(w)
    return warnings


def rule_catalog() -> list[dict]:
    return [{k: v for k, v in r.items() if k != "fn"} for r in RULES.values()]
