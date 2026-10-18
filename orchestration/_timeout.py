"""Run a callable with a wall-clock timeout by abandoning a daemon worker thread (cannot kill it —
plan amendment A6: true cancellation needs an async gateway path)."""
from __future__ import annotations

import threading
from typing import Any, Callable, Tuple


class CallTimeout(Exception):
    pass


def call_with_timeout(fn: Callable[[], Any], timeout_s: float) -> Any:
    box: dict = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 — surfaced to the caller below
            box["error"] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise CallTimeout(f"no result within {timeout_s}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")
