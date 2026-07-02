"""FastAPI split-screen demo — the live 'Heal' beat.

/api/generate runs the full loop and returns EVERY attempt (broken drafts + heals) with
its HTML and ladder. The UI renders attempt 0 (which may dead-end), you click "Heal", and
it advances to the next attempt — so the deliberate-fail-then-repair beat is a couple of
clicks with no flaky live timing.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .bench import run_bench
from .engine import run
from .models import MockModel, has_real_provider, select_real_model

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Kintsugi")


class GenerateBody(BaseModel):
    prompt: str
    real: bool = False
    bugs: list[str] | None = None  # pin the mock's failure sequence for a scripted demo


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/generate")
def api_generate(body: GenerateBody) -> JSONResponse:
    if body.real and has_real_provider():
        model = select_real_model()
    else:
        # Guarantee the scripted dead-end for the canonical demo prompt.
        bugs = body.bugs
        if bugs is None and "cursed" in body.prompt.lower():
            bugs = ["dead_end"]
        model = MockModel(bugs=bugs)

    res = run(body.prompt, model, prefer_browser=True, run_judge=True)
    return JSONResponse(
        {
            "run_id": res.run_id,
            "title": res.spec.title if res.spec else body.prompt,
            "published": res.published,
            "rounds_to_valid": res.rounds_to_valid,
            "category": res.final_category.value if res.final_category else None,
            "tokens": res.trace.total_tokens if res.trace else 0,
            "latency_ms": round(res.trace.total_latency_ms, 1) if res.trace else 0,
            "attempts": [
                {
                    "index": a.index,
                    "passed": a.report.passed,
                    "engine": a.report.engine,
                    "summary": a.report.summary(),
                    "not_reached": a.report.not_reached,
                    "tiers": [
                        {
                            "tier": t.tier,
                            "passed": t.passed,
                            "category": t.category.value if t.category else None,
                            "detail": t.detail,
                            "advisory": t.advisory,
                        }
                        for t in a.report.tiers
                    ],
                    "html": a.html,
                }
                for a in res.attempts
            ],
        }
    )


class BenchBody(BaseModel):
    degrade: bool = False


@app.post("/api/bench")
def api_bench(body: BenchBody | None = None) -> JSONResponse:
    degrade = bool(body and body.degrade)
    res = run_bench(Path("data/golden_set.json"), mock=True, degrade=degrade)
    return JSONResponse(res.to_json())


@app.get("/bench.png")
def bench_png() -> FileResponse:
    p = Path("runs/bench/repair_lift.png")
    if not p.exists():
        run_bench(Path("data/golden_set.json"), mock=True)
    return FileResponse(p)
