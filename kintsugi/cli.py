"""Command line: `kintsugi run | bench | serve`.

`run` streams the validation ladder live so you literally watch a broken Jam get healed.
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from .bench import run_bench
from .engine import run
from .models import MockModel, has_real_provider, select_real_model

try:
    from rich.console import Console
    _c = Console()

    def out(msg=""):
        _c.print(msg)
except Exception:  # rich optional
    def out(msg=""):
        # crude tag stripping so it degrades gracefully
        import re
        print(re.sub(r"\[/?[a-z0-9 #]+\]", "", str(msg)))


TIER_GLYPH = {True: "[green]✓[/green]", False: "[red]✗[/red]"}


def _load_dotenv() -> None:
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _print_ladder(ev: dict) -> None:
    tag = "first draft" if ev["index"] == 0 else f"repair round {ev['index']}"
    engine = ev["engine"]
    head = "[green]VALID[/green]" if ev["passed"] else "[red]INVALID[/red]"
    out(f"\n  [bold]{tag}[/bold]  ·  ladder ([dim]{engine}[/dim])  →  {head}")
    for t in ev["tiers"]:
        glyph = TIER_GLYPH[t["passed"]]
        name = t["tier"].ljust(12)
        if t["advisory"]:
            out(f"    [dim]{glyph} {name} {t['detail']}  (advisory)[/dim]")
        elif t["passed"]:
            out(f"    {glyph} [green]{name}[/green] [dim]{t['detail']}[/dim]")
        else:
            out(f"    {glyph} [red]{name}[/red] [yellow]{t['category']}[/yellow] — {t['detail']}")
    for skipped in ev["not_reached"]:
        out(f"    [dim]· {skipped.ljust(12)} not reached[/dim]")


def _cmd_run(args) -> int:
    _load_dotenv()
    mock = not args.real
    if not mock and not has_real_provider():
        out("[red]--real needs GROQ_API_KEY or ANTHROPIC_API_KEY (copy .env.example -> .env).[/red]")
        return 2
    if mock:
        bugs = args.bugs.split(",") if args.bugs else None
        model = MockModel(bugs=bugs)
    else:
        model = select_real_model()
    out(f"[bold cyan]Kintsugi[/bold cyan]  ·  {model.name} model  ·  "
        f"prompt: [italic]{args.prompt}[/italic]")

    def emit(ev):
        if ev["type"] == "plan":
            out(f"\n🎬 planned [bold]{ev['title']}[/bold]  "
                f"[dim]theme={ev['theme']} · outcomes: {', '.join(ev['outcomes'])}[/dim]")
        elif ev["type"] == "attempt":
            _print_ladder(ev)
        elif ev["type"] == "repair":
            arrow = "↑ escalating to strong model" if ev["attempt"] >= 1 else ""
            out(f"\n  🔧 [magenta]repairing[/magenta]: {ev['resolving']}  [dim]{arrow}[/dim]")
        elif ev["type"] == "done":
            out("")
            if ev["published"]:
                verb = "healed and published" if ev.get("rounds") else "published clean"
                rounds = f" in {ev['rounds']} repair round(s)" if ev.get("rounds") else ""
                out(f"✅ [bold green]{verb}[/bold green]{rounds}  →  {ev.get('path')}")
            else:
                out(f"⛔ [bold red]did not converge[/bold red] ({ev.get('category')}) — "
                    f"nothing published (last valid guarantee held)")
            out(f"   [dim]{ev.get('tokens', 0)} tokens · {ev.get('latency_ms', 0):.0f} ms wall[/dim]")

    res = run(
        args.prompt, model,
        max_repairs=args.max_repairs,
        prefer_browser=not args.no_browser,
        run_judge=not args.no_judge,
        emit=emit,
    )
    if res.published and args.open and res.published_path:
        webbrowser.open(res.published_path.resolve().as_uri())
    return 0 if res.published else 1


def _cmd_bench(args) -> int:
    _load_dotenv()
    mock = not args.real
    if not mock and not has_real_provider():
        out("[red]--real needs GROQ_API_KEY or ANTHROPIC_API_KEY (copy .env.example -> .env).[/red]")
        return 2
    out(f"[bold cyan]Kintsugi release gate[/bold cyan]  ·  {'mock' if mock else 'real'} model")
    res = run_bench(
        Path(args.golden),
        mock=mock,
        max_repairs=args.max_repairs,
        prefer_browser=args.browser,
        degrade=args.degrade,
        out_dir=Path(args.out),
    )
    if args.degrade:
        out("  [yellow](degrade mode: simulating a weaker model — one extra defect per case)[/yellow]")
    out("\n  repair-lift (cumulative % valid):")
    for r in range(res.max_repairs + 1):
        label = "first try" if r == 0 else f"+{r}"
        bar = "█" * round(res.valid_at(r) * 30)
        out(f"    {label:>9} │ {bar} {res.valid_at(r)*100:.0f}%")
    out(f"\n  non-convergence: {res.non_convergence_rate*100:.1f}%")
    out("  failures caught: " + ", ".join(f"{k}×{v}" for k, v in res.failure_breakdown.most_common()))
    verdict = "[green]SHIP[/green]" if res.ship else "[red]BLOCK[/red]"
    out(f"\n  verdict: {verdict}   →  chart: {args.out}/repair_lift.png")
    return 0 if res.ship else 1


def _cmd_serve(args) -> int:
    _load_dotenv()
    import uvicorn
    out(f"[bold cyan]Kintsugi[/bold cyan] demo → http://127.0.0.1:{args.port}")
    uvicorn.run("kintsugi.web:app", host="127.0.0.1", port=args.port, reload=False)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kintsugi", description="Self-healing Jam-generation runtime.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="generate + self-heal one Jam")
    pr.add_argument("prompt")
    pr.add_argument("--real", action="store_true", help="use Anthropic models (needs ANTHROPIC_API_KEY)")
    pr.add_argument("--bugs", help="(mock) pin the failure sequence, e.g. dead_end,structure")
    pr.add_argument("--max-repairs", type=int, default=4)
    pr.add_argument("--no-browser", action="store_true", help="skip Playwright, use static validation")
    pr.add_argument("--no-judge", action="store_true", help="skip advisory LLM judge tier")
    pr.add_argument("--open", action="store_true", help="open the published Jam in a browser")
    pr.set_defaults(func=_cmd_run)

    pb = sub.add_parser("bench", help="run the golden-set release gate + chart")
    pb.add_argument("--golden", default="data/golden_set.json")
    pb.add_argument("--real", action="store_true")
    pb.add_argument("--max-repairs", type=int, default=4)
    pb.add_argument("--browser", action="store_true", help="validate in a real browser (slower)")
    pb.add_argument("--degrade", action="store_true", help="simulate a weaker model (regression) — expect BLOCK")
    pb.add_argument("--out", default="runs/bench")
    pb.set_defaults(func=_cmd_bench)

    ps = sub.add_parser("serve", help="launch the split-screen Heal demo")
    ps.add_argument("--port", type=int, default=8000)
    ps.set_defaults(func=_cmd_serve)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
