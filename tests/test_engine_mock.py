"""End-to-end loop behavior on the offline mock: heal, converge-clean, and the
non-convergence / last-valid guarantee."""

from pathlib import Path

from kintsugi.engine import run
from kintsugi.models import MockModel
from kintsugi.taxonomy import FailureCategory


def _run(prompt, bugs, tmp_path, max_repairs=4):
    return run(
        prompt, MockModel(bugs=bugs),
        max_repairs=max_repairs,
        out_dir=tmp_path,
        run_id="test",
        prefer_browser=False,
        run_judge=False,
    )


def test_clean_first_try(tmp_path):
    res = _run("a wholesome quiz", [], tmp_path)
    assert res.published and res.rounds_to_valid == 0
    assert not res.healed


def test_single_heal(tmp_path):
    res = _run("the cursed quiz", ["dead_end"], tmp_path)
    assert res.published and res.rounds_to_valid == 1 and res.healed
    assert not res.attempts[0].report.passed
    assert res.attempts[0].report.category == FailureCategory.DEAD_END
    assert res.attempts[1].report.passed
    assert (tmp_path / "test" / "jam.html").exists()
    assert (tmp_path / "test" / "trace.json").exists()


def test_multi_heal(tmp_path):
    res = _run("stack of bugs", ["structure", "dead_end", "hallucinated_api"], tmp_path)
    assert res.published and res.rounds_to_valid == 3


def test_non_convergence_publishes_nothing(tmp_path):
    # 5 defects but only 4 repairs allowed -> must not publish, must flag NON_CONVERGENCE
    res = _run("unfixable", ["dead_end", "structure", "hallucinated_api", "crash_on_result", "dead_end"], tmp_path)
    assert not res.published
    assert res.final_category == FailureCategory.NON_CONVERGENCE
    assert not (tmp_path / "test" / "jam.html").exists()


def test_trace_records_routing(tmp_path):
    res = _run("route me", ["dead_end"], tmp_path)
    kinds = [s.kind for s in res.trace.spans]
    assert "plan" in kinds and "generate" in kinds and "repair" in kinds
    repair_models = [s.model for s in res.trace.spans if s.kind == "repair"]
    assert any("sonnet" in (m or "") for m in repair_models)  # escalation happened
