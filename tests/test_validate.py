"""Every validation rule: fires on a bad payload, stays quiet on a good one."""
from conftest import fixture, ingest
from omnimap.validate import RULES, validate


def rules(w):
    return sorted(x["rule"] for x in validate(w.build(), w.sources))


def shopify_base(w, *, skip=()):
    ingest(w, fixture("10-shopify-orders-create.json"))
    for i in range(2):
        if i not in skip:
            ingest(w, fixture("11-shopify-fulfillments-create.json", i))


def test_clean_order_has_no_warnings(ws):
    shopify_base(ws)
    assert rules(ws) == []


def test_demo_data_triggers_each_rule_exactly_once(demo_ws):
    got = rules(demo_ws)
    expected = sorted(c for c in RULES if c != "mapping_error")
    assert got == expected


# -- missing_tracking_number
def test_missing_tracking_number(ws):
    shopify_base(ws)
    ingest(ws, fixture("11-shopify-fulfillments-create.json", 2))      # #1042.3 has no tracking
    w = [x for x in validate(ws.build(), ws.sources) if x["rule"] == "missing_tracking_number"]
    assert len(w) == 1 and "#1042.3" in w[0]["message"] and w[0]["shipment_id"] == "4400003"


def test_tracking_number_present_is_fine(ws):
    shopify_base(ws)
    d = fixture("11-shopify-fulfillments-create.json", 2)
    d["body"]["tracking_number"] = "9400100000000000000000"
    ingest(ws, d)
    assert "missing_tracking_number" not in rules(ws)


# -- unknown_carrier
def test_unknown_carrier(ws):
    shopify_base(ws, skip=(1,))
    d = fixture("11-shopify-fulfillments-create.json", 1)
    d["body"]["tracking_company"] = "Speedy Parcel Co"
    ingest(ws, d)
    assert rules(ws) == ["unknown_carrier"]


def test_carrier_aliases_do_not_warn(ws):
    shopify_base(ws)   # "UPS" and "United Parcel Service"
    view = ws.build()
    assert {s["carrier"] for s in view["orders"]["shopify:5550001042"]["shipments"].values()} == {"ups"}
    assert "unknown_carrier" not in rules(ws)


def test_tracking_without_carrier_name_warns(ws):
    shopify_base(ws, skip=(1,))
    d = fixture("11-shopify-fulfillments-create.json", 1)
    d["body"]["tracking_company"] = None
    ingest(ws, d)
    assert rules(ws) == ["unknown_carrier"]


# -- unknown_order
def test_shipment_for_unknown_order(ws):
    ingest(ws, fixture("13-shopify-fulfillment-unknown-order.json"))
    assert rules(ws) == ["unknown_order"]


def test_unknown_order_clears_once_order_arrives(ws):
    ingest(ws, fixture("23-amazon-mfn-confirmShipment.json"))
    assert rules(ws) == ["unknown_order"]
    ingest(ws, fixture("21-amazon-mfn-getOrder.json"))
    ingest(ws, fixture("22-amazon-mfn-getOrderItems.json"))
    assert rules(ws) == []


def test_status_webhook_alone_does_not_count_as_order(ws):
    ingest(ws, fixture("32-tiktok-webhook-order-status.json"))
    assert rules(ws) == ["unknown_order"]


def test_tracking_for_unknown_shipment(ws):
    ingest(ws, fixture("20-amazon-mcf-package-tracking.json"))   # Shopify #1042.2 never ingested
    w = validate(ws.build(), ws.sources)
    assert [x["rule"] for x in w] == ["unknown_order"] and "1Z999AA10123456795" in w[0]["message"]


# -- over_shipped
def test_over_shipped(ws):
    ingest(ws, fixture("24-amazon-fba-getOrder.json"))
    items = fixture("25-amazon-fba-getOrderItems.json")
    items["body"]["payload"]["OrderItems"][0]["ItemPrice"]["CurrencyCode"] = "CAD"   # isolate this rule
    ingest(ws, items)
    ingest(ws, fixture("26-amazon-fba-fulfilled-shipments.tsv"))
    w = [x for x in validate(ws.build(), ws.sources)]
    assert [x["rule"] for x in w] == ["over_shipped"] and "3 shipped but only 2 ordered" in w[0]["message"]


def test_split_shipments_summing_to_ordered_qty_are_fine(ws):
    ingest(ws, fixture("10-shopify-orders-create.json"))
    for qty in (1, 1):                                   # two packages of 1 mug each, 2 ordered
        d = fixture("11-shopify-fulfillments-create.json", 0)
        d["body"]["id"] += qty * 100 + len(ws.sources)
        d["body"]["line_items"][0]["quantity"] = qty
        ingest(ws, d)
    assert "over_shipped" not in rules(ws)


def test_redelivered_webhook_does_not_double_count(ws):
    shopify_base(ws)
    ingest(ws, fixture("11-shopify-fulfillments-create.json", 0))   # same fulfillment again
    assert rules(ws) == []


def test_shipped_line_not_on_order(ws):
    shopify_base(ws)
    d = fixture("11-shopify-fulfillments-create.json", 2)
    d["body"]["tracking_number"] = "9400100000000000000000"
    d["body"]["line_items"][0].update(id=1, sku="GHOST")
    ingest(ws, d)
    w = [x for x in validate(ws.build(), ws.sources) if x["rule"] == "over_shipped"]
    assert "not on the order" in w[0]["message"]


# -- timestamps_out_of_order
def test_scan_before_shipment(ws):
    ingest(ws, fixture("30-tiktok-order-detail.json"))
    ingest(ws, fixture("31-tiktok-get-tracking.json"))
    w = [x for x in validate(ws.build(), ws.sources) if x["rule"] == "timestamps_out_of_order"]
    assert len(w) == 1 and "Package picked up by carrier" in w[0]["message"]


def test_label_created_before_shipment_is_allowed(ws):
    ingest(ws, fixture("30-tiktok-order-detail.json"))
    t = fixture("31-tiktok-get-tracking.json")
    t["body"]["data"]["tracking"] = [x for x in t["body"]["data"]["tracking"] if "picked up" not in x["description"]]
    ingest(ws, t)
    assert "timestamps_out_of_order" not in rules(ws)


def test_shipment_before_order(ws):
    shopify_base(ws, skip=(1,))
    d = fixture("11-shopify-fulfillments-create.json", 1)
    d["body"]["created_at"] = "2026-08-31T23:00:00-04:00"
    ingest(ws, d)
    assert rules(ws) == ["timestamps_out_of_order"]


def test_transit_scan_after_delivered(ws):
    shopify_base(ws)
    for i in range(5):
        ingest(ws, fixture("12-shopify-fulfillment-events.json", i))
    assert rules(ws) == []
    late = fixture("12-shopify-fulfillment-events.json", 2)
    late["body"].update(id=1, happened_at="2026-09-05T08:00:00-07:00", message="Departed facility")
    ingest(ws, late)
    w = [x for x in validate(ws.build(), ws.sources)]
    assert [x["rule"] for x in w] == ["timestamps_out_of_order"] and "after it was marked delivered" in w[0]["message"]


def test_offsets_are_compared_as_instants(ws):
    """10:00-07:00 is 17:00Z, i.e. *after* a 13:12Z order even though '10:00' < '13:12' as text."""
    ingest(ws, fixture("10-shopify-orders-create.json"))
    d = fixture("11-shopify-fulfillments-create.json", 0)
    d["body"]["created_at"] = "2026-09-01T10:00:00-07:00"
    ingest(ws, d)
    assert "timestamps_out_of_order" not in rules(ws)


# -- currency_mismatch
def test_currency_mismatch(ws):
    ingest(ws, fixture("24-amazon-fba-getOrder.json"))
    ingest(ws, fixture("25-amazon-fba-getOrderItems.json"))
    w = validate(ws.build(), ws.sources)
    assert [x["rule"] for x in w] == ["currency_mismatch"] and "USD" in w[0]["message"] and "CAD" in w[0]["message"]


def test_same_currency_is_fine(ws):
    ingest(ws, fixture("21-amazon-mfn-getOrder.json"))
    ingest(ws, fixture("22-amazon-mfn-getOrderItems.json"))
    assert rules(ws) == []


# -- mapping_error
def test_mapping_errors_surface_as_warnings(ws):
    ws.ingest({"not": "a known payload"})
    assert rules(ws) == ["mapping_error"]
