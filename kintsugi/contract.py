"""The Jam contract.

A "Jam" (Sekai's unit of interactive content) here is a personality quiz. The
contract is the typed invariant a Jam must satisfy to be publishable. The load-bearing
invariant is `every_outcome_has_a_screen`: if an option can score toward an outcome
that has no result screen, some answer path dead-ends — which is the exact review
complaint we're targeting.

Pydantic is doing what Zod does in the TS design: it gives us a machine-checkable
contract at *plan* time, before we ever generate code.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, model_validator


class Option(BaseModel):
    id: str
    label: str = Field(min_length=1)
    outcome: str = Field(min_length=1, description="Which persona bucket this option scores toward.")


class Question(BaseModel):
    id: str
    prompt: str = Field(min_length=1)
    options: List[Option] = Field(min_length=2)


class Persona(BaseModel):
    outcome: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class JamSpec(BaseModel):
    """The typed intermediate representation the whole runtime operates on."""

    title: str = Field(min_length=1)
    theme: str = Field(default="default", description="Palette / vibe hint for the renderer.")
    questions: List[Question] = Field(min_length=3)
    personas: List[Persona] = Field(min_length=2)

    @property
    def outcomes_referenced(self) -> set[str]:
        return {o.outcome for q in self.questions for o in q.options}

    @property
    def outcomes_with_screens(self) -> set[str]:
        return {p.outcome for p in self.personas}

    @model_validator(mode="after")
    def every_outcome_has_a_screen(self) -> "JamSpec":
        """The dead-end invariant. This is the one that maps to the reviews."""
        missing = self.outcomes_referenced - self.outcomes_with_screens
        if missing:
            raise ValueError(
                f"dead-end: outcome(s) {sorted(missing)} are scoreable but have no result screen"
            )
        return self

    @model_validator(mode="after")
    def personas_are_unique(self) -> "JamSpec":
        seen = [p.outcome for p in self.personas]
        dupes = {o for o in seen if seen.count(o) > 1}
        if dupes:
            raise ValueError(f"duplicate result screens for outcome(s) {sorted(dupes)}")
        return self


def winning_outcome(spec: JamSpec, chosen_outcomes: List[str]) -> str:
    """Deterministic scoring: the most-chosen outcome wins (ties broken by spec order).

    Mirrors the JS in the rendered Jam so the Python side can predict what the browser
    *should* show for a given answer path.
    """
    counts = {p.outcome: 0 for p in spec.personas}
    for oc in chosen_outcomes:
        counts[oc] = counts.get(oc, 0) + 1
    # Preserve persona declaration order for deterministic tie-break.
    order = {p.outcome: i for i, p in enumerate(spec.personas)}
    return max(counts, key=lambda k: (counts[k], -order.get(k, 1_000_000)))


def maximizing_path(spec: JamSpec, outcome: str) -> List[str]:
    """Return one option id per question that drives *toward* `outcome` where possible.

    The validation ladder replays one maximizing path per persona to prove every result
    screen is actually reachable and renders.
    """
    path: List[str] = []
    for q in spec.questions:
        match = next((o for o in q.options if o.outcome == outcome), q.options[0])
        path.append(match.id)
    return path
