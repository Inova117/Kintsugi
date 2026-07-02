"""The Jam contract is the spec-time gate. Its invariants are the review complaints."""

import pytest
from pydantic import ValidationError

from kintsugi.contract import JamSpec, maximizing_path, winning_outcome


def _valid_payload():
    return {
        "title": "Which snack are you?",
        "theme": "cozy",
        "questions": [
            {"id": "q0", "prompt": "Pick a vibe", "options": [
                {"id": "a", "label": "Salty", "outcome": "chip"},
                {"id": "b", "label": "Sweet", "outcome": "cookie"},
            ]},
            {"id": "q1", "prompt": "Pick a time", "options": [
                {"id": "a", "label": "Noon", "outcome": "chip"},
                {"id": "b", "label": "Midnight", "outcome": "cookie"},
            ]},
            {"id": "q2", "prompt": "Pick a place", "options": [
                {"id": "a", "label": "Couch", "outcome": "chip"},
                {"id": "b", "label": "Kitchen", "outcome": "cookie"},
            ]},
        ],
        "personas": [
            {"outcome": "chip", "title": "The Chip", "description": "crunchy and reliable"},
            {"outcome": "cookie", "title": "The Cookie", "description": "soft and generous"},
        ],
    }


def test_valid_spec_parses():
    spec = JamSpec.model_validate(_valid_payload())
    assert spec.outcomes_referenced == {"chip", "cookie"}
    assert spec.outcomes_referenced <= spec.outcomes_with_screens


def test_dead_end_invariant_rejects_missing_screen():
    payload = _valid_payload()
    # Keep two screens (so min_length passes) but leave the referenced 'cookie' uncovered:
    # rename the second screen to an outcome no option scores toward.
    payload["personas"][1]["outcome"] = "cake"
    with pytest.raises(ValidationError, match="dead-end"):
        JamSpec.model_validate(payload)


def test_requires_three_questions():
    payload = _valid_payload()
    payload["questions"] = payload["questions"][:2]
    with pytest.raises(ValidationError):
        JamSpec.model_validate(payload)


def test_scoring_and_maximizing_path():
    spec = JamSpec.model_validate(_valid_payload())
    path = maximizing_path(spec, "cookie")
    chosen = [next(o.outcome for o in q.options if o.id == oid) for q, oid in zip(spec.questions, path)]
    assert winning_outcome(spec, chosen) == "cookie"
