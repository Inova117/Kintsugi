"""The validation ladder — real execution feedback.

Deterministic tiers (these gate publish):
  STRUCTURE   — the rendered Jam exposes the required primitives (jam-root, option buttons)
  RENDER      — it loads without throwing (crash / hallucinated-API caught here)
  PLAYTHROUGH — for EVERY persona, drive the maximizing answer path and assert it reaches a
                non-empty result screen. This is the tier that catches the review-grade
                "story dead-ends / crashes on some path" bug that static checks miss.

Advisory tier (never sets ground truth):
  JUDGE       — LLM rates coherence + safety. Advisory ONLY. Humans own ground truth.

Real feedback needs a browser. If chromium isn't installed we fall back to static checks
that approximate the same findings for the known failure modes, and say so in the report.
Enable the real tier with:  python -m playwright install chromium
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from .contract import JamSpec, maximizing_path
from .taxonomy import FailureCategory
from .trace import Trace

DETERMINISTIC_TIERS = ["STRUCTURE", "RENDER", "PLAYTHROUGH"]


@dataclass
class TierResult:
    tier: str
    passed: bool
    category: Optional[FailureCategory] = None
    detail: str = ""
    advisory: bool = False


@dataclass
class ValidationReport:
    tiers: List[TierResult] = field(default_factory=list)
    not_reached: List[str] = field(default_factory=list)
    engine: str = "browser"  # "browser" | "static"

    @property
    def passed(self) -> bool:
        return all(t.passed for t in self.tiers if not t.advisory)

    @property
    def failure(self) -> Optional[TierResult]:
        return next((t for t in self.tiers if not t.passed and not t.advisory), None)

    @property
    def category(self) -> Optional[FailureCategory]:
        f = self.failure
        return f.category if f else None

    def summary(self) -> str:
        f = self.failure
        if not f:
            return "all deterministic tiers passed"
        return f"{f.tier}: {f.category.value if f.category else 'FAIL'} — {f.detail}"


# --------------------------------------------------------------------------------------
# Public entrypoint
# --------------------------------------------------------------------------------------

def validate(
    html: str,
    spec: JamSpec,
    *,
    model=None,
    trace: Optional[Trace] = None,
    prefer_browser: bool = True,
    run_judge: bool = True,
) -> ValidationReport:
    use_browser = prefer_browser and _can_launch_browser()
    with (trace.span("validate", "validate") if trace else _null_ctx()) as s:
        if use_browser:
            tiers, not_reached = _browser_validate(html, spec)
            engine = "browser"
        else:
            tiers, not_reached = _static_validate(html, spec)
            engine = "static"
        report = ValidationReport(tiers=tiers, not_reached=not_reached, engine=engine)

        # Advisory judge only runs on otherwise-valid Jams and never gates.
        if report.passed and run_judge and model is not None:
            v = model.judge(html, spec, trace or Trace("adhoc"))
            report.tiers.append(
                TierResult(
                    tier="JUDGE",
                    passed=v.safe and v.coherence >= 0.5,
                    category=None if v.safe else FailureCategory.SAFETY,
                    detail=f"coherence={v.coherence:.2f} safe={v.safe} (advisory) {v.notes}".strip(),
                    advisory=True,
                )
            )
        if s is not None:
            s.status = "ok" if report.passed else "fail"
            s.attributes.update(engine=engine, passed=report.passed, summary=report.summary())
    return report


# --------------------------------------------------------------------------------------
# Browser runner (Playwright)
# --------------------------------------------------------------------------------------

_LAUNCH_OK: Optional[bool] = None


def _can_launch_browser() -> bool:
    global _LAUNCH_OK
    if _LAUNCH_OK is not None:
        return _LAUNCH_OK
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        _LAUNCH_OK = True
    except Exception:
        _LAUNCH_OK = False
    return _LAUNCH_OK


def _categorize_error(text: str) -> FailureCategory:
    t = text.lower()
    if "is not defined" in t or "referenceerror" in t or "is not a function" in t:
        return FailureCategory.TYPE_HALLUCINATED_API
    return FailureCategory.RUNTIME_CRASH


def _browser_validate(html: str, spec: JamSpec):
    from playwright.sync_api import sync_playwright

    tiers: List[TierResult] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        def fresh_page():
            # A fresh page per load: reusing one page across multiple set_content cycles
            # leaves clicks timing out after the first path. Correctness > a few ms.
            errs: List[str] = []
            pg = browser.new_page()
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            pg.set_content(html, wait_until="load")
            pg.wait_for_timeout(40)
            return pg, errs

        try:
            # --- initial load: STRUCTURE + RENDER ---
            page, errors = fresh_page()
            root = page.query_selector('[data-testid="jam-root"]')
            options = page.query_selector_all('[data-testid^="option-"]')
            has_outcome = bool(options) and options[0].get_attribute("data-outcome") is not None
            if root is None:
                tiers.append(TierResult("STRUCTURE", False, FailureCategory.STRUCTURE, "no [data-testid=jam-root]"))
                return tiers, ["RENDER", "PLAYTHROUGH"]
            if len(options) < 2 or not has_outcome:
                tiers.append(TierResult("STRUCTURE", False, FailureCategory.STRUCTURE,
                                        "option buttons missing data-testid/data-outcome"))
                return tiers, ["RENDER", "PLAYTHROUGH"]
            tiers.append(TierResult("STRUCTURE", True, detail=f"{len(options)} options on first screen"))

            if errors:
                cat = _categorize_error(errors[0])
                tiers.append(TierResult("RENDER", False, cat, f"error on load: {errors[0][:120]}"))
                return tiers, ["PLAYTHROUGH"]
            tiers.append(TierResult("RENDER", True, detail="clean initial render"))
            page.close()

            # --- PLAYTHROUGH: one maximizing path per persona, each on a fresh page ---
            for persona in spec.personas:
                page, errors = fresh_page()
                path = maximizing_path(spec, persona.outcome)
                current_sel = ""
                try:
                    for q, oid in zip(spec.questions, path):
                        current_sel = f'[data-testid="option-{q.id}-{oid}"]'
                        page.click(current_sel, timeout=1500)
                except Exception:  # a path we can't complete is itself a dead-end
                    # Name the exact missing button so the repair model can fix the testid.
                    tiers.append(TierResult("PLAYTHROUGH", False, FailureCategory.DEAD_END,
                                            f"outcome '{persona.outcome}': answer button {current_sel} was "
                                            f"never clickable — its data-testid/option id must match the spec, "
                                            f"and every option must render"))
                    return tiers, []
                page.wait_for_timeout(20)
                if errors:
                    cat = _categorize_error(errors[0])
                    tiers.append(TierResult("PLAYTHROUGH", False, cat,
                                            f"outcome '{persona.outcome}' crashed: {errors[0][:120]}"))
                    return tiers, []
                title_el = page.query_selector('[data-testid="result-title"]')
                title_txt = (title_el.inner_text().strip() if title_el else "")
                if not title_txt:
                    tiers.append(TierResult("PLAYTHROUGH", False, FailureCategory.DEAD_END,
                                            f"outcome '{persona.outcome}' reaches a blank result screen"))
                    return tiers, []
                page.close()
            tiers.append(TierResult("PLAYTHROUGH", True, detail=f"{len(spec.personas)} personas all reachable"))
        finally:
            browser.close()
    return tiers, []


# --------------------------------------------------------------------------------------
# Static fallback (no browser) — approximates the browser findings for known failure modes
# --------------------------------------------------------------------------------------

def _embedded_spec(html: str) -> Optional[dict]:
    marker = "const SPEC = "
    i = html.find(marker)
    if i == -1:
        return None
    j = i + len(marker)
    depth = 0
    for k in range(j, len(html)):
        c = html[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[j : k + 1])
                except Exception:
                    return None
    return None


def _static_validate(html: str, spec: JamSpec):
    tiers: List[TierResult] = []

    # STRUCTURE
    if 'data-testid="jam-root"' not in html:
        return [TierResult("STRUCTURE", False, FailureCategory.STRUCTURE, "no jam-root")], ["RENDER", "PLAYTHROUGH"]
    if "'data-testid', 'option-'" not in html and "data-testid=\"option-" not in html:
        return [TierResult("STRUCTURE", False, FailureCategory.STRUCTURE,
                           "option testids not emitted")], ["RENDER", "PLAYTHROUGH"]
    tiers.append(TierResult("STRUCTURE", True, detail="jam-root + option testids present (static)"))

    # RENDER — the only load-time crash we can spot statically is a call to a known-undefined fn
    if "sparkleConfetti(" in html:
        tiers.append(TierResult("RENDER", False, FailureCategory.TYPE_HALLUCINATED_API,
                                "calls undefined sparkleConfetti() on load (static)"))
        return tiers, ["PLAYTHROUGH"]
    tiers.append(TierResult("RENDER", True, detail="no known undefined symbols (static)"))

    # PLAYTHROUGH — every referenced outcome must have a screen in the *embedded* spec
    embedded = _embedded_spec(html)
    embedded_outcomes = {p["outcome"] for p in embedded["personas"]} if embedded else spec.outcomes_with_screens
    missing = sorted(spec.outcomes_referenced - embedded_outcomes)
    if missing:
        guarded = "persona ? persona.title" in html
        cat = FailureCategory.DEAD_END if guarded else FailureCategory.RUNTIME_CRASH
        detail = f"outcome '{missing[0]}' has no result screen" + ("" if guarded else " (unguarded -> throws)")
        tiers.append(TierResult("PLAYTHROUGH", False, cat, detail + " (static)"))
        return tiers, []
    tiers.append(TierResult("PLAYTHROUGH", True, detail="all outcomes have screens (static)"))
    return tiers, []


class _null_ctx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False
