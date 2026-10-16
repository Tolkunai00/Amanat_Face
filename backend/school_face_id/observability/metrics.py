"""Thread-safe counters and latency aggregates without a heavy dependency."""

from __future__ import annotations

import threading


_lock = threading.Lock()
_counters: dict[str, int] = {}
_latencies: dict[str, dict[str, float]] = {}


def increment(name: str, amount: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + int(amount)


def observe(name: str, value_ms: float | None) -> None:
    if value_ms is None:
        return
    value = max(0.0, float(value_ms))
    with _lock:
        current = _latencies.setdefault(name, {"count": 0, "sum": 0.0, "last": 0.0})
        current["count"] += 1
        current["sum"] += value
        current["last"] = value


def snapshot() -> tuple[dict[str, int], dict[str, dict[str, float]]]:
    with _lock:
        return dict(_counters), {name: dict(values) for name, values in _latencies.items()}
