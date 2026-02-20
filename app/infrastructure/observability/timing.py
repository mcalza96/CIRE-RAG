from __future__ import annotations

import time


def perf_now() -> float:
    """Monotonic timer for latency measurements."""

    return time.perf_counter()


def elapsed_ms(start: float) -> float:
    """Milliseconds elapsed since `start` (from perf_now())."""

    return round((time.perf_counter() - start) * 1000, 2)
