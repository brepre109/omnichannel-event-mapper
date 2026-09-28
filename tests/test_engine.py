"""Path language, value coercion and carrier normalisation."""
import pytest

from omnimap.carriers import default_registry
from omnimap.paths import iter_root, resolve
from omnimap.transforms import coerce, to_instant

DOC = {"id": 7, "items": [{"sku": "A", "q": 1}, {"sku": "B", "q": 2}],
       "district": [{"level": "L0", "name": "US"}, {"level": "L1", "name": "Oregon"}],
       "orders": [{"id": "o1", "lines": [{"id": "l1"}, {"id": "l2"}]}, {"id": "o2", "lines": [{"id": "l3"}]}]}


class TestPaths:
    def test_dotted_and_index(self):
        assert resolve("items[1].sku", DOC) == "B"
        assert resolve("items[-1].q", DOC) == 2
        assert resolve("items[5].sku", DOC) is None

    def test_missing_keys_are_none_not_errors(self):
        assert resolve("nope.deeper[0].x", DOC) is None
        assert resolve("id.x", DOC) is None

    def test_fan_out(self):
        assert resolve("items[*].sku", DOC) == ["A", "B"]
        assert resolve("orders[*].lines[*].id", DOC) == ["l1", "l2", "l3"]

    def test_filter_selector(self):
        assert resolve("district[level=L1].name", DOC) == "Oregon"
        assert resolve("district[level=L9].name", DOC) is None

    def test_ctx_root_and_self(self):
        assert resolve("$ctx.topic", DOC, ctx={"topic": "t"}) == "t"
        assert resolve("@", DOC) is DOC
        assert resolve("$root.id", {"x": 1}, root=DOC) == 7

    def test_iter_root_parent_chain(self):
        got = [(el["id"], resolve("^.id", el, parents=parents)) for el, parents in iter_root(DOC, "orders[*].lines[*]")]
        assert got == [("l1", "o1"), ("l2", "o1"), ("l3", "o2")]

    def test_iter_root_without_fanout(self):
        (el, parents), = list(iter_root({"payload": {"a": 1}}, "payload"))
        assert el == {"a": 1} and parents[0] == {"payload": {"a": 1}}


class TestTimestamps:
    def test_iso_with_offset_keeps_local_and_normalises_utc(self):
        i = to_instant("2026-09-03T21:10:00-07:00")
        assert i["utc"] == "2026-09-04T04:10:00Z"
        assert i["local"] == "2026-09-03T21:10:00-07:00"
        assert i["offset"] == "-07:00" and i["source"] == "iso-offset"

    def test_iso_z(self):
        assert to_instant("2026-09-02T02:14:07Z")["utc"] == "2026-09-02T02:14:07Z"
        assert to_instant("2026-09-02T02:14:07Z")["source"] == "iso-z"

    def test_naive_uses_assumed_zone(self):
        i = to_instant("2026-01-15 09:00:00", assume_tz="America/New_York")
        assert i["utc"] == "2026-01-15T14:00:00Z" and i["source"] == "iso-naive"

    def test_epoch_seconds_and_millis(self):
        assert to_instant(1788805800)["utc"] == to_instant(1788805800000)["utc"] == "2026-09-07T18:30:00Z"
        assert to_instant(1788805800)["source"] == "epoch-s"
        assert to_instant(1788805800000)["source"] == "epoch-ms"
        assert to_instant("1788805800", fmt="epoch_s")["utc"] == "2026-09-07T18:30:00Z"

    def test_bad_timestamp_raises(self):
        with pytest.raises(ValueError):
            to_instant("next tuesday")

    def test_empty_is_none(self):
        assert to_instant(None) is None and to_instant("") is None


class TestCoerce:
    def test_decimal_keeps_precision_as_string(self):
        assert coerce("24.10", "decimal", {}) == "24.10"
        assert coerce(0.1, "decimal", {}) == "0.1"

    def test_int_rejects_fractions(self):
        assert coerce("3", "int", {}) == 3
        with pytest.raises(ValueError):
            coerce("2.5", "int", {})

    def test_postal_prefix_drops_detail(self):
        assert coerce("M5V 2T6", "postal_prefix", {}) == "M5V"
        assert coerce("78704-1234", "postal_prefix", {}) == "787"

    def test_unknown_coercion(self):
        with pytest.raises(ValueError):
            coerce("x", "banana", {})


class TestCarriers:
    @pytest.mark.parametrize("raw", ["UPS", "ups", "U.P.S.", "United Parcel Service", "united parcel service"])
    def test_ups_spellings(self, raw):
        assert default_registry().normalize(raw) == "ups"

    @pytest.mark.parametrize("raw,code", [("FedEx Ground", "fedex"), ("FDXG", "fedex"), ("US Postal Service", "usps"),
                                          ("AMZN_US", "amazon_logistics"), ("Canada Post", "canada_post")])
    def test_other_carriers(self, raw, code):
        assert default_registry().normalize(raw) == code

    def test_unknown_and_empty(self):
        assert default_registry().normalize("Speedy Parcel Co") is None
        assert default_registry().normalize("  ") is None
        assert default_registry().normalize(None) is None

    def test_display_name(self):
        assert default_registry().name("fedex") == "FedEx"
