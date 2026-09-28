"""A tiny path language for reading values out of JSON payloads.

Syntax (deliberately small, so mapping files stay readable):

    order.line_items[0].sku     dotted keys and list indexes
    line_items[*].sku           fan out over a list -> returns a list
    district_info[address_level=L1].address_name   first list element whose key equals a value
    @                           the current element itself
    ^.id                        the enclosing element one [*] level up (^.^.id = two levels)
    $ctx.topic                  a value from the delivery context (HTTP headers, URL path params)
    $root.data.shop_id          the whole payload, regardless of the current element

Missing keys resolve to None instead of raising: payloads from real channels omit fields all the time.
"""
from __future__ import annotations

import re
from typing import Any, Iterator

_TOKEN = re.compile(r"\[\*\]|\[-?\d+\]|\[[^\]=]+=[^\]]*\]|[^.\[\]]+")


def tokenize(path: str) -> list[str]:
    return _TOKEN.findall(path)


def _walk(value: Any, tokens: list[str]) -> Any:
    for i, tok in enumerate(tokens):
        if value is None:
            return None
        if tok == "[*]":
            if not isinstance(value, list):
                return None
            rest = tokens[i + 1:]
            out = []
            for item in value:
                got = _walk(item, rest)
                if isinstance(got, list) and "[*]" in rest:
                    out.extend(got)
                elif got is not None:
                    out.append(got)
            return out
        if tok.startswith("[") and "=" in tok:          # [key=value]: first element whose key equals value
            key, want = tok[1:-1].split("=", 1)
            value = next((x for x in value if isinstance(x, dict) and str(x.get(key)) == want), None) \
                if isinstance(value, list) else None
            continue
        if tok.startswith("["):
            if not isinstance(value, list):
                return None
            idx = int(tok[1:-1])
            value = value[idx] if -len(value) <= idx < len(value) else None
            continue
        value = value.get(tok) if isinstance(value, dict) else None
    return value


def resolve(path: str, current: Any, *, parents: list[Any] | None = None, ctx: dict | None = None,
            root: Any = None) -> Any:
    """Resolve ``path`` relative to ``current``."""
    parents = parents or []
    if path == "@":
        return current
    if path.startswith("$ctx."):
        return _walk(ctx or {}, tokenize(path[5:]))
    if path.startswith("$root."):
        base = root if root is not None else (parents[0] if parents else current)
        return _walk(base, tokenize(path[6:]))
    level = 0
    while path.startswith("^."):
        level += 1
        path = path[2:]
    if level:
        if level > len(parents):
            return None
        return _walk(parents[-level], tokenize(path))
    return _walk(current, tokenize(path))


def iter_root(payload: Any, root_path: str | None) -> Iterator[tuple[Any, list[Any]]]:
    """Yield (element, parents) for every element selected by ``root_path``.

    ``parents`` is the chain of containing elements, one per ``[*]`` crossed, starting with the payload,
    so field paths can reach "up" with ``^.`` (e.g. a TikTok line item reading its order's id).
    """
    if not root_path or root_path == "@":
        yield payload, [payload]
        return

    def rec(value: Any, tokens: list[str], parents: list[Any]):
        for i, tok in enumerate(tokens):
            if value is None:
                return
            if tok == "[*]":
                if isinstance(value, list):
                    for item in value:
                        yield from rec(item, tokens[i + 1:], parents + [item])
                return
            value = _walk(value, [tok])
        if value is not None:
            # parents[-1] is the element itself when the path ended on [*]; drop it so ^. means "enclosing"
            chain = parents[:-1] if parents and parents[-1] is value and len(parents) > 1 else parents
            yield value, chain

    yield from rec(payload, tokenize(root_path), [payload])
