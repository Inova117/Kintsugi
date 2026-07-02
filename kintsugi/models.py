"""Model layer.

Two implementations behind one interface:

  * MockModel      — deterministic, offline, no API key. Simulates a model that emits
                     buggy Jams and fixes them on execution feedback. This is what makes
                     the whole runtime + the "Heal" beat runnable and testable tonight.
  * AnthropicModel — the real thing. Implements the model-routing story: cheap Haiku for
                     plan/first-draft, strong Sonnet for repair (escalate on the hard tail).

The engine only knows the `Model` protocol, so swapping mock <-> real is a one-line change.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Protocol

from .contract import JamSpec
from .render import render_html
from .trace import Trace

if TYPE_CHECKING:
    from .validate import ValidationReport


@dataclass
class JudgeVerdict:
    coherence: float  # 0..1
    safe: bool
    notes: str = ""


class Model(Protocol):
    name: str

    def plan(self, prompt: str, trace: Trace) -> JamSpec: ...
    def generate(self, spec: JamSpec, trace: Trace) -> str: ...
    def repair(self, html: str, spec: JamSpec, report: "ValidationReport", trace: Trace, attempt: int) -> str: ...
    def judge(self, html: str, spec: JamSpec, trace: Trace) -> JudgeVerdict: ...


# --------------------------------------------------------------------------------------
# Mock model
# --------------------------------------------------------------------------------------

_ARCHETYPES = [
    ("spark", "The Spark", "You are pure kinetic energy — the one who starts the fire and dances by it."),
    ("anchor", "The Anchor", "Steady, grounded, the person everyone drifts back to when the night gets loud."),
    ("wave", "The Wave", "You go where the feeling goes — fluid, intuitive, impossible to pin down."),
    ("flame", "The Flame", "Intense and all-in. When you love something, the whole room can feel the heat."),
]

# Bug plans the mock burns down across attempts. Deterministic by prompt hash unless the
# caller pins them (the golden set does, so the benchmark curve is reproducible).
_BUG_TABLE: List[List[str]] = [
    [],                          # perfect first try
    [],
    ["dead_end"],                # one heal
    ["hallucinated_api"],        # one heal (render-tier)
    ["dead_end"],
    ["structure", "dead_end"],   # two heals
    ["crash_on_result"],
    ["hallucinated_api", "dead_end", "structure", "crash_on_result", "dead_end"],  # non-converger (>4)
]


class MockModel:
    name = "mock"

    def __init__(self, bugs: Optional[List[str]] = None) -> None:
        # `bugs`: explicit sequence to inject (golden set pins these). None => derive from prompt.
        self._pinned = list(bugs) if bugs is not None else None
        self._remaining: List[str] = []

    def _bug_plan(self, prompt: str) -> List[str]:
        if self._pinned is not None:
            return list(self._pinned)
        # Canonical demo convenience: "the cursed quiz" reliably shows the single dead-end heal.
        if "cursed" in prompt.lower():
            return ["dead_end"]
        h = int(hashlib.sha256(prompt.encode()).hexdigest(), 16)
        return list(_BUG_TABLE[h % len(_BUG_TABLE)])

    def _template_spec(self, prompt: str) -> JamSpec:
        low = prompt.lower()
        theme = (
            "festival" if any(w in low for w in ("festival", "coachella", "headliner", "concert"))
            else "cyber" if any(w in low for w in ("cyber", "ai", "robot", "future"))
            else "cozy" if any(w in low for w in ("cozy", "coffee", "autumn", "cottage"))
            else "default"
        )
        title = prompt.strip().rstrip("?") or "Which one are you?"
        if len(title) > 60:
            title = title[:57] + "..."
        questions = []
        prompts = [
            "It's the first hour. Where are you?",
            "Someone hands you the aux. You play...",
            "The plan falls apart. Your move?",
            "How do people describe your energy?",
            "The night is ending. You...",
        ]
        for qi, qp in enumerate(prompts):
            options = [
                {"id": f"o{qi}{ai}", "label": _opt_label(qi, ai), "outcome": arch[0]}
                for ai, arch in enumerate(_ARCHETYPES)
            ]
            questions.append({"id": f"q{qi}", "prompt": qp, "options": options})
        personas = [
            {"outcome": oc, "title": t, "description": d} for (oc, t, d) in _ARCHETYPES
        ]
        return JamSpec.model_validate(
            {"title": title, "theme": theme, "questions": questions, "personas": personas}
        )

    def plan(self, prompt: str, trace: Trace) -> JamSpec:
        with trace.span("plan", "plan", prompt=prompt) as s:
            s.model = "mock-haiku"
            s.tokens_in, s.tokens_out = 220, 180
            spec = self._template_spec(prompt)
            self._remaining = self._bug_plan(prompt)
            s.attributes["outcomes"] = sorted(spec.outcomes_with_screens)
        return spec

    def generate(self, spec: JamSpec, trace: Trace) -> str:
        with trace.span("generate", "generate") as s:
            s.model = "mock-haiku"
            s.tokens_in, s.tokens_out = 300, 1400
            bugs = self._remaining[:1]
            s.attributes["injected_bugs"] = bugs
            return render_html(spec, bugs=bugs)

    def repair(self, html: str, spec: JamSpec, report: "ValidationReport", trace: Trace, attempt: int) -> str:
        with trace.span("repair", "repair", attempt=attempt) as s:
            # Routing: escalate to the strong model once the cheap draft has failed.
            s.model = "mock-sonnet" if attempt >= 1 else "mock-haiku"
            s.tokens_in, s.tokens_out = 900, 700
            if self._remaining:
                s.attributes["resolved"] = self._remaining.pop(0)
            bugs = self._remaining[:1]
            s.attributes["remaining_bugs"] = bugs
            return render_html(spec, bugs=bugs)

    def judge(self, html: str, spec: JamSpec, trace: Trace) -> JudgeVerdict:
        with trace.span("judge", "judge") as s:
            s.model = "mock-haiku"
            s.tokens_in, s.tokens_out = 500, 60
            return JudgeVerdict(coherence=0.9, safe=True, notes="mock: assumed coherent + SFW")


def _opt_label(qi: int, ai: int) -> str:
    banks = [
        ["Front and center, already moving", "Somewhere with a good view", "Wherever the crowd pulls me", "Right up against the speakers"],
        ["Something loud and new", "A song everyone knows", "Whatever fits the mood", "My favorite, no apologies"],
        ["Improvise something better", "Keep everyone calm", "Follow the vibe", "Double down harder"],
        ["Electric", "Reassuring", "Unpredictable", "Intense"],
        ["Keep it going somewhere else", "Head home content", "Drift wherever", "Burn til sunrise"],
    ]
    return banks[qi % len(banks)][ai]


# --------------------------------------------------------------------------------------
# Real models
# --------------------------------------------------------------------------------------
#
# Both providers share one base (_ChatModel). A provider only implements `_call` (how it
# talks to its API) + names its four routed models. The plan->generate->repair->judge
# prompting logic is identical, so routing/repair behaviour is provider-agnostic.

_JAM_HTML_CONTRACT = """\
Emit ONE self-contained HTML document (no external requests, no CDN) implementing this quiz.
The automated validator enforces ALL of these — a Jam that misses any is rejected:
  - a root element with data-testid="jam-root"
  - each answer is a <button> with data-testid="option-<questionId>-<optionId>" AND data-outcome="<outcome>"
  - after the last question, a screen with data-testid="result-screen" containing
    data-testid="result-title" whose text is NON-EMPTY
  - EVERY outcome any option can score toward MUST have a reachable, non-empty result screen
    (a missing branch = a dead-end = rejected)
Score by counting chosen outcomes; the most-chosen outcome picks the result screen.
Return ONLY the raw HTML — no prose, no markdown fences.

Skeleton to follow (fill in from the spec):
<!doctype html><html><body>
<main data-testid="jam-root"></main>
<script>
const SPEC = /* the spec as JSON */;
/* render questions; each option button has data-testid="option-<qid>-<oid>" + data-outcome;
   on the final answer, compute the winning outcome, find its persona, and render
   <section data-testid="result-screen"><h1 data-testid="result-title">...</h1></section>;
   GUARD the persona lookup so a missing one never throws and never renders blank. */
</script></body></html>
"""

_PLAN_SYSTEM = (
    "You design personality-quiz specs. Respond with JSON only, matching: "
    '{"title": str, "theme": str, "questions":[{"id":str,"prompt":str,'
    '"options":[{"id":str,"label":str,"outcome":str}]}], '
    '"personas":[{"outcome":str,"title":str,"description":str}]}. '
    "Use >=3 questions, >=2 options each, and EVERY outcome referenced by an option MUST "
    "have a matching persona (else a player dead-ends). Keep ids short like q0/o00."
)

_JUDGE_SYSTEM = (
    "Rate this quiz. Respond with JSON only: "
    '{"coherence": number 0..1, "safe": true/false, "notes": string}. '
    "coherence = do the questions and results hang together and feel fun; safe = SFW. "
    "You are ADVISORY only — you do not decide pass/fail."
)


class _ChatModel:
    """Shared plan/generate/repair/judge logic. Subclasses provide `_call` + model names."""

    name = "chat"
    _plan_model = _draft_model = _repair_model = _judge_model = ""

    def _call(self, model: str, system: str, user: str, span, max_tokens: int = 4096,
              json_mode: bool = False) -> str:
        raise NotImplementedError

    def plan(self, prompt: str, trace: Trace) -> JamSpec:
        with trace.span("plan", "plan", prompt=prompt) as s:
            raw = self._call(self._plan_model, _PLAN_SYSTEM, prompt, s, max_tokens=2048, json_mode=True)
            spec = JamSpec.model_validate(_extract_json(raw))  # raises -> engine repairs the spec
            s.attributes["outcomes"] = sorted(spec.outcomes_with_screens)
        return spec

    def generate(self, spec: JamSpec, trace: Trace) -> str:
        system = "You are a senior frontend engineer. " + _JAM_HTML_CONTRACT
        user = "Build this quiz as HTML:\n" + spec.model_dump_json(indent=2)
        with trace.span("generate", "generate") as s:
            return _extract_html(self._call(self._draft_model, system, user, s))

    def repair(self, html: str, spec: JamSpec, report: "ValidationReport", trace: Trace, attempt: int) -> str:
        system = "You repair a broken quiz. Change the minimum needed to pass. " + _JAM_HTML_CONTRACT
        user = (
            f"The quiz failed validation.\nFAILURE:\n{report.summary()}\n\n"
            f"SPEC:\n{spec.model_dump_json()}\n\nCURRENT HTML:\n{html}\n\n"
            "Return the corrected full HTML."
        )
        with trace.span("repair", "repair", attempt=attempt) as s:
            model = self._repair_model if attempt >= 1 else self._draft_model  # escalate on the tail
            return _extract_html(self._call(model, system, user, s, max_tokens=4096))

    def judge(self, html: str, spec: JamSpec, trace: Trace) -> JudgeVerdict:
        with trace.span("judge", "judge") as s:
            raw = self._call(self._judge_model, _JUDGE_SYSTEM, "SPEC:\n" + spec.model_dump_json(),
                             s, max_tokens=512, json_mode=True)
            try:
                data = _extract_json(raw)
            except ValueError:
                data = {}
        return JudgeVerdict(
            coherence=float(data.get("coherence", 0.0)),
            safe=bool(data.get("safe", True)),
            notes=str(data.get("notes", "")),
        )


class AnthropicModel(_ChatModel):
    """Anthropic provider: cheap Haiku draft/plan, strong Sonnet repair."""

    name = "anthropic"

    def __init__(self) -> None:
        import anthropic  # lazy so mock/groq modes need no dependency

        self._client = anthropic.Anthropic()
        self._plan_model = os.environ.get("KINTSUGI_PLAN_MODEL", "claude-haiku-4-5-20251001")
        self._draft_model = os.environ.get("KINTSUGI_DRAFT_MODEL", "claude-haiku-4-5-20251001")
        self._repair_model = os.environ.get("KINTSUGI_REPAIR_MODEL", "claude-sonnet-5")
        self._judge_model = os.environ.get("KINTSUGI_JUDGE_MODEL", "claude-haiku-4-5-20251001")

    def _call(self, model, system, user, span, max_tokens=4096, json_mode=False) -> str:
        resp = self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        span.model = model
        span.tokens_in = resp.usage.input_tokens
        span.tokens_out = resp.usage.output_tokens
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


class GroqModel(_ChatModel):
    """Groq provider (free, OpenAI-compatible): fast small draft/plan, 70B repair."""

    name = "groq"

    def __init__(self) -> None:
        from groq import Groq  # lazy

        self._client = Groq()  # reads GROQ_API_KEY from env
        self._plan_model = os.environ.get("KINTSUGI_GROQ_PLAN_MODEL", "llama-3.1-8b-instant")
        self._draft_model = os.environ.get("KINTSUGI_GROQ_DRAFT_MODEL", "llama-3.3-70b-versatile")
        self._repair_model = os.environ.get("KINTSUGI_GROQ_REPAIR_MODEL", "llama-3.3-70b-versatile")
        self._judge_model = os.environ.get("KINTSUGI_GROQ_JUDGE_MODEL", "llama-3.1-8b-instant")

    def _call(self, model, system, user, span, max_tokens=4096, json_mode=False) -> str:
        kwargs = dict(
            model=model, max_tokens=max_tokens, temperature=0.4,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._retrying_create(**kwargs)
        span.model = model
        usage = getattr(resp, "usage", None)
        span.tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        span.tokens_out = getattr(usage, "completion_tokens", 0) or 0
        return resp.choices[0].message.content or ""

    def _retrying_create(self, **kwargs):
        """Retry rate-limit / transient errors with exponential backoff (free tier throttles)."""
        transient = ("RateLimit", "Timeout", "Connection", "InternalServer", "APIStatus", "ServiceUnavailable")
        last = None
        for i in range(6):
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as e:  # noqa: BLE001 — only retry known-transient classes; re-raise the rest
                if not any(k in type(e).__name__ for k in transient):
                    raise
                last = e
                time.sleep(min(2 ** i, 20))
        raise last or RuntimeError("groq call failed after retries")  # exhausted retries


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def _extract_html(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    return text


def has_real_provider() -> bool:
    return bool(os.environ.get("GROQ_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def select_real_model() -> Model:
    """Pick the real provider from whichever API key is present (Groq wins if both)."""
    if os.environ.get("GROQ_API_KEY"):
        return GroqModel()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicModel()
    raise RuntimeError("no real provider: set GROQ_API_KEY or ANTHROPIC_API_KEY (see .env.example)")


def build_model(mock: bool) -> Model:
    return MockModel() if mock else select_real_model()
