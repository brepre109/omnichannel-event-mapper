"""Carrier-name normalisation driven by ``mappings/carriers.yaml``."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_FILE = Path(__file__).parent / "mappings" / "carriers.yaml"


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


class CarrierRegistry:
    def __init__(self, table: dict):
        self.carriers = table
        self._index: dict[str, str] = {}
        for code, info in table.items():
            for alias in [code, info.get("name", code), *info.get("aliases", [])]:
                self._index[_key(str(alias))] = code

    @classmethod
    def load(cls, path: Path = DEFAULT_FILE) -> "CarrierRegistry":
        return cls(yaml.safe_load(path.read_text())["carriers"])

    def normalize(self, raw: str | None) -> str | None:
        """Return the canonical carrier code for any spelling we know, else None."""
        if not raw or not str(raw).strip():
            return None
        return self._index.get(_key(str(raw)))

    def name(self, code: str | None) -> str | None:
        return self.carriers.get(code, {}).get("name") if code else None


@lru_cache(maxsize=1)
def default_registry() -> CarrierRegistry:
    return CarrierRegistry.load()
