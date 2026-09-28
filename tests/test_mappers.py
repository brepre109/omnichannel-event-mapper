"""Each channel mapping against its sample payload fixtures."""
import pytest
import yaml

from conftest import fixture
from omnimap.loader import DEMO_DIR, load_fixture, parse_text
from omnimap.mapping import ChannelMapping, MappingError, MAPPINGS_DIR


def map_fx(mappings, name, index=0, use_ctx=True):
    d = fixture(name, index)
    return mappings.map(d["body"], d["context"] if use_ctx else {})


# ---------------------------------------------------------------- Shopify
class TestShopify:
    def test_orders_create(self, mappings):
        r = map_fx(mappings, "10-shopify-orders-create.json")
        assert (r.channel, r.payload_type, r.errors) == ("shopify", "orders/create", [])
        (rec,) = r.records
        d = rec.data
        assert rec.kind == "order"
        assert d["external_id"] == "5550001042" and d["display_name"] == "#1042"
        assert d["placed_at"]["utc"] == "2026-09-01T13:12:31Z"
        assert (d["currency"], d["total"]) == ("USD", "118.00")
        assert d["customer"] == {"country": "US", "region": "OR", "postal_prefix": "972"}
        assert [(l["sku"], l["quantity"], l["unit_price"], l["currency"]) for l in d["lines"]] == [
            ("MUG-12-BLU", 2, "24.00", "USD"), ("TEE-ORG-M", 1, "32.00", "USD"), ("CAP-CANVAS", 1, "22.00", "USD")]

    def test_customer_pii_not_copied(self, mappings):
        d = map_fx(mappings, "10-shopify-orders-create.json").records[0].data
        flat = repr(d)
        assert "buyer@example.com" not in flat and "Example St" not in flat

    def test_detects_by_header_or_by_shape(self, mappings):
        assert map_fx(mappings, "10-shopify-orders-create.json", use_ctx=False).payload_type == "orders/create"
        assert map_fx(mappings, "11-shopify-fulfillments-create.json", use_ctx=False).payload_type == "fulfillments/create"

    def test_header_name_is_case_insensitive(self, mappings):
        d = fixture("10-shopify-orders-create.json")
        assert mappings.map(d["body"], {"x-shopify-topic": "orders/create"}).payload_type == "orders/create"

    def test_fulfillment(self, mappings):
        d = map_fx(mappings, "11-shopify-fulfillments-create.json", 1).records[0].data
        assert d["order_ref"] == "5550001042" and d["shipment_id"] == "4400002" and d["name"] == "#1042.2"
        assert d["carrier"] == "United Parcel Service" and d["tracking_number"] == "1Z999AA10123456795"
        assert d["lines"] == [{"line_id": "13900000002", "sku": "TEE-ORG-M", "quantity": 1}]

    def test_fulfillment_falls_back_to_tracking_numbers_list(self, mappings):
        d = fixture("11-shopify-fulfillments-create.json")
        d["body"]["tracking_number"] = None
        rec = mappings.map(d["body"], d["context"]).records[0]
        assert rec.data["tracking_number"] == "1Z999AA10123456784"

    def test_fulfillment_event_status_mapping(self, mappings):
        statuses = [map_fx(mappings, "12-shopify-fulfillment-events.json", i).records[0].data["events"][0]["status"]
                    for i in range(5)]
        assert statuses == ["label_created", "in_transit", "in_transit", "out_for_delivery", "delivered"]

    def test_fulfillment_event_unknown_status(self, mappings):
        d = fixture("12-shopify-fulfillment-events.json")
        d["body"]["status"] = "teleported"
        assert mappings.map(d["body"], d["context"]).records[0].data["events"][0]["status"] == "unknown"


# ---------------------------------------------------------------- Amazon
class TestAmazon:
    def test_get_order(self, mappings):
        r = map_fx(mappings, "21-amazon-mfn-getOrder.json")
        d = r.records[0].data
        assert r.payload_type == "orders-v0/getOrder"
        assert d["external_id"] == "113-4827701-5550123" and d["status"] == "Unshipped"
        assert d["customer"] == {"country": "US", "region": "TX", "postal_prefix": "787"}
        assert "lines" not in d

    def test_get_order_items_become_lines_record(self, mappings):
        r = map_fx(mappings, "22-amazon-mfn-getOrderItems.json")
        (rec,) = r.records
        assert rec.kind == "lines" and rec.data["order_ref"] == "113-4827701-5550123"
        assert rec.data["lines"][0] == {"line_id": "61452091735211", "sku": "MUG-12-BLU",
                                        "title": "Stoneware Mug 12oz - Blue", "quantity": 1,
                                        "extended_price": "24.99", "currency": "USD"}

    def test_confirm_shipment_takes_order_id_from_url_context(self, mappings):
        rec = map_fx(mappings, "23-amazon-mfn-confirmShipment.json").records[0]
        assert rec.kind == "shipment" and rec.data["order_ref"] == "113-4827701-5550123"
        assert rec.data["carrier"] == "UPS" and rec.data["service"] == "UPS Ground"

    def test_confirm_shipment_without_context_is_an_error(self, mappings):
        r = map_fx(mappings, "23-amazon-mfn-confirmShipment.json", use_ctx=False)
        assert r.records == [] and "missing order_ref" in r.errors[0]

    def test_confirm_shipment_other_carrier_uses_carrier_name(self, mappings):
        d = fixture("23-amazon-mfn-confirmShipment.json")
        d["body"]["packageDetail"].update(carrierCode="Other", carrierName="Speedy Parcel Co")
        assert mappings.map(d["body"], d["context"]).records[0].data["carrier"] == "Speedy Parcel Co"

    def test_fba_report_tsv(self, mappings):
        (d,) = load_fixture(DEMO_DIR / "26-amazon-fba-fulfilled-shipments.tsv")
        r = mappings.map(d["body"])
        assert r.payload_type.startswith("report/GET_AMAZON_FULFILLED_SHIPMENTS")
        assert [x.data["lines"][0]["quantity"] for x in r.records] == [3, 1]
        assert {x.data["shipment_id"] for x in r.records} == {"DkQ7rVb2N"}
        assert r.records[0].data["shipped_at"]["utc"] == "2026-09-04T11:03:00Z"

    def test_mcf_tracking(self, mappings):
        rec = map_fx(mappings, "20-amazon-mcf-package-tracking.json").records[0]
        assert rec.kind == "tracking" and rec.data["tracking_number"] == "1Z999AA10123456795"
        assert [e["status"] for e in rec.data["events"]] == [
            "label_created", "in_transit", "in_transit", "in_transit", "out_for_delivery", "delivered"]


# ---------------------------------------------------------------- TikTok
class TestTikTok:
    def test_order_detail_order(self, mappings):
        r = map_fx(mappings, "30-tiktok-order-detail.json")
        order = next(x for x in r.records if x.kind == "order").data
        assert order["external_id"] == "576461413038785752"
        assert order["placed_at"] == {"utc": "2026-09-02T18:30:00Z", "local": "2026-09-02T18:30:00+00:00",
                                      "offset": "+00:00", "source": "epoch-s"}
        assert order["customer"] == {"country": "US", "region": "Arizona", "postal_prefix": "850"}
        assert (order["currency"], order["total"]) == ("USD", "62.96")
        # no quantity field on TikTok line items: one line item per unit
        assert [l["quantity"] for l in order["lines"]] == [1, 1, 1]

    def test_order_detail_emits_one_shipment_record_per_packaged_line(self, mappings):
        ships = [x.data for x in map_fx(mappings, "30-tiktok-order-detail.json").records if x.kind == "shipment"]
        assert len(ships) == 3
        assert {s["shipment_id"] for s in ships} == {"1152921506870911111"}
        assert ships[0]["order_ref"] == "576461413038785752"      # read from the parent order via ^.id
        assert ships[0]["shipped_at"]["utc"] == "2026-09-03T16:00:00Z"

    def test_unpackaged_line_items_are_skipped_silently(self, mappings):
        d = fixture("30-tiktok-order-detail.json")
        for li in d["body"]["data"]["orders"][0]["line_items"]:
            li["package_id"] = None
        r = mappings.map(d["body"])
        assert [x.kind for x in r.records] == ["order"] and r.errors == []

    def test_tracking_uses_epoch_millis_and_text_classification(self, mappings):
        rec = map_fx(mappings, "31-tiktok-get-tracking.json").records[0]
        assert rec.data["order_ref"] == "576461413038785752"
        ev = rec.data["events"]
        assert ev[0]["occurred_at"]["source"] == "epoch-ms"
        assert [e["status"] for e in ev] == ["delivered", "out_for_delivery", "in_transit", "in_transit", "label_created"]

    def test_order_status_webhook(self, mappings):
        rec = map_fx(mappings, "32-tiktok-webhook-order-status.json").records[0]
        assert (rec.data["external_id"], rec.data["status"]) == ("576461413038785752", "DELIVERED")
        assert rec.data["updated_at"]["utc"] == "2026-09-05T19:21:00Z"

    def test_package_update_webhook_is_recognised_but_maps_to_nothing(self, mappings):
        r = map_fx(mappings, "33-tiktok-webhook-package-update.json")
        assert r.matched and r.records == [] and r.errors == []


# ---------------------------------------------------------------- engine behaviour
class TestMappingEngine:
    def test_unrecognised_payload(self, mappings):
        r = mappings.map({"hello": "world"})
        assert not r.matched and r.errors

    def test_forced_channel_does_not_match_other_channel_payload(self, mappings):
        d = fixture("10-shopify-orders-create.json")
        assert not mappings.map(d["body"], {}, channel="amazon").matched

    def test_unknown_channel(self, mappings):
        with pytest.raises(MappingError):
            mappings.map({}, channel="ebay")

    def test_bad_timestamp_reported_not_raised(self, mappings):
        d = fixture("10-shopify-orders-create.json")
        d["body"]["created_at"] = "yesterday-ish"
        r = mappings.map(d["body"], d["context"])
        assert r.records == [] and "unparseable timestamp" in r.errors[0]

    def test_rules_may_only_write_canonical_fields(self):
        doc = yaml.safe_load((MAPPINGS_DIR / "shopify.yaml").read_text())
        doc["payloads"][0]["emit"][0]["fields"]["favourite_colour"] = "note"
        with pytest.raises(MappingError, match="favourite_colour"):
            ChannelMapping(doc)

    def test_rules_may_only_emit_known_kinds(self):
        with pytest.raises(MappingError):
            ChannelMapping({"channel": "x", "payloads": [{"type": "t", "detect": [], "emit": [{"kind": "invoice"}]}]})

    def test_parse_text_json_and_tsv(self):
        assert parse_text('{"a": 1}') == {"a": 1}
        assert parse_text("a\tb\n1\t\n") == [{"a": "1", "b": None}]
        with pytest.raises(ValueError):
            parse_text("not json at all")


def test_adding_a_channel_guide_example_works():
    """The YAML in docs/ADDING_A_CHANNEL.md is real: load it and map a payload with it."""
    import re
    from pathlib import Path

    from omnimap.mapping import MappingSet

    md = (Path(__file__).parent.parent / "docs" / "ADDING_A_CHANNEL.md").read_text()
    doc = yaml.safe_load(re.search(r"```yaml\n(.*?)```", md, re.S).group(1))
    ms = MappingSet([ChannelMapping(doc)])
    payload = {"order": {"id": 42, "created": 1788805800000, "totals": {"currency": "EUR", "grand": "9.50"},
                         "ship_to": {"country": "de", "zip": "10115"},
                         "items": [{"id": 1, "sku": "S-1", "qty": "2", "price": "4.75"}]}}
    r = ms.map(payload, {"X-Acme-Event": "order.created"})
    d = r.records[0].data
    assert r.errors == [] and d["external_id"] == "42" and d["placed_at"]["utc"] == "2026-09-07T18:30:00Z"
    assert d["customer"] == {"country": "DE", "postal_prefix": "101"}
    assert d["lines"] == [{"line_id": "1", "sku": "S-1", "quantity": 2, "unit_price": "4.75", "currency": "EUR"}]
    assert ms.map(payload).payload_type == "order.created"        # shape-based detection too
