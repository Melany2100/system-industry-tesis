from __future__ import annotations

import time
from collections import deque
from threading import Lock

_LIVE_LOG = deque(maxlen=300)
_LOG_LOCK = Lock()
_LOG_SEQ = 0
_LAST_LOG_TS: dict[str, float] = {}


def _format_message(message: str, repeat_count: int) -> str:
    if repeat_count <= 1:
        return message
    return f"{message} (x{repeat_count})"


def log_line(
    message: str,
    key: str | None = None,
    throttle_sec: float = 0.0,
    merge_window_sec: float = 2.0,
) -> None:
    global _LOG_SEQ
    now = time.monotonic()

    if key and throttle_sec > 0:
        last = _LAST_LOG_TS.get(key, 0.0)
        if (now - last) < throttle_sec:
            return
        _LAST_LOG_TS[key] = now

    ts = time.strftime("%H:%M:%S")

    with _LOG_LOCK:
        if _LIVE_LOG:
            last_item = _LIVE_LOG[-1]
            same_message = last_item.get("_raw_msg") == message
            inside_merge_window = (now - last_item.get("_mono", 0.0)) <= merge_window_sec

            if same_message and inside_merge_window:
                last_item["repeat"] += 1
                _LOG_SEQ += 1
                last_item["id"] = _LOG_SEQ
                last_item["ts"] = ts
                last_item["_mono"] = now
                last_item["msg"] = _format_message(message, last_item["repeat"])
                return

        _LOG_SEQ += 1
        _LIVE_LOG.append(
            {
                "id": _LOG_SEQ,
                "ts": ts,
                "msg": message,
                "repeat": 1,
                "_raw_msg": message,
                "_mono": now,
            }
        )


def get_live_log(after: int = 0, limit: int = 80) -> dict:
    with _LOG_LOCK:
        last_id = _LOG_SEQ
        lines = [x for x in _LIVE_LOG if x["id"] > after]
        lines = lines[-limit:]

        public_lines = [
            {
                "id": item["id"],
                "ts": item["ts"],
                "msg": item["msg"],
            }
            for item in lines
        ]

    return {"lines": public_lines, "last_id": last_id}