# Canonical schema

Every channel payload is mapped into **records** of four kinds. The workspace then folds the records
into unified **orders**. The source of truth is [`src/omnimap/schema.py`](../src/omnimap/schema.py). A
mapping rule that writes a field not listed there fails when the mapping file loads.

## Record kinds

### `order`
| field | meaning |
|---|---|
| `external_id` | Channel's order id (required). The order key is `<channel>:<external_id>`. |
| `display_name` | Human-facing order number, e.g. `#1042` |
| `placed_at` | When the buyer placed the order (Instant) |
| `status` | Channel order status, verbatim |
| `updated_at` | When the channel last changed the order (Instant). The newest status wins. |
| `currency` | ISO-4217 currency of the order total |
| `total` | Order total as a decimal string |
| `customer.country` | ISO-3166 alpha-2 ship-to country |
| `customer.region` | Ship-to state / province |
| `customer.postal_prefix` | First 3 characters of the postcode. **No name, street, email or phone is kept.** |
| `lines[]` | `line_id`, `sku`, `title`, `quantity` (int), `unit_price`, `extended_price`, `currency` |

`unit_price` and `extended_price` are both included because channels disagree. Shopify's `price` is per
unit, while SP-API's `ItemPrice` is the price for the whole line (`price × QuantityOrdered`). The
mapper does not do arithmetic; it records which one the channel sent.

### `lines`
Order lines that arrive separately from the order header, for example SP-API `getOrderItems`. It has
`order_ref` plus `lines[]` with the same columns as `order.lines`. The coverage report folds these into
`order.lines`.

### `shipment`
One package or fulfillment. An order can have several.

| field | meaning |
|---|---|
| `order_ref` | `external_id` of the order |
| `shipment_id` | Channel's fulfillment / package id. Records with the same id are merged. |
| `name` | Human label, e.g. `#1042.1` |
| `carrier` | Carrier name **as sent**. It is normalised to a code by `carriers.yaml`. |
| `service` | Shipping service / method |
| `tracking_number` | Carrier tracking number |
| `shipped_at` | When the shipment was created / handed over (Instant) |
| `origin` | Location / fulfilment-centre id |
| `lines[]` | `line_id`, `sku`, `quantity` |

### `tracking`
Carrier scans for one package.

| field | meaning |
|---|---|
| `tracking_number` | Preferred join key. It matches across channels, so Amazon MCF scans attach to a Shopify fulfillment. |
| `carrier` | Carrier name as sent |
| `shipment_ref` | Channel package id, used when there is no tracking number (Shopify `fulfillment_id`) |
| `order_ref` | Last-resort join key. The events attach to the order's package only if it has exactly one. |
| `events[]` | `occurred_at` (Instant), `status`, `description`, `city`, `region`, `country` |

`events[].status` is one of `label_created`, `in_transit`, `out_for_delivery`, `delivered`,
`exception`, `unknown`.

## Instant

All timestamps become an *Instant*, so ordering never depends on how a channel formats time:

```json
{"utc": "2026-09-04T04:10:00Z", "local": "2026-09-03T21:10:00-07:00", "offset": "-07:00", "source": "iso-offset"}
```

`source` is one of `iso-offset`, `iso-z`, `iso-naive` (the `assume_tz` of the rule is applied),
`epoch-s` or `epoch-ms`. Sorting and every validation comparison use `utc`. The UI can show UTC, the
viewer's zone, or the value exactly as the channel sent it.

## How records become orders

`Workspace.build()` rebuilds the unified view from every stored record each time it is called:

1. `order` and `lines` records create or fill the order. Lines merge by `line_id`.
2. `shipment` records attach to their order and merge by `shipment_id`. If the order was never received,
   a stub order is created with `has_header = false`, which triggers the `unknown_order` warning.
3. Carrier names are normalised.
4. `tracking` events join by tracking number, then shipment ref, then order ref. They are
   de-duplicated on `(occurred_at, status, description)`.

Because of this, ingesting the same payloads in any order, or delivering a webhook twice, gives the
same result. The test suite checks this.

## Validation rules

| rule | severity | fires when |
|---|---|---|
| `missing_tracking_number` | error | a shipment has no tracking number |
| `unknown_carrier` | warning | the carrier name is not in `carriers.yaml`, or a tracking number has no carrier |
| `unknown_order` | error | shipments, lines or a status update reference an order that was never ingested, or scans match no package |
| `over_shipped` | error | units shipped for a line (summed over all packages) exceed units ordered, or a shipped line is not on the order |
| `timestamps_out_of_order` | warning | a shipment predates its order, a non-label scan predates its shipment, or transit scans follow a delivery |
| `currency_mismatch` | warning | a line is priced in a different currency than the order total |
| `mapping_error` | error | a payload was not recognised, a required field is missing, or a value could not be coerced |
