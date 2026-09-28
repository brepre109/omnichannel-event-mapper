# Adding a new channel

A channel is **one YAML file** in `src/omnimap/mappings/`. You don't write any Python: the file is
picked up automatically and shows up in the UI, the coverage report and auto-detection.

## 1. Collect real sample payloads

Get one example of each payload you need: order created, shipment / fulfillment, and tracking if the
channel provides it. Scrub it (names, addresses, emails), then save it under
`src/omnimap/fixtures/demo/` (or `tests/`) as:

```json
{"label": "Acme Market order webhook", "channel": "acme",
 "context": {"X-Acme-Event": "order.created"},
 "body": { "...the payload exactly as received..." }}
```

`context` holds anything that is **not in the body**, such as webhook headers or ids from the URL path.

## 2. Write `mappings/acme.yaml`

```yaml
channel: acme
label: Acme Market
payloads:
  - type: order.created
    doc: https://developer.acme.example/webhooks#order-created      # cite the docs you checked
    detect:                          # any one of these conditions matches
      - ctx: {x-acme-event: order.created}          # header (case-insensitive)
      - has: [order.id, order.items]                 # or: these paths are all present
    emit:
      - kind: order
        root: order                  # optional: map from this sub-object; [*] emits one record per element
        require: [external_id]       # record is rejected (mapping_error) if these end up empty
        fields:
          external_id: {path: id, as: string}
          placed_at: {path: created, as: datetime, format: epoch_ms}
          currency: totals.currency
          total: {path: totals.grand, as: decimal}
          customer.country: {path: ship_to.country, as: upper}
          customer.postal_prefix: {path: ship_to.zip, as: postal_prefix}
        lists:
          lines:
            each: items[*]
            fields:
              line_id: {path: id, as: string}
              sku: {paths: [merchant_sku, sku]}      # first non-empty wins
              quantity: {path: qty, as: int}
              unit_price: {path: price, as: decimal}
              currency: ^.totals.currency            # ^. = the enclosing element
```

### Field-rule reference

| key | meaning |
|---|---|
| `path` | Where to read the value. Syntax: `a.b[0].c`, `list[*].x`, `list[key=value].x`, `@` (current element), `^.` (enclosing element), `$ctx.name` (delivery context), `$root.` (whole payload) |
| `paths` | Several paths; the first non-empty value wins |
| `const` | A fixed value, e.g. `quantity: {const: 1}` for channels with one line per unit |
| `default` | Used when the path is empty |
| `as` | `string`, `int`, `decimal`, `datetime`, `upper`, `lower`, `postal_prefix` |
| `format` / `assume_tz` | For `datetime`: `auto` (default), `epoch_s`, `epoch_ms`. `assume_tz` is the IANA zone used for naive timestamps. |
| `map` + `map_default` | Exact value lookup, e.g. channel status → canonical tracking status |
| `contains` + `contains_default` | Case-insensitive substring lookup, for channels that only send free text |

A shorthand string (`currency: totals.currency`) is the same as `{path: ...}`.

Emit-level keys: `kind`, `root`, `require`, `skip_if_missing` (drop records that fail `require` without
reporting an error, e.g. line items that are not packed yet), `fields`, `lists`. A payload with
`emit: []` is recognised but produces nothing. Use it for informational webhooks.

## 3. Teach it the carrier names

If the channel spells carriers its own way, add the spellings to `aliases` in
`mappings/carriers.yaml`. Until you do, each shipment gets an `unknown_carrier` warning, which is how
you find them.

## 4. Check it

```bash
omnimap map src/omnimap/fixtures/demo/40-acme-order.json   # prints canonical records, exit 1 on errors
omnimap check                                              # all fixtures -> warnings
omnimap coverage                                           # which canonical fields you fill
```

Then add tests next to `tests/test_mappers.py`: one test per payload type that asserts the important
fields, and one per edge case you hit (missing fields, odd timestamp formats). The **Mapping coverage**
tab in the UI shows the gaps you still have.
