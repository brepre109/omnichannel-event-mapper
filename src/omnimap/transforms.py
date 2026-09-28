"""Value coercions used by mapping rules (``as: datetime``, ``as: decimal`` ...)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo


class Instant(dict):
    """A timestamp that remembers how the channel expressed it.

    ``utc``    ISO-8601 in UTC, used for ordering and comparisons
    ``local``  ISO-8601 with the offset the channel sent (or UTC when the channel sent epoch time)
    ``offset`` e.g. "-04:00"; ``source`` one of iso-offset / iso-z / iso-naive / epoch-s / epoch-ms
    It is a dict so it serialises to JSON unchanged.
    """

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(self["utc"].replace("Z", "+00:00"))


def _fmt_offset(td: timedelta | None) -> str:
    if td is None:
        return "+00:00"
    total = int(td.total_seconds())
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def _instant(dt: datetime, source: str) -> Instant:
    utc = dt.astimezone(timezone.utc)
    return Instant(
        utc=utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        local=dt.isoformat(timespec="seconds"),
        offset=_fmt_offset(dt.utcoffset()),
        source=source,
    )


def to_instant(value: Any, fmt: str = "auto", assume_tz: str = "UTC") -> Instant | None:
    """Parse ISO-8601 (with offset, 'Z', or naive), epoch seconds or epoch milliseconds."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"not a timestamp: {value!r}")
    if fmt in ("epoch_s", "epoch_ms") or (fmt == "auto" and (isinstance(value, (int, float)) or
                                                             (isinstance(value, str) and value.isdigit()))):
        num = float(value)
        is_ms = fmt == "epoch_ms" or (fmt == "auto" and num > 1e11)
        dt = datetime.fromtimestamp(num / 1000 if is_ms else num, tz=timezone.utc)
        return _instant(dt, "epoch-ms" if is_ms else "epoch-s")
    text = str(value).strip()
    source = "iso-z" if text.endswith(("Z", "z")) else "iso-offset"
    text = re.sub(r"[Zz]$", "+00:00", text)
    text = text.replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} \d", text) else text
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"unparseable timestamp {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(assume_tz))
        source = "iso-naive"
    return _instant(dt, source)


def to_decimal(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return str(Decimal(str(value)))
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    d = Decimal(str(value))
    if d != d.to_integral_value():
        raise ValueError(f"not a whole number: {value!r}")
    return int(d)


def coerce(value: Any, kind: str | None, spec: dict) -> Any:
    if value is None:
        return None
    if kind in (None, "raw"):
        return value
    if kind == "string":
        return str(value)
    if kind == "int":
        return to_int(value)
    if kind == "decimal":
        return to_decimal(value)
    if kind == "datetime":
        return to_instant(value, spec.get("format", "auto"), spec.get("assume_tz", "UTC"))
    if kind == "upper":
        return str(value).upper()
    if kind == "lower":
        return str(value).lower()
    if kind == "postal_prefix":
        # keep only the coarse region part of a postcode: enough for zone analytics, not an address
        return re.sub(r"\s+", "", str(value))[:3].upper() or None
    raise ValueError(f"unknown coercion 'as: {kind}'")
