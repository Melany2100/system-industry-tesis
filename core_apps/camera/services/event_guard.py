from __future__ import annotations

import time
from threading import Lock

_EVENT_CACHE: dict[str, float] = {}
_EVENT_LOCK = Lock()


def _cleanup_expired(now: float, max_age: float = 60.0) -> None:
    expired_keys = [key for key, ts in _EVENT_CACHE.items() if (now - ts) > max_age]
    for key in expired_keys:
        _EVENT_CACHE.pop(key, None)


def normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def build_event_key(event_type: str, details: str, zone: str | None = None) -> str:
    event_type = normalize_text(event_type)
    details = normalize_text(details)
    zone = normalize_text(zone or "global")
    return f"{event_type}|{zone}|{details}"


def should_emit_event(event_key: str, cooldown_sec: float) -> bool:
    now = time.monotonic()

    with _EVENT_LOCK:
        _cleanup_expired(now)

        last_seen = _EVENT_CACHE.get(event_key)
        if last_seen is not None and (now - last_seen) < cooldown_sec:
            return False

        _EVENT_CACHE[event_key] = now
        return True