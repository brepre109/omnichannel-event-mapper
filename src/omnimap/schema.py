"""The canonical schema: the only shape the rest of the app (store, validators, UI) understands.

Mapping files may only write the fields declared here; ``ChannelMapping`` rejects anything else, so a
typo in a YAML rule fails at load time instead of silently producing an empty column.
"""

KINDS: dict[str, dict] = {
    "order": {
        "doc": "An order as the channel reported it at creation (or a later status update).",
        "fields": {
            "external_id": "Channel's order id (required; the order key is channel + external_id)",
            "display_name": "Human-facing order number, e.g. #1042",
            "placed_at": "When the buyer placed the order (Instant)",
            "status": "Channel order status, verbatim",
            "updated_at": "When the channel last changed the order (Instant); the newest status wins",
            "currency": "ISO-4217 currency of the order total",
            "total": "Order total as a decimal string",
            "customer.country": "ISO-3166 alpha-2 ship-to country",
            "customer.region": "Ship-to state / province code",
            "customer.postal_prefix": "First 3 characters of the ship-to postcode (no full address is kept)",
        },
        "lists": {
            "lines": {
                "line_id": "Channel's line / order-item id",
                "sku": "Seller SKU",
                "title": "Product title",
                "quantity": "Units ordered (int)",
                "unit_price": "Unit price as a decimal string",
                "extended_price": "Price for the whole line (unit price x quantity), when that is what the "
                                  "channel sends",
                "currency": "Currency of the line prices",
            },
        },
    },
    "lines": {
        "doc": "Order lines delivered separately from the order header (e.g. SP-API getOrderItems).",
        "fields": {"order_ref": "external_id of the order these lines belong to"},
        "lists": {"lines": None},  # same columns as order.lines, filled in below
    },
    "shipment": {
        "doc": "One package / fulfillment. An order may have several (split shipments).",
        "fields": {
            "order_ref": "external_id of the order",
            "shipment_id": "Channel's fulfillment / package id (join key for tracking events)",
            "name": "Human-facing package label, e.g. #1042.1",
            "carrier": "Carrier name as sent; normalised to a carrier code by carriers.yaml",
            "service": "Shipping service / method",
            "tracking_number": "Carrier tracking number",
            "shipped_at": "When the shipment was created / handed over (Instant)",
            "origin": "Where it shipped from (fulfillment centre / location id), free text",
        },
        "lists": {
            "lines": {
                "line_id": "Order line id this package contains",
                "sku": "SKU (used when the channel does not give line ids)",
                "quantity": "Units in this package (int)",
            },
        },
    },
    "tracking": {
        "doc": "Carrier scan events for one package.",
        "fields": {
            "tracking_number": "Tracking number the events belong to (preferred join key)",
            "carrier": "Carrier name as sent",
            "shipment_ref": "Channel shipment / package id, when no tracking number is given",
            "order_ref": "external_id of the order, as a last-resort join key",
        },
        "lists": {
            "events": {
                "occurred_at": "When the scan happened (Instant)",
                "status": "Canonical status: label_created | in_transit | out_for_delivery | delivered | "
                          "exception | unknown",
                "description": "Carrier / channel wording",
                "city": "Scan location city",
                "region": "Scan location state / province",
                "country": "Scan location country",
            },
        },
    },
}
KINDS["lines"]["lists"]["lines"] = KINDS["order"]["lists"]["lines"]

TRACKING_STATUSES = ["label_created", "in_transit", "out_for_delivery", "delivered", "exception", "unknown"]


def canonical_field_paths(include_lines_kind: bool = False) -> list[tuple[str, str, str]]:
    """(kind, field path, description) for every canonical field; list columns are written ``lines[].sku``.

    The "lines" kind only exists so order lines can arrive separately; it is folded into order.lines
    unless ``include_lines_kind`` is set.
    """
    out = []
    for kind, spec in KINDS.items():
        if kind == "lines" and not include_lines_kind:
            continue
        for name, desc in spec["fields"].items():
            out.append((kind, name, desc))
        for lname, cols in spec["lists"].items():
            for name, desc in cols.items():
                out.append((kind, f"{lname}[].{name}", desc))
    return out
