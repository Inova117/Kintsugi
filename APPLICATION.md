# Kintsugi — a demo for Sekai's agent-harness / app-generation role

> **Kintsugi (金継ぎ)** is the Japanese art of repairing broken pottery with gold, so the
> seam becomes the most beautiful part. **Sekai (世界)** means *world*. This project is a
> working, miniature version of the system Sekai is hiring someone to own: it turns a prompt
> into a playable mini-app, and when the generation breaks, it heals it — and the repair is
> the point.

**Repo:** https://github.com/Inova117/Kintsugi · **Run it:** see [README](README.md) (works
offline in 2 minutes, no API key).

---

## The problem Sekai is actually solving

Sekai turns a prompt into a playable, shareable mini-app *instantly*. Generating *something*
is easy now. Generating something that reliably **works** — every path, every time, at scale,
and tastefully — is the hard part. Sekai's own store reviews name the failure mode directly:
content that **crashes** or **dead-ends**. And the job post is explicit about the mission:

> *"We're doubling down on creation quality and reliability… own the lane end-to-end and set
> the technical bar for agent-driven app generation. How do we generate fully autonomous
> software for recreational use? How do we generate it fast? And how do we do it tastefully?"*

That is the whole game. Kintsugi is built around exactly that question.

---

## What I built

A **self-healing generation runtime**. Give it a prompt and it runs a real agentic loop:

```
prompt → plan → generate → run / validate → repair → publish
```

1. **plan** — the model produces a typed spec (Pydantic contract); if the spec would
   dead-end, the contract rejects it and the model repairs the *spec* first.
2. **generate** — the model writes a real, self-contained interactive mini-app (a playable
   quiz) as actual code.
3. **run / validate** — a **five-tier ladder** runs the app in a **real headless browser**
   (Playwright), driving *every* answer path to prove each one reaches a valid result screen —
   catching the exact "story dead-ends / crashes" failure Sekai's reviews describe.
4. **repair** — on failure, it tags the failure by category, feeds the *exact* broken button
   or path back to the model, and asks for a targeted fix — bounded, so it always terminates.
5. **publish** — only Jams that actually work ship; the rest are blocked.

Around that loop: a **failure taxonomy**, a **release gate** (a regression benchmark with
explicit SHIP/BLOCK thresholds), **model routing** (cheap model for the frequent draft,
strong model for the rarer repair), and **tracing** (per-step tokens / latency / model). It
runs on real models (Groq, Anthropic) and fully offline via a deterministic mock, so the
mechanism is demonstrable for free.

---

## How it maps to the role — every "you will own" bullet

| What Sekai says you'll own / want | Where it lives in this repo |
|---|---|
| **Own the agent harness / runtime layer** | The `plan→generate→validate→repair→publish` state machine — [`engine.py`](kintsugi/engine.py) |
| **Long-horizon agentic workflow, maximal autonomy** | The bounded repair loop with failure-context feedback + model escalation |
| **Eval & quality loops: eval harnesses, regression, failure taxonomies** | 5-tier validation ladder [`validate.py`](kintsugi/validate.py), 9-category taxonomy [`taxonomy.py`](kintsugi/taxonomy.py), release gate [`bench.py`](kintsugi/bench.py) |
| **Model strategy: routing, benchmarking, reliability, cost/latency** | Cheap-draft → strong-repair routing [`models.py`](kintsugi/models.py); per-step token/latency spans; the benchmark |
| **Debuggable systems: tracing, metrics, observability** | OpenTelemetry-shaped spans → `trace.json` [`trace.py`](kintsugi/trace.py); the live validation-ladder UI |
| **Research novel approaches & ship to production** | The repair loop + gate are the experiment; shipped as a running web app |
| **Agentic systems: tool use, orchestration, retry/repair, context mgmt** | The whole loop; repair passes structured failure context back to the model |
| **Frameworks (LangGraph / PydanticAI…) — and knowing when they get in the way** | Hand-rolled state machine *by choice*, with a documented port — [`docs/langgraph_port.md`](docs/langgraph_port.md) |
| **Bonus: built a code-generation pipeline ("this is what we do")** | Kintsugi *is* a code-generation pipeline |
| **Bonus: evaluation frameworks (scoring, regression, release gating)** | The golden-set gate with SHIP/BLOCK thresholds |
| **Strong product instincts → system-design decisions** | The typed contract + `data-testid` primitives are chosen for reliability & diffability; the whole thing targets Sekai's real review complaints |

---

## Why this demo proves I can do *this* job

1. **It's the actual loop, not a wrapper.** The hard, differentiating part of Sekai's product
   is the `generate → run → repair → publish` cycle with *real execution feedback*. That loop —
   driving generated code in a real browser and healing it from what actually breaks — is the
   center of this repo, and it works. Most "AI app" demos stop at "the model returned some
   text." This one runs the output and proves it.

2. **I treated quality as measured, not hoped-for.** A named failure taxonomy, a regression
   gate with explicit thresholds, deterministic fault-injection to validate the repair loop,
   and an LLM judge kept strictly *advisory* so it can't inflate the headline number. That is
   precisely the "eval harnesses, regression testing, failure taxonomy" the role owns.

3. **I made real reliability tradeoffs under real constraints.** Model routing (cheap draft,
   strong repair) for cost/latency; a bounded repair budget as a *correctness* guarantee
   (non-converging loops are a real failure mode, so termination is a property, not a nicety);
   and graceful degradation — when a provider rate-limited mid-run, the system produced a clean
   `PROVIDER_ERROR` and "did not converge" instead of crashing. Those are the instincts a
   reliability-focused agent-harness role is made of.

4. **I was honest about what worked and what didn't — because that's the eval mindset.**
   On the free 8B model tier, the gate measured roughly half of quizzes valid and correctly
   **BLOCKED** that config. I didn't hide it or fake a prettier number — the gate's entire job
   is to tell the truth, even when the truth is "not good enough yet." A team doubling down on
   *reliability* wants the person who ships the gate that says no.

---

## Honest scope

This is a weekend-scale demo, not a production system. It generates one Jam type (quizzes),
validation targets a constrained contract, and the "release gate" chart shown in the UI is a
**deterministic fault-injection** suite (a standard eval technique) — clearly labeled as such,
not disguised as live-model data. Live-model runs are real but free-tier-limited (Groq's strong
70B model is daily-capped; the small 8B model isn't reliable enough to repair its own output,
which the gate honestly reports). See the README's "note on the free tier."

**What I'd build next, in priority order:** promote "taste" to a tracked eval metric (LLM-judge
coherence validated against human labels); first-class **remix** (fork a Jam, mutate the spec,
re-validate — Sekai's actual social mechanic); real Langfuse/OTel export; and a port of the loop
to LangGraph/PydanticAI for durable checkpointing + human-in-the-loop moderation on publish.

---

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
python -m playwright install chromium

kintsugi serve        # → http://127.0.0.1:8000   (the live demo)
kintsugi run "the cursed quiz — which chaos gremlin are you?"   # watch a broken Jam get healed
kintsugi bench        # the release gate + repair-lift chart
```

For real generation with a free Groq key, see [README → Run it for real](README.md).
