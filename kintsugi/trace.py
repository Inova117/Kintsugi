"""Minimal tracing.

MVP cut of the full plan: instead of OpenTelemetry GenAI spans -> Langfuse, we append
structured span events to a list and dump them to runs/<id>/trace.json. The span shape
(name, kind, model, tokens, latency, status, attributes) is deliberately OTel-GenAI-ish
so the production swap is a serializer change, not a re-architecture.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Span:
    name: str
    kind: str  # "plan" | "generate" | "validate" | "tier" | "repair" | "publish" | "judge"
    status: str = "ok"  # "ok" | "fail"
    model: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    attributes: Dict[str, Any] = field(default_factory=dict)


class Trace:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.spans: List[Span] = []
        self._t0 = time.perf_counter()

    def span(self, name: str, kind: str, **attrs: Any) -> "_SpanCtx":
        return _SpanCtx(self, name, kind, attrs)

    def add(self, span: Span) -> None:
        self.spans.append(span)

    # --- cheap rollups the CLI / bench read ---
    @property
    def total_tokens(self) -> int:
        return sum(s.tokens_in + s.tokens_out for s in self.spans)

    @property
    def total_latency_ms(self) -> float:
        return sum(s.latency_ms for s in self.spans)

    def cost_usd(self, price_per_mtok: float = 3.0) -> float:
        # Rough single-rate estimate; real routing would price per-model.
        return self.total_tokens / 1_000_000 * price_per_mtok

    def to_json(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_tokens": self.total_tokens,
            "total_latency_ms": round(self.total_latency_ms, 1),
            "spans": [asdict(s) for s in self.spans],
        }

    def save(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "trace.json"
        path.write_text(json.dumps(self.to_json(), indent=2))
        return path


class _SpanCtx:
    """`with trace.span(...) as s: s.model=...; s.tokens_out=...` — records latency on exit."""

    def __init__(self, trace: Trace, name: str, kind: str, attrs: Dict[str, Any]) -> None:
        self._trace = trace
        self._span = Span(name=name, kind=kind, attributes=dict(attrs))
        self._start = 0.0

    def __enter__(self) -> Span:
        self._start = time.perf_counter()
        return self._span

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._span.latency_ms = (time.perf_counter() - self._start) * 1000.0
        if exc_type is not None and self._span.status == "ok":
            self._span.status = "fail"
            self._span.attributes["error"] = repr(exc)
        self._trace.add(self._span)
        return False  # never swallow exceptions
