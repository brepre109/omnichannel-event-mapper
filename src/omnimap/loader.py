"""Reading payloads: JSON text, TSV flat-file reports, and fixture envelopes."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

DEMO_DIR = Path(__file__).parent / "fixtures" / "demo"


def parse_text(text: str) -> Any:
    """JSON if it parses; otherwise a tab-separated report (header row + rows) becomes a list of dicts."""
    text = text.strip()
    if not text:
        raise ValueError("empty payload")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        first = text.splitlines()[0]
        if "\t" in first:
            rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
            return [{k: (v if v != "" else None) for k, v in r.items()} for r in rows]
        raise ValueError(f"not JSON ({exc.msg} at line {exc.lineno}) and not a TSV report") from exc


def load_fixture(path: Path) -> list[dict]:
    """Read one fixture file into a list of deliveries ``{"label", "channel", "context", "body"}``.

    JSON fixtures are ``{"label", "channel"?, "context"?, "body"}`` or, for a burst of webhooks,
    ``{"label", "deliveries": [{"context", "body"}, ...]}``. ``.tsv`` files are bare flat-file reports.
    """
    if path.suffix == ".tsv":
        return [{"label": path.stem, "channel": None, "context": {}, "body": parse_text(path.read_text()),
                 "file": path.name}]
    doc = json.loads(path.read_text())
    items = doc.get("deliveries") or [doc]
    out = []
    for i, d in enumerate(items):
        label = doc.get("label", path.stem) + (f" ({i + 1}/{len(items)})" if len(items) > 1 else "")
        out.append({"label": d.get("label", label), "channel": d.get("channel", doc.get("channel")),
                    "context": d.get("context", doc.get("context", {})), "body": d["body"], "file": path.name})
    return out


def demo_fixtures(directory: Path = DEMO_DIR) -> list[dict]:
    return [d for p in sorted(directory.iterdir()) if p.suffix in (".json", ".tsv") for d in load_fixture(p)]
