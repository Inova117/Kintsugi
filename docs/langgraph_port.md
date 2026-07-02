# Porting the loop to LangGraph / PydanticAI

The runtime in [`kintsugi/engine.py`](../kintsugi/engine.py) is a deliberately plain state
machine. This doc shows how it maps onto the frameworks named in the Sekai JD, and — just as
importantly — **when the framework earns its place and when it gets in the way**.

## The nodes are already there

The hand-rolled loop is a graph in disguise:

```
plan ──► generate ──► validate ──►(fail)──► repair ──┐
                          │(pass)                     │
                          ▼                           │
                       publish        ◄───────────────┘  (bounded by max_repairs)
```

## LangGraph (Python) sketch

```python
from langgraph.graph import StateGraph, END

class S(TypedDict):
    prompt: str; spec: JamSpec | None; html: str | None
    report: ValidationReport | None; attempt: int

g = StateGraph(S)
g.add_node("plan",     lambda s: {"spec": model.plan(s["prompt"], trace)})
g.add_node("generate", lambda s: {"html": model.generate(s["spec"], trace), "attempt": 0})
g.add_node("validate", lambda s: {"report": validate(s["html"], s["spec"], model=model, trace=trace)})
g.add_node("repair",   lambda s: {"html": model.repair(s["html"], s["spec"], s["report"], trace, s["attempt"]+1),
                                  "attempt": s["attempt"] + 1})
g.add_node("publish",  lambda s: {"published": _publish(s["html"], out_dir)})

g.set_entry_point("plan")
g.add_edge("plan", "generate")
g.add_edge("generate", "validate")
g.add_conditional_edges("validate", lambda s:
    "publish" if s["report"].passed else
    "repair"  if s["attempt"] < MAX_REPAIRS else "publish")   # last-valid fallback lives in publish
g.add_edge("repair", "validate")
g.add_edge("publish", END)
app = g.compile(checkpointer=SqliteSaver(...))   # <-- the reason to adopt it
```

**What LangGraph buys you here (and the scaffold doesn't have):**

- **Durable checkpointing** — resume a long generation after a crash/deploy instead of
  restarting the whole loop. Matters at production volume.
- **Human-in-the-loop `interrupt()`** — pause on the `publish` node for SFW moderation, get a
  human decision, resume. This is the clean home for a moderation gate.
- **Streaming + built-in observability** hooks (LangSmith) instead of the hand-rolled
  [`trace.py`](../kintsugi/trace.py).

**What it costs:** the control flow stops being greppable, `validate`'s five-way branch gets
awkward as conditional edges, and simple local debugging gets heavier. For a scaffold whose
whole point is *legibility of the loop*, that's a bad trade — hence the hand-rolled version.

## PydanticAI angle

The Jam contract is already Pydantic ([`contract.py`](../kintsugi/contract.py)), so PydanticAI
is the most natural typed-agent port: `plan` becomes an agent whose `result_type=JamSpec`
(validation + retry on contract violation is built in — exactly the `SCHEMA_CONTRACT`
repair we hand-roll), and the ladder tiers become typed tools the agent can call. It keeps the
"types are the contract" property end-to-end without LangGraph's orchestration weight — a good
middle point when you want typed tool-calling but not durable graph state.

## Rule of thumb

Start hand-rolled (this repo). Adopt **PydanticAI** when you want typed tool-calling +
automatic spec-repair. Adopt **LangGraph** when you specifically need durable checkpoints or
human-in-the-loop pause/resume — not before.
