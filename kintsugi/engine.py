"""The runtime loop: prompt -> plan -> generate -> run/validate -> repair -> publish.

Deliberately a plain, inspectable state machine rather than a framework graph. It maps
1:1 onto LangGraph nodes for production (durable checkpointing, human-in-the-loop pause on
publish) — see docs/langgraph_port.md — but a scaffold you can read top-to-bottom beats a
graph you have to trust.

Two safety properties worth saying out loud in the interview:
  * bounded repair (`max_repairs`) — non-converging repair loops are a real failure mode,
    so termination is a correctness guarantee, not just a cost control.
  * always publish the last VALID artifact — we never ship a degraded Jam over a good one.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from pydantic import ValidationError

from .contract import JamSpec
from .taxonomy import FailureCategory
from .trace import Trace
from .validate import ValidationReport, validate

Emit = Callable[[dict], None]


@dataclass
class Attempt:
    index: int  # 0 = first draft, 1.. = repair rounds
    html: str
    report: ValidationReport


@dataclass
class RunResult:
    run_id: str
    prompt: str
    spec: Optional[JamSpec]
    attempts: List[Attempt] = field(default_factory=list)
    published: bool = False
    published_path: Optional[Path] = None
    final_category: Optional[FailureCategory] = None
    trace: Optional[Trace] = None

    @property
    def rounds_to_valid(self) -> Optional[int]:
        for a in self.attempts:
            if a.report.passed:
                return a.index
        return None

    @property
    def healed(self) -> bool:
        return self.published and (self.rounds_to_valid or 0) > 0

    @property
    def final_html(self) -> Optional[str]:
        for a in reversed(self.attempts):
            if a.report.passed:
                return a.html
        return self.attempts[-1].html if self.attempts else None


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _report_event(index: int, report: ValidationReport) -> dict:
    return {
        "type": "attempt",
        "index": index,
        "passed": report.passed,
        "engine": report.engine,
        "summary": report.summary(),
        "not_reached": report.not_reached,
        "tiers": [
            {
                "tier": t.tier,
                "passed": t.passed,
                "category": t.category.value if t.category else None,
                "detail": t.detail,
                "advisory": t.advisory,
            }
            for t in report.tiers
        ],
    }


def run(
    prompt: str,
    model,
    *,
    max_repairs: int = 4,
    out_dir: Path = Path("runs"),
    run_id: Optional[str] = None,
    prefer_browser: bool = True,
    run_judge: bool = True,
    emit: Optional[Emit] = None,
) -> RunResult:
    run_id = run_id or f"{int(time.time())}-{_short_hash(prompt)}"
    trace = Trace(run_id)
    _emit = emit or (lambda e: None)
    result = RunResult(run_id=run_id, prompt=prompt, spec=None, trace=trace)

    def _validate(html: str) -> ValidationReport:
        return validate(html, spec, model=model, trace=trace,
                        prefer_browser=prefer_browser, run_judge=run_judge)

    def done_event(published: bool, rounds=None, path=None, category=None) -> dict:
        return {
            "type": "done", "published": published, "rounds": rounds, "path": path,
            "category": category,
            "tokens": trace.total_tokens, "latency_ms": round(trace.total_latency_ms, 1),
        }

    # --- plan (repairs itself: feed the contract error back so the model fixes the spec) ---
    spec: Optional[JamSpec] = None
    plan_prompt = prompt
    for plan_try in range(3):
        try:
            spec = model.plan(plan_prompt, trace)
            break
        except (ValidationError, ValueError) as e:
            _emit({"type": "plan_error", "attempt": plan_try, "error": str(e)[:300]})
            plan_prompt = (
                f"{prompt}\n\nYour previous spec was REJECTED: {str(e)[:300]}\n"
                "EVERY outcome any option scores toward MUST have a matching persona. "
                "Return corrected JSON."
            )
    else:  # never broke out -> all attempts failed the contract
        result.final_category = FailureCategory.SCHEMA_CONTRACT
        _emit(done_event(False, category=FailureCategory.SCHEMA_CONTRACT.value))
        return result
    result.spec = spec
    _emit({"type": "plan", "title": spec.title, "theme": spec.theme,
           "outcomes": sorted(spec.outcomes_with_screens)})

    # --- generate + first validation ---
    html = model.generate(spec, trace)
    report = _validate(html)
    result.attempts.append(Attempt(0, html, report))
    _emit(_report_event(0, report))

    # --- repair loop (bounded) ---
    i = 0
    while not report.passed and i < max_repairs:
        i += 1
        _emit({"type": "repair", "attempt": i, "resolving": report.summary(),
               "category": report.category.value if report.category else None})
        html = model.repair(html, spec, report, trace, attempt=i)
        report = _validate(html)
        result.attempts.append(Attempt(i, html, report))
        _emit(_report_event(i, report))

    # --- publish (or record non-convergence) ---
    if result.rounds_to_valid is not None:
        path = _publish(result.final_html, out_dir / run_id)
        result.published = True
        result.published_path = path
        result.final_category = None
    else:
        result.final_category = FailureCategory.NON_CONVERGENCE
        with trace.span("non_convergence", "publish") as s:
            s.status = "fail"
            s.attributes["reason"] = f"still failing after {max_repairs} repairs"

    trace.save(out_dir / run_id)
    _emit(done_event(
        result.published,
        rounds=result.rounds_to_valid,
        path=str(result.published_path) if result.published_path else None,
        category=result.final_category.value if result.final_category else None,
    ))
    return result


def _publish(html: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "jam.html"
    path.write_text(html)
    return path
