"""The validation ladder must catch each failure category. Runs in static mode so the
suite needs no browser; the browser path is exercised by the end-to-end run in the README.
"""

import pytest

from kintsugi.models import MockModel
from kintsugi.render import render_html
from kintsugi.taxonomy import FailureCategory
from kintsugi.trace import Trace
from kintsugi.validate import validate


@pytest.fixture
def spec():
    return MockModel()._template_spec("Which festival headliner are you?")


def _validate(html, spec):
    # static mode = deterministic + no chromium needed
    return validate(html, spec, model=None, trace=Trace("t"), prefer_browser=False, run_judge=False)


def test_clean_jam_passes(spec):
    report = _validate(render_html(spec), spec)
    assert report.passed, report.summary()
    assert report.engine == "static"


def test_dead_end_is_caught(spec):
    report = _validate(render_html(spec, bugs=["dead_end"]), spec)
    assert not report.passed
    assert report.category == FailureCategory.DEAD_END
    assert report.failure.tier == "PLAYTHROUGH"


def test_hallucinated_api_is_caught(spec):
    report = _validate(render_html(spec, bugs=["hallucinated_api"]), spec)
    assert report.category == FailureCategory.TYPE_HALLUCINATED_API
    assert report.failure.tier == "RENDER"


def test_missing_structure_is_caught(spec):
    report = _validate(render_html(spec, bugs=["structure"]), spec)
    assert report.category == FailureCategory.STRUCTURE
    assert report.failure.tier == "STRUCTURE"


def test_crash_on_result_is_caught(spec):
    report = _validate(render_html(spec, bugs=["crash_on_result"]), spec)
    assert report.category == FailureCategory.RUNTIME_CRASH


# --- real-execution path (skipped when chromium isn't installed) ---
from kintsugi.validate import _can_launch_browser  # noqa: E402

_HAS_BROWSER = _can_launch_browser()
browser_only = pytest.mark.skipif(not _HAS_BROWSER, reason="chromium not installed")


@browser_only
def test_browser_clean_and_dead_end(spec):
    """Regression: multiple playthroughs on one validate() must each work (fresh page per path)."""
    clean = validate(render_html(spec), spec, trace=Trace("b"), prefer_browser=True, run_judge=False)
    assert clean.engine == "browser" and clean.passed, clean.summary()

    dead = validate(render_html(spec, bugs=["dead_end"]), spec, trace=Trace("b"),
                    prefer_browser=True, run_judge=False)
    assert not dead.passed
    assert dead.category == FailureCategory.DEAD_END
    assert "blank result screen" in dead.failure.detail
