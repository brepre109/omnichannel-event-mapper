"""Workspace merging, the unified timeline, coverage and the HTTP API."""
import json
import random

from conftest import fixture, ingest
from omnimap import Workspace, timeline
from omnimap.app import create_app
from omnimap.coverage import coverage
from omnimap.loader import DEMO_DIR, load_fixture

ORDER = "shopify:5550001042"


def all_deliveries():
    return [d for p in sorted(DEMO_DIR.iterdir()) for d in load_fixture(p)]


def test_timeline_merges_channels_in_instant_order(demo_ws):
    tl = timeline(demo_ws.build()["orders"][ORDER])
    utcs = [e["at"]["utc"] for e in tl]
    assert utcs == sorted(utcs)
    # the Amazon arrival scan (01:00Z) precedes the Shopify scan sent as 2026-09-03T21:10-07:00 (04:10Z),
    # although a text sort of the raw timestamps would put the Shopify one first
    amazon = next(i for i, e in enumerate(tl) if e["title"] == "Arrival scan.")
    shopify = next(i for i, e in enumerate(tl) if e["title"] == "Arrived at carrier facility")
    assert amazon < shopify
    assert tl[amazon]["channel"] == "amazon" and tl[shopify]["channel"] == "shopify"
    assert tl[0]["type"] == "order"


def test_cross_channel_tracking_joins_by_tracking_number(demo_ws):
    ships = demo_ws.build()["orders"][ORDER]["shipments"]
    assert len(ships["4400002"]["events"]) == 6                 # Amazon MCF scans on a Shopify fulfillment
    assert {e["channel"] for e in ships["4400002"]["events"]} == {"amazon"}
    assert len(ships["4400001"]["events"]) == 5


def test_result_is_independent_of_arrival_order(mappings):
    deliveries = all_deliveries()
    a = Workspace(mappings)
    for d in deliveries:
        ingest(a, d)
    random.Random(7).shuffle(deliveries)
    b = Workspace(mappings)
    for d in deliveries:
        ingest(b, d)

    def normal(view):
        out = {}
        for k, o in view["orders"].items():
            o = dict(o, sources=None)
            o["shipments"] = {sid: dict(s, events=sorted(json.dumps(e, sort_keys=True) for e in
                                                         [dict(x, source=None) for x in s["events"]]))
                              for sid, s in o["shipments"].items()}
            out[k] = o
        return json.dumps(out, sort_keys=True)

    assert normal(a.build()) == normal(b.build())


def test_newest_status_wins_regardless_of_arrival(ws):
    ingest(ws, fixture("32-tiktok-webhook-order-status.json"))    # DELIVERED, updated 09-05
    ingest(ws, fixture("30-tiktok-order-detail.json"))            # IN_TRANSIT, updated 09-03, arrives later
    assert ws.build()["orders"]["tiktok:576461413038785752"]["status"] == "DELIVERED"


def test_redelivery_is_idempotent(ws):
    for _ in range(3):
        ingest(ws, fixture("12-shopify-fulfillment-events.json", 1))
    ingest(ws, fixture("10-shopify-orders-create.json"))
    ingest(ws, fixture("11-shopify-fulfillments-create.json", 0))
    assert len(ws.build()["orders"][ORDER]["shipments"]["4400001"]["events"]) == 1


def test_tiktok_packages_merge_by_package_id(demo_ws):
    o = demo_ws.build()["orders"]["tiktok:576461413038785752"]
    (pkg,) = o["shipments"].values()
    assert len(pkg["lines"]) == 3 and len(pkg["events"]) == 5
    assert o["status"] == "DELIVERED"          # later status webhook updated the order


def test_order_level_tracking_with_several_packages_is_kept_unassigned(ws):
    d = fixture("30-tiktok-order-detail.json")
    d["body"]["data"]["orders"][0]["line_items"][2]["package_id"] = "999"
    ingest(ws, d)
    ingest(ws, fixture("31-tiktok-get-tracking.json"))
    o = ws.build()["orders"]["tiktok:576461413038785752"]
    assert len(o["shipments"]) == 2 and len(o["unassigned_events"]) == 5
    assert all(e["shipment_id"] is None for e in timeline(o) if e["type"] == "tracking")


def test_coverage_report(demo_ws):
    cov = coverage(demo_ws.mappings, demo_ws.sources)
    assert cov["channels"] == ["amazon", "shopify", "tiktok"]
    row = {(r["kind"], r["field"]): r for r in cov["rows"]}
    assert row[("shipment", "tracking_number")]["channels"]["shopify"]["fill_rate"] == 0.75   # 3 of 4 carry one
    assert row[("order", "lines[].extended_price")]["channels"]["amazon"]["declared"]
    assert not row[("order", "lines[].unit_price")]["channels"]["amazon"]["declared"]
    assert row[("order", "lines[].sku")]["channels"]["amazon"]["payload_types"] == ["orders-v0/getOrderItems"]
    assert not any(r["kind"] == "lines" for r in cov["rows"])


# ---------------------------------------------------------------- HTTP API
def client(demo=True):
    return create_app(load_demo=demo).test_client()


def test_api_state_and_order():
    c = client()
    st = c.get("/api/state").get_json()
    assert len(st["orders"]) == 5 and len(st["sources"]) == 21 and len(st["warnings"]) == 6
    od = c.get("/api/order/" + ORDER).get_json()
    assert [w["rule"] for w in od["warnings"]] == ["missing_tracking_number"]
    assert len(od["timeline"]) == 15
    assert c.get("/api/order/nope:1").status_code == 404


def test_api_preview_does_not_commit_and_ingest_does():
    c = client()
    d = fixture("11-shopify-fulfillments-create.json", 2)
    body = {"text": json.dumps(d["body"]), "context": json.dumps(d["context"])}
    r = c.post("/api/preview", json=body).get_json()
    assert r["committed"] is False and r["result"]["payload_type"] == "fulfillments/create"
    assert "missing_tracking_number" in [w["rule"] for w in r["warnings"]]
    assert len(c.get("/api/state").get_json()["sources"]) == 21
    r = c.post("/api/ingest", json=body).get_json()
    assert r["committed"] is True and r["orders"] == [ORDER]
    assert len(c.get("/api/state").get_json()["sources"]) == 22


def test_api_accepts_tsv_and_rejects_garbage():
    c = client(demo=False)
    tsv = (DEMO_DIR / "26-amazon-fba-fulfilled-shipments.tsv").read_text()
    r = c.post("/api/preview", json={"text": tsv}).get_json()
    assert r["result"]["channel"] == "amazon" and len(r["result"]["records"]) == 2
    assert c.post("/api/preview", json={"text": "<xml/>"}).status_code == 400
    assert c.post("/api/preview", json={"text": "{}", "channel": "ebay"}).status_code == 400


def test_api_reset_and_demo_reload():
    c = client()
    c.post("/api/reset", json={})
    assert c.get("/api/state").get_json()["orders"] == []
    c.post("/api/reset", json={"demo": True})
    assert len(c.get("/api/state").get_json()["orders"]) == 5


def test_api_static_and_metadata():
    c = client()
    assert b"Omnichannel Event Mapper" in c.get("/").data
    assert c.get("/static/app.js").status_code == 200
    assert set(c.get("/api/mappings").get_json()) == {"amazon", "shopify", "tiktok", "carriers"}
    assert len(c.get("/api/samples").get_json()) == 21
    assert c.get("/api/coverage").get_json()["channels"] == ["amazon", "shopify", "tiktok"]
