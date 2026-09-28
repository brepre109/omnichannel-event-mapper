import copy
import json
from pathlib import Path

import pytest

from omnimap import MappingSet, Workspace
from omnimap.loader import DEMO_DIR, load_fixture


@pytest.fixture(scope="session")
def mappings():
    return MappingSet.load_dir()


def fixture(name: str, index: int = 0) -> dict:
    """One delivery from a demo fixture file, deep-copied so tests can mutate it."""
    return copy.deepcopy(load_fixture(DEMO_DIR / name)[index])


@pytest.fixture
def fx():
    return fixture


@pytest.fixture
def ws(mappings):
    return Workspace(mappings)


@pytest.fixture
def demo_ws(mappings):
    w = Workspace(mappings)
    for p in sorted(DEMO_DIR.iterdir()):
        for d in load_fixture(p):
            w.ingest(d["body"], d["context"], d.get("channel"), label=d["label"])
    return w


def ingest(w: Workspace, d: dict):
    return w.ingest(d["body"], d["context"], d.get("channel"), label=d["label"])[1]


def load_json(path: Path):
    return json.loads(path.read_text())
