from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass
class _RetrievalBackendMetrics:
    hybrid_rpc_hits: int = 0
    hybrid_rpc_fallbacks: int = 0
    hybrid_rpc_disabled: int = 0


class RetrievalMetricsStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._metrics = _RetrievalBackendMetrics()

    def record_hybrid_rpc_hit(self) -> None:
        with self._lock:
            self._metrics.hybrid_rpc_hits += 1

    def record_hybrid_rpc_fallback(self) -> None:
        with self._lock:
            self._metrics.hybrid_rpc_fallbacks += 1

    def record_hybrid_rpc_disabled(self) -> None:
        with self._lock:
            self._metrics.hybrid_rpc_disabled += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total = self._metrics.hybrid_rpc_hits + self._metrics.hybrid_rpc_fallbacks
            hit_ratio = round(self._metrics.hybrid_rpc_hits / total, 4) if total > 0 else 0.0
            return {
                "hybrid_rpc_hits": self._metrics.hybrid_rpc_hits,
                "hybrid_rpc_fallbacks": self._metrics.hybrid_rpc_fallbacks,
                "hybrid_rpc_disabled": self._metrics.hybrid_rpc_disabled,
                "hybrid_rpc_hit_ratio": hit_ratio,
            }


retrieval_metrics_store = RetrievalMetricsStore()
