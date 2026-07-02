"""Reference renderer: JamSpec -> a self-contained, playable HTML quiz.

Two roles:
  * The MockModel uses this to emit both *correct* Jams and *deliberately buggy* ones
    (via `bugs=`), so the whole validate/repair loop runs offline with no API key.
  * It doubles as the "golden" the LLM-as-judge tier can diff against.

The real AnthropicModel does NOT use this — it free-generates HTML, which is where real
failures come from. This renderer just gives us a reproducible, inspectable stand-in.

Every interactive primitive carries a `data-testid` so the Playwright ladder can drive
it deterministically. That constraint is the point: it makes the smoke test reliable and
the output diffable, which is the senior tradeoff you defend in the interview.
"""

from __future__ import annotations

import json
from typing import Iterable

from .contract import JamSpec

_PALETTES = {
    "default": ("#0f172a", "#f8fafc", "#6366f1"),
    "festival": ("#1a0b2e", "#fef3c7", "#f43f5e"),
    "cozy": ("#1c1917", "#fef2f2", "#f59e0b"),
    "cyber": ("#020617", "#e0f2fe", "#22d3ee"),
}


def render_html(spec: JamSpec, bugs: Iterable[str] = ()) -> str:
    """Render `spec` to HTML. `bugs` injects known defects for the mock loop.

    Recognised bug tags:
      "dead_end"         -> drop one result screen; guarded render => blank result (DEAD_END)
      "crash_on_result"  -> drop one result screen; unguarded render => throws (RUNTIME_CRASH)
      "hallucinated_api" -> call an undefined function on load (TYPE_HALLUCINATED_API)
      "structure"        -> omit option testids (STRUCTURE)
    """
    bugs = set(bugs)
    personas = list(spec.personas)
    guard = True
    startup_extra = ""
    option_testid = True

    if "dead_end" in bugs:
        personas = personas[:-1]  # a scoreable outcome now has no screen
    if "crash_on_result" in bugs:
        personas = personas[:-1]
        guard = False
    if "hallucinated_api" in bugs:
        startup_extra = "  sparkleConfetti();  // undefined symbol -> ReferenceError\n"
    if "structure" in bugs:
        option_testid = False

    bg, fg, accent = _PALETTES.get(spec.theme, _PALETTES["default"])

    embedded = {
        "title": spec.title,
        "questions": [q.model_dump() for q in spec.questions],
        "personas": [p.model_dump() for p in personas],
    }

    title_line = (
        "persona ? persona.title : ''" if guard else "persona.title"
    )
    desc_line = (
        "persona ? persona.description : ''" if guard else "persona.description"
    )
    option_testid_js = (
        "b.setAttribute('data-testid', 'option-' + q.id + '-' + o.id);\n        "
        if option_testid
        else "// (testid intentionally omitted)\n        "
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{spec.title}</title>
<style>
  :root {{ --bg:{bg}; --fg:{fg}; --accent:{accent}; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: ui-sans-serif, system-ui, sans-serif;
         background: var(--bg); color: var(--fg);
         min-height:100vh; display:flex; align-items:center; justify-content:center; }}
  main {{ width:min(560px, 92vw); padding:28px; }}
  .card, .result {{ background: rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08);
          border-radius:18px; padding:24px; }}
  h1 {{ font-size:28px; margin:0 0 8px; }}
  h2 {{ font-size:20px; margin:0 0 18px; }}
  .option {{ display:block; width:100%; text-align:left; margin:8px 0; padding:14px 16px;
             border-radius:12px; border:1px solid rgba(255,255,255,.14);
             background: rgba(255,255,255,.02); color: var(--fg); font-size:16px;
             cursor:pointer; transition: transform .05s ease, border-color .1s ease; }}
  .option:hover {{ border-color: var(--accent); transform: translateY(-1px); }}
  .progress {{ font-size:12px; letter-spacing:.08em; text-transform:uppercase;
               opacity:.6; margin-bottom:14px; }}
  .result h1 {{ color: var(--accent); }}
</style>
</head>
<body>
<main data-testid="jam-root" data-jam="quiz"></main>
<script>
const SPEC = {json.dumps(embedded)};
let qi = 0;
const tally = {{}};
const root = document.querySelector('[data-testid="jam-root"]');

function showQuestion(i) {{
  const q = SPEC.questions[i];
  root.innerHTML = '';
  const card = document.createElement('section');
  card.className = 'card';
  card.setAttribute('data-testid', 'question-' + q.id);
  const prog = document.createElement('div');
  prog.className = 'progress';
  prog.textContent = 'Question ' + (i + 1) + ' of ' + SPEC.questions.length;
  card.appendChild(prog);
  const h = document.createElement('h2');
  h.textContent = q.prompt;
  card.appendChild(h);
  q.options.forEach(function (o) {{
    const b = document.createElement('button');
    b.className = 'option';
    {option_testid_js}b.setAttribute('data-outcome', o.outcome);
    b.textContent = o.label;
    b.addEventListener('click', function () {{ choose(o.outcome); }});
    card.appendChild(b);
  }});
  root.appendChild(card);
}}

function choose(outcome) {{
  tally[outcome] = (tally[outcome] || 0) + 1;
  qi += 1;
  if (qi < SPEC.questions.length) showQuestion(qi);
  else showResult();
}}

function winner() {{
  let best = null, bestN = -1;
  SPEC.personas.forEach(function (p) {{
    const n = tally[p.outcome] || 0;
    if (n > bestN) {{ bestN = n; best = p.outcome; }}
  }});
  // If the winning tally belongs to an outcome with no screen, `best` may still be
  // an outcome that only appears in `tally` — reflect the true winner honestly.
  for (const k in tally) {{ if (tally[k] > bestN) {{ bestN = tally[k]; best = k; }} }}
  return best;
}}

function showResult() {{
  const w = winner();
  const persona = SPEC.personas.find(function (p) {{ return p.outcome === w; }});
  root.innerHTML = '';
  const screen = document.createElement('section');
  screen.className = 'result';
  screen.setAttribute('data-testid', 'result-screen');
  const title = document.createElement('h1');
  title.setAttribute('data-testid', 'result-title');
  title.textContent = {title_line};
  const desc = document.createElement('p');
  desc.setAttribute('data-testid', 'result-desc');
  desc.textContent = {desc_line};
  screen.appendChild(title);
  screen.appendChild(desc);
  root.appendChild(screen);
}}

showQuestion(0);
{startup_extra}</script>
</body>
</html>
"""
