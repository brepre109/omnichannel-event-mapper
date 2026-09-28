"""Command line: ``omnimap serve | map | check | coverage``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .coverage import coverage
from .loader import DEMO_DIR, load_fixture, parse_text
from .store import Workspace
from .validate import validate


def _ingest_dir(ws: Workspace, directory: Path) -> None:
    for p in sorted(directory.iterdir()):
        if p.suffix in (".json", ".tsv"):
            for fx in load_fixture(p):
                ws.ingest(fx["body"], fx["context"], fx.get("channel"), label=fx["label"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="omnimap", description="Map channel order/shipment payloads to one schema.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the local web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8710)
    s.add_argument("--no-demo", action="store_true", help="start with an empty workspace")
    m = sub.add_parser("map", help="map one payload file and print the canonical records")
    m.add_argument("file", type=Path)
    m.add_argument("--context", default="{}", help='JSON, e.g. \'{"X-Shopify-Topic": "orders/create"}\'')
    m.add_argument("--channel")
    c = sub.add_parser("check", help="ingest a directory of fixtures and print validation warnings")
    c.add_argument("directory", type=Path, nargs="?", default=DEMO_DIR)
    v = sub.add_parser("coverage", help="print which canonical fields each channel fills")
    v.add_argument("directory", type=Path, nargs="?", default=DEMO_DIR)
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .app import create_app
        print(f"omnichannel-event-mapper on http://{args.host}:{args.port}")
        create_app(load_demo=not args.no_demo).run(host=args.host, port=args.port)
        return 0
    ws = Workspace()
    if args.cmd == "map":
        text = args.file.read_text()
        raw = parse_text(text)
        if isinstance(raw, dict) and ("body" in raw or "deliveries" in raw) and "label" in raw:
            deliveries = load_fixture(args.file)          # a fixture envelope
        else:
            deliveries = [{"body": raw, "context": json.loads(args.context), "channel": None}]
        ok = True
        for d in deliveries:
            res = ws.map(d["body"], d["context"], args.channel or d.get("channel"))
            print(json.dumps(res.to_dict(), indent=2))
            ok = ok and res.matched and not res.errors
        return 0 if ok else 1
    _ingest_dir(ws, args.directory)
    if args.cmd == "check":
        warnings = validate(ws.build(), ws.sources)
        for w in warnings:
            print(f"[{w['severity']:7}] {w['rule']:24} {w['order_key'] or '-':28} {w['message']}")
        print(f"{len(ws.sources)} payloads, {len(warnings)} warnings")
        return 0
    cov = coverage(ws.mappings, ws.sources)
    chans = cov["channels"]
    print(f"{'field':38}" + "".join(f"{c:>10}" for c in chans))
    for r in cov["rows"]:
        cells = []
        for ch in chans:
            cell = r["channels"][ch]
            cells.append("-" if not cell["declared"] else
                         ("yes" if cell["fill_rate"] is None else f"{cell['fill_rate']:.0%}"))
        print(f"{r['kind'] + '.' + r['field']:38}" + "".join(f"{x:>10}" for x in cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
