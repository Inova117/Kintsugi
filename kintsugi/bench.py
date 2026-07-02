"""Repair-lift benchmark = the release gate.

Replays the whole runtime over a golden set at repair budgets N=0..max and reports:
  * cumulative valid-rate as a function of repair rounds  (the "money chart")
  * the failure-category breakdown the loop had to fix     (the taxonomy in action)
  * a SHIP / BLOCK verdict against explicit thresholds

This is the thing you wire into CI so "creation quality declined after an update" becomes
a mechanical BLOCK instead of a user review. Offline it runs on the MockModel with pinned
failure sequences, so the curve is reproducible; `--real` runs it against live models.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .engine import run
from .models import MockModel, select_real_model

# Release thresholds (tune per product risk tolerance).
THRESHOLD_POST_REPAIR_VALID = 0.90
THRESHOLD_NON_CONVERGENCE = 0.03


@dataclass
class CaseOutcome:
    prompt: str
    adversarial: bool
    rounds_to_valid: Optional[int]
    first_failure: Optional[str]


@dataclass
class BenchResult:
    max_repairs: int
    cases: List[CaseOutcome]

    @property
    def n(self) -> int:
        return len(self.cases)

    def valid_at(self, rounds: int) -> float:
        ok = sum(1 for c in self.cases if c.rounds_to_valid is not None and c.rounds_to_valid <= rounds)
        return ok / self.n if self.n else 0.0

    @property
    def non_convergence_rate(self) -> float:
        bad = sum(1 for c in self.cases if c.rounds_to_valid is None)
        return bad / self.n if self.n else 0.0

    @property
    def failure_breakdown(self) -> Counter:
        return Counter(c.first_failure for c in self.cases if c.first_failure)

    @property
    def ship(self) -> bool:
        return (
            self.valid_at(self.max_repairs) >= THRESHOLD_POST_REPAIR_VALID
            and self.non_convergence_rate <= THRESHOLD_NON_CONVERGENCE
        )

    def to_json(self) -> dict:
        return {
            "n": self.n,
            "max_repairs": self.max_repairs,
            "valid_curve": {str(r): round(self.valid_at(r), 4) for r in range(self.max_repairs + 1)},
            "non_convergence_rate": round(self.non_convergence_rate, 4),
            "failure_breakdown": dict(self.failure_breakdown),
            "verdict": "SHIP" if self.ship else "BLOCK",
            "thresholds": {
                "post_repair_valid": THRESHOLD_POST_REPAIR_VALID,
                "non_convergence": THRESHOLD_NON_CONVERGENCE,
            },
            "cases": [
                {
                    "prompt": c.prompt,
                    "adversarial": c.adversarial,
                    "rounds_to_valid": c.rounds_to_valid,
                    "first_failure": c.first_failure,
                }
                for c in self.cases
            ],
        }


def load_golden(path: Path) -> list[dict]:
    return json.loads(path.read_text())["cases"]


def run_bench(
    golden_path: Path,
    *,
    mock: bool = True,
    max_repairs: int = 4,
    prefer_browser: bool = False,
    degrade: bool = False,
    out_dir: Path = Path("runs/bench"),
) -> BenchResult:
    cases_in = load_golden(golden_path)
    outcomes: List[CaseOutcome] = []
    shared_real = select_real_model() if not mock else None

    for idx, case in enumerate(cases_in):
        prompt = case["prompt"]
        # `degrade` simulates a weaker model/prompt: one extra defect per case. Offline it's
        # an injected bug; in --real mode you'd instead point KINTSUGI_*_MODEL at a cheaper tier.
        pinned = list(case.get("mock_bugs") or [])
        if degrade:
            pinned = pinned + ["dead_end"]
        model = MockModel(bugs=pinned) if mock else shared_real
        try:
            res = run(
                prompt, model,
                max_repairs=max_repairs,
                out_dir=out_dir / "runs",
                prefer_browser=prefer_browser,
                run_judge=False,  # advisory tier off in the gate — deterministic tiers own pass/fail
                emit=None,
            )
            rounds = res.rounds_to_valid
            first_fail = res.attempts[0].report.category if res.attempts else None
            first_fail = first_fail.value if first_fail else None
        except Exception as e:  # never let one case kill the batch
            rounds, first_fail = None, "PROVIDER_ERROR"
            print(f"[bench] case {idx + 1} errored: {str(e)[:120]}", flush=True)
        status = f"valid@{rounds}" if rounds is not None else "UNFIXED"
        print(f"[bench] {idx + 1}/{len(cases_in)}  {status:9} {prompt[:46]}", flush=True)
        outcomes.append(
            CaseOutcome(
                prompt=prompt,
                adversarial=bool(case.get("adversarial")),
                rounds_to_valid=rounds,
                first_failure=first_fail,
            )
        )

    result = BenchResult(max_repairs=max_repairs, cases=outcomes)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bench.json").write_text(json.dumps(result.to_json(), indent=2))
    _plot(result, out_dir / "repair_lift.png")
    return result


def _plot(result: BenchResult, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = list(range(result.max_repairs + 1))
    ys = [result.valid_at(r) * 100 for r in xs]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

    ax1.plot(xs, ys, marker="o", linewidth=2.5, color="#6366f1")
    for x, y in zip(xs, ys):
        ax1.annotate(f"{y:.0f}%", (x, y), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=9)
    ax1.axhline(THRESHOLD_POST_REPAIR_VALID * 100, ls="--", color="#ef4444", lw=1)
    ax1.text(0, THRESHOLD_POST_REPAIR_VALID * 100 + 1, "ship threshold", color="#ef4444", fontsize=8)
    ax1.set_xticks(xs)
    ax1.set_xticklabels(["first try"] + [f"+{r}" for r in xs[1:]])
    ax1.set_ylim(0, 105)
    ax1.set_ylabel("% Jams valid (all personas reachable)")
    ax1.set_xlabel("repair rounds")
    ax1.set_title("Repair lift")
    ax1.grid(alpha=0.15)

    fb = result.failure_breakdown.most_common()
    if fb:
        labels = [k for k, _ in fb]
        vals = [v for _, v in fb]
        ax2.barh(labels, vals, color="#f43f5e")
        ax2.invert_yaxis()
        ax2.set_xlabel("count (first failure per case)")
        ax2.set_title("Failure taxonomy caught")
    ax2.grid(alpha=0.15, axis="x")

    verdict = "SHIP" if result.ship else "BLOCK"
    fig.suptitle(
        f"Kintsugi release gate — {verdict}  "
        f"(post-repair valid {result.valid_at(result.max_repairs)*100:.0f}%, "
        f"non-convergence {result.non_convergence_rate*100:.0f}%)",
        fontsize=12, color=("#16a34a" if result.ship else "#dc2626"),
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=120)
    plt.close(fig)
