"""Failure taxonomy.

Sekai's negative reviews say "crashes", "generation failed", "story dead-ends".
Those aren't one bug — they're distinct, catchable categories. Naming them is what
turns qualitative user pain into counted, gate-able metrics (see bench.py).

Framing loosely follows MAST (Multi-Agent System failure Taxonomy): most of these
are *specification* or *verification* failures, not raw model-capability failures —
which is exactly why a validate/repair loop can catch them.
"""

from __future__ import annotations

from enum import Enum


class FailureCategory(str, Enum):
    # --- specification failures (the spec/plan is wrong) ---
    SCHEMA_CONTRACT = "SCHEMA_CONTRACT"  # spec violates the Jam contract (e.g. an outcome with no result screen)

    # --- generation/build failures (the emitted code is wrong) ---
    SYNTAX = "SYNTAX"                        # HTML/JS won't parse
    STRUCTURE = "STRUCTURE"                  # required primitives / testids missing
    TYPE_HALLUCINATED_API = "TYPE_HALLUCINATED_API"  # references an undefined fn/symbol/import

    # --- verification failures (only visible when you actually run it) ---
    RUNTIME_CRASH = "RUNTIME_CRASH"          # throws while playing
    DEAD_END = "DEAD_END"                    # a reachable answer path never reaches a valid result screen

    # --- quality / safety (advisory tier) ---
    SAFETY = "SAFETY"                        # not SFW / policy issue
    INCOHERENT = "INCOHERENT"                # judged low-coherence

    # --- loop-level ---
    NON_CONVERGENCE = "NON_CONVERGENCE"      # ran out of repair budget still failing
    PROVIDER_ERROR = "PROVIDER_ERROR"        # model/API failed (rate limit, network) — infra, not content

    @property
    def stage(self) -> str:
        return {
            FailureCategory.SCHEMA_CONTRACT: "spec",
            FailureCategory.SYNTAX: "build",
            FailureCategory.STRUCTURE: "build",
            FailureCategory.TYPE_HALLUCINATED_API: "build",
            FailureCategory.RUNTIME_CRASH: "verify",
            FailureCategory.DEAD_END: "verify",
            FailureCategory.SAFETY: "quality",
            FailureCategory.INCOHERENT: "quality",
            FailureCategory.NON_CONVERGENCE: "loop",
            FailureCategory.PROVIDER_ERROR: "infra",
        }[self]
