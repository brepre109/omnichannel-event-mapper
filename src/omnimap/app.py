"""Local web app: JSON API + a single static page."""
from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .coverage import coverage
from .loader import DEMO_DIR, demo_fixtures, parse_text
from .mapping import MappingError
from .store import Source, Workspace, timeline
from .validate import rule_catalog, validate

STATIC = Path(__file__).parent / "static"


def order_summary(o: dict, warnings: list[dict]) -> dict:
    return {"key": o["key"], "channel": o["channel"], "external_id": o["external_id"],
            "display_name": o.get("display_name") or o["external_id"], "placed_at": o.get("placed_at"),
            "has_header": o["has_header"], "shipments": len(o["shipments"]),
            "events": sum(len(s["events"]) for s in o["shipments"].values()) + len(o["unassigned_events"]),
            "warnings": sum(1 for w in warnings if w["order_key"] == o["key"])}


def order_detail(o: dict) -> dict:
    lines = []
    for k, l in o["lines"].items():
        shipped = sum(int(sl.get("quantity") or 0) for s in o["shipments"].values()
                      for sk, sl in s["lines"].items() if sk == k or (l.get("sku") and sl.get("sku") == l.get("sku")))
        lines.append(dict(l, key=k, shipped=shipped))
    shipments = [dict(s, lines=list(s["lines"].values())) for s in o["shipments"].values()]
    return dict(o, lines=lines, shipments=shipments)


def create_app(workspace: Workspace | None = None, load_demo: bool = True) -> Flask:
    app = Flask(__name__, static_folder=None)
    ws = workspace or Workspace()
    app.config["WORKSPACE"] = ws

    def ingest_demo():
        for fx in demo_fixtures():
            ws.ingest(fx["body"], fx["context"], fx.get("channel"), label=fx["label"])

    if load_demo:
        ingest_demo()

    def read_request():
        body = request.get_json(force=True, silent=True) or {}
        text = body.get("text")
        payload = parse_text(text) if isinstance(text, str) else body.get("payload")
        ctx = body.get("context") or {}
        if isinstance(ctx, str):
            ctx = json.loads(ctx) if ctx.strip() else {}
        return payload, ctx, (body.get("channel") or None), body.get("label")

    def evaluate(extra: Source | None = None) -> tuple[dict, list[dict]]:
        sources = ws.sources + ([extra] if extra else [])
        tmp = Workspace(ws.mappings, ws.carriers)
        tmp.sources = sources
        view = tmp.build()
        return view, validate(view, sources)

    @app.errorhandler(ValueError)
    def bad_request(exc):
        return jsonify({"error": str(exc)}), 400

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(STATIC, name)

    @app.get("/api/state")
    def state():
        view, warnings = evaluate()
        orders = sorted(view["orders"].values(), key=lambda o: (o.get("placed_at") or {}).get("utc") or "~")
        return jsonify({"sources": [s.summary() for s in ws.sources],
                        "orders": [order_summary(o, warnings) for o in orders],
                        "warnings": warnings, "rules": rule_catalog(),
                        "channels": {c: m.label for c, m in ws.mappings.by_channel.items()}})

    @app.get("/api/order/<path:key>")
    def order(key):
        view, warnings = evaluate()
        o = view["orders"].get(key)
        if not o:
            return jsonify({"error": f"no order {key}"}), 404
        return jsonify({"order": order_detail(o), "timeline": timeline(o),
                        "warnings": [w for w in warnings if w["order_key"] == key]})

    @app.post("/api/preview")
    @app.post("/api/ingest")
    def preview_or_ingest():
        try:
            payload, ctx, channel, label = read_request()
            result = ws.map(payload, ctx, channel)
        except (MappingError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc)}), 400
        src = Source(len(ws.sources) + 1, label or result.payload_type or "pasted payload", result.channel,
                     result.payload_type, result.records, result.errors, payload, ctx)
        view, warnings = evaluate(src)
        touched = [k for k, o in view["orders"].items() if src.id in o["sources"]]
        related = [w for w in warnings if (w["order_key"] in touched) or w.get("source") == src.id
                   or (w["order_key"] is None and w["rule"] == "unknown_order" and result.records
                       and result.records[0].kind == "tracking")]
        committed = request.path.endswith("/ingest")
        if committed:
            ws.sources.append(src)
        return jsonify({"committed": committed, "result": result.to_dict(), "orders": touched,
                        "warnings": related})

    @app.post("/api/reset")
    def reset():
        ws.reset()
        if (request.get_json(silent=True) or {}).get("demo"):
            ingest_demo()
        return jsonify({"ok": True, "sources": len(ws.sources)})

    @app.get("/api/coverage")
    def cov():
        return jsonify(coverage(ws.mappings, ws.sources))

    @app.get("/api/mappings")
    def mappings():
        out = {c: m.source.read_text() if m.source else "" for c, m in ws.mappings.by_channel.items()}
        carriers = Path(__file__).parent / "mappings" / "carriers.yaml"
        out["carriers"] = carriers.read_text()
        return jsonify(out)

    @app.get("/api/samples")
    def samples():
        return jsonify([{"file": f["file"], "label": f["label"], "channel": f.get("channel"),
                         "context": f["context"],
                         "text": (DEMO_DIR / f["file"]).read_text() if f["file"].endswith(".tsv")
                         else json.dumps(f["body"], indent=2)}
                        for f in demo_fixtures()])

    return app
