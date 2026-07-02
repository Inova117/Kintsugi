# Kintsugi — a self-healing Jam-generation runtime

> Built as an interview demo for the **Sekai** agent-harness / app-generation role.
> *Kintsugi (金継ぎ): the art of repairing something broken so the seams become the point.*

Sekai is "TikTok for mini apps," and its most common negative reviews are about **crashes,
failed generations, and stories that dead-end**. This is a small, runnable runtime that
turns that exact pain into a measured system:

**prompt → plan → generate → run/validate → repair → publish**, wrapped in an eval loop
that *gates the ship*.

It generates a playable personality-quiz "Jam" from one prompt, reproduces the dead-end
bug **live** (an answer path that ends on a blank result screen), **heals it in one repair
round using real browser-execution feedback** — failure category and fix visible the whole
time — then proves it isn't a fluke with a repair-lift benchmark wired to a **SHIP / BLOCK**
release gate.

It runs **end-to-end with no API key** (a deterministic mock model simulates a model that
makes realistic mistakes and fixes them), so you can try everything below in two minutes.

---

## Why this, for this role

Sekai says they're *"doubling down on creation quality and reliability"* and ask three
questions: how do we generate autonomous software **fast**, and **tastefully**? Kintsugi
puts the reliability + eval half of that on stage, and has an honest answer for taste.

| "You will own" (JD) | Where it lives here |
|---|---|
| Own the **agent harness layer** | [`engine.py`](kintsugi/engine.py) — the plan→generate→validate→repair→publish state machine |
| **Long-horizon agentic workflow, max autonomy** | the bounded repair loop with reflection + model escalation |
| **Eval + quality loops, regression, failure taxonomy** | [`validate.py`](kintsugi/validate.py), [`taxonomy.py`](kintsugi/taxonomy.py), [`bench.py`](kintsugi/bench.py) |
| **Model strategy: routing, cost/latency** | cheap-draft → strong-repair routing in [`models.py`](kintsugi/models.py) (Groq 8B→70B or Anthropic Haiku→Sonnet); tokens/latency per span |
| **Debuggable systems: tracing, observability** | [`trace.py`](kintsugi/trace.py) — OTel-GenAI-shaped spans → `runs/<id>/trace.json` |
| **Research-and-ship** | the benchmark *is* the experiment harness; thresholds gate production |
| Bonus: *"built a code-generation pipeline — this is what we do"* | that's literally what this is |

**On "tastefully":** the deterministic tiers only prove a Jam *works*. Whether it's *good*
is the `JUDGE` tier — an LLM rating coherence + safety, kept **advisory only** so judge bias
can never inflate the headline number. Humans own ground truth. Promoting taste to a tracked
(still un-gated) metric with a human-agreement harness is the first roadmap item.

**On frameworks:** this is a hand-rolled state machine on purpose — a scaffold you can read
top-to-bottom beats a graph you have to trust. The Jam contract is **Pydantic**, so
[**PydanticAI**](https://ai.pydantic.dev) is the natural typed-agent port; **LangGraph** is
the path once you need durable checkpointing + human-in-the-loop pause on publish. See
[docs/langgraph_port.md](docs/langgraph_port.md). Knowing when the framework gets in your
way is exactly why the core stays framework-free.

---

## Quickstart (no API key needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
python -m playwright install chromium        # enables the real-execution tier

# 1) watch a broken Jam get healed (real browser validation)
kintsugi run "the cursed quiz — which chaos gremlin are you?"

# 2) the live split-screen demo: generate, click "Heal", run the release gate
kintsugi serve            # -> http://127.0.0.1:8000

# 3) the release gate + repair-lift chart
kintsugi bench            # baseline -> SHIP
kintsugi bench --degrade  # simulate a weaker model -> BLOCK
```

No browser installed? Everything still runs via a static-analysis fallback
(`kintsugi run ... --no-browser`); it approximates the same findings and says so.

### Run it for real with a free Groq key

By default everything uses a built-in **mock** model — no key, no cost, fully offline. To
generate with a real LLM, Kintsugi supports **Groq (free)** and Anthropic. Groq is the easy
path. Three steps:

1. **Get a free key** at <https://console.groq.com/keys> (it looks like `gsk_...`).
2. **Put it in a `.env` file.** From the project folder:
   ```bash
   cp .env.example .env
   ```
   Open `.env`, and set just this one line (leave everything else as-is):
   ```
   GROQ_API_KEY=gsk_your_key_here
   ```
3. **Add `--real`** to any command:
   ```bash
   kintsugi run "What kind of cursed houseplant are you?" --real
   ```
   In the web UI (`kintsugi serve`), tick the **"use real model (Groq)"** box before Generate.

That's the whole thing. `.env` is gitignored, so **your key is never committed**. If you see
`--real needs GROQ_API_KEY`, the key just isn't in `.env` yet.

> **Want to watch the repair loop with a real model?** The strong default model usually gets
> it right on the first try. To force more (real) mistakes for it to heal, set
> `KINTSUGI_GROQ_DRAFT_MODEL=llama-3.1-8b-instant` in `.env` — the weaker model slips up and
> the 70B repair model fixes it.

> **Security:** never paste a real key anywhere public, and rotate it if you do. The key lives
> only in `.env` on your machine.

---

## What you actually see

`kintsugi run "the cursed quiz ..."`:

```
  first draft  ·  ladder (browser)  →  INVALID
    ✓ STRUCTURE    4 options on first screen
    ✓ RENDER       clean initial render
    ✗ PLAYTHROUGH  DEAD_END — outcome 'flame' reaches a blank result screen
  🔧 repairing: PLAYTHROUGH: DEAD_END ...  ↑ escalating to strong model
  repair round 1  ·  ladder (browser)  →  VALID
    ✓ STRUCTURE  ✓ RENDER  ✓ PLAYTHROUGH  ✓ JUDGE (advisory)
✅ healed and published in 1 repair round(s)
```

`kintsugi bench` writes `runs/bench/repair_lift.png` — cumulative % valid vs repair rounds
(25 → 62 → 88 → 94 → 100%) plus the failure-taxonomy breakdown and a SHIP/BLOCK verdict.

---

## The 90-second demo script

1. **(0:10)** Interviewer picks *"the cursed quiz."* Plan streams, quiz renders instantly.
2. **(0:25) the deliberate fail** — they click the mostly-`flame` path; it ends on a blank
   result screen: the exact "story dead-ends" review complaint, reproduced live.
3. **(0:45) the heal** — hit **Heal**. `PLAYTHROUGH: DEAD_END — outcome 'flame' reaches a
   blank result screen`; the agent patches, the ladder re-runs all-green, the same path now
   lands on a real persona. Broken → healed with the failure category on screen.
4. **(1:05) not a fluke** — flip to the benchmark: the repair-lift curve + taxonomy.
5. **(1:20) the gate** — click **Simulate weaker model**. Same thresholds, `SHIP → BLOCK`
   (non-convergence jumps past 3%). *"Your harness just blocked a creation-quality
   regression before it hit the feed."*

## Five talking points (say these while demoing)

1. **Routing economics.** First draft is Haiku; I only escalate to Sonnet on the tail that
   fails validation — at 200k Jams/day that's paying the strong model on 1 in 10, not all 10.
2. **Reviews → metrics.** "Crashes / dead-ends" aren't one bug; they're `DEAD_END`,
   `RUNTIME_CRASH`, `SCHEMA_CONTRACT`… Most are spec/verification failures (MAST), so they're
   *catchable*, not just hopeable.
3. **Gating answers "quality declined after an update."** Every model/prompt/router change
   runs the golden gate with a hard threshold before it reaches the feed → mechanical BLOCK.
4. **The repair cap is a correctness property.** Bounded repair + always-publish-last-valid:
   non-converging loops are a real failure mode, so termination is a guarantee, not just cost.
5. **Constraint as reliability.** The typed Jam contract + `data-testid` primitives make the
   smoke test deterministic and the output diffable/forkable — the senior tradeoff. Judge is
   advisory; humans own ground truth.

---

## Architecture

```
prompt
  │  plan       model → JamSpec           (Pydantic contract: every outcome needs a screen)
  ▼
JamSpec
  │  generate   model → self-contained HTML quiz (data-testid primitives)
  ▼
HTML ──► validate (the ladder) ──────────────────────────────────────────┐
  ▲        STRUCTURE → RENDER → PLAYTHROUGH   (deterministic, gate)        │
  │        JUDGE                              (LLM, advisory only)         │
  │                                                                        │
  └── repair (targeted patch, tag failure, escalate model)  ◄── fail ──────┘
             bounded by max_repairs; always keep last valid
  │
  ▼  publish   content-addressed HTML + trace.json
```

- **The dead-end invariant** ([`contract.py`](kintsugi/contract.py)): a Jam is only valid if
  *every* scoreable outcome has a reachable, non-empty result screen. That single Pydantic
  rule is the review complaint, made machine-checkable.
- **Real execution feedback** ([`validate.py`](kintsugi/validate.py)): `PLAYTHROUGH` drives
  one maximizing answer path **per persona** in a headless browser and asserts each reaches a
  non-empty result — catching runtime dead-ends static checks miss.
- **The release gate** ([`bench.py`](kintsugi/bench.py)): replays the loop over a golden set
  (half deliberately adversarial) at N=0..4 repairs; ships only if post-repair valid ≥ 90%
  and non-convergence ≤ 3%.

## Repo layout

```
kintsugi/
  contract.py   Pydantic Jam spec + the dead-end invariant + scoring
  taxonomy.py   8-category failure taxonomy (MAST-framed)
  render.py     spec → self-contained HTML; injects realistic bugs for the mock
  models.py     Model protocol · MockModel (offline) · AnthropicModel (Haiku→Sonnet routing)
  validate.py   the ladder: STRUCTURE→RENDER→PLAYTHROUGH (browser) + JUDGE (advisory) + static fallback
  engine.py     the plan→generate→validate→repair→publish loop
  trace.py      OTel-GenAI-shaped spans → runs/<id>/trace.json
  bench.py      golden-set replay, repair-lift chart, SHIP/BLOCK gate
  cli.py        `kintsugi run | bench | serve`
  web.py        FastAPI split-screen Heal demo
  static/       the demo UI (self-contained)
data/golden_set.json   regression prompts (mock failure sequences pinned for reproducibility)
tests/                 contract, ladder (static + browser), engine
docs/langgraph_port.md how the state machine maps to LangGraph / PydanticAI
```

## Tests

```bash
pytest            # 15 tests; the browser test auto-skips if chromium isn't installed
```

## What's real vs. mocked

| Real today | Mocked for the offline scaffold |
|---|---|
| The full loop, ladder, taxonomy, routing logic, tracing, gate | Model calls (deterministic `MockModel`) — swap with `--real` + a free `GROQ_API_KEY` (or `ANTHROPIC_API_KEY`) |
| Browser execution feedback (Playwright/chromium) | — |
| Repair-lift chart + SHIP/BLOCK | The mock's failure *sequences* are pinned per golden case for a reproducible curve |

## Roadmap (the "if I had two weeks" answer)

- Promote **taste** to a tracked metric: LLM-judge coherence + a human-labeled agreement harness.
- **Remix** as a first-class primitive: fork a Jam, mutate the spec, re-validate against the
  contract, track lineage (`parent_id`) + a semantic diff — Sekai's actual social mechanic.
- Real **Langfuse**/OTel export; **e2b** microVMs for parallel offline eval at scale.
- Port the loop to **LangGraph**/**PydanticAI** for durable checkpointing + human-in-the-loop
  moderation (`interrupt()`) on the publish path.
- Richer Jam types beyond quizzes (branching stories, mini-games) behind the same contract idea.
