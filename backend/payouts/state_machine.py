"""
State machine for Payout status transitions.

We enforce transitions through a single function rather than scattering
if/elif guards across the codebase. Two reasons:

  1. The adjacency map is the single source of truth for what's allowed.
     Adding a new state means updating one dict, not hunting down every
     status assignment in views.py, tasks.py, and seed commands.

  2. Terminal states (COMPLETED, FAILED) need to be impossible to exit.
     A payout that COMPLETED cannot become FAILED because a retry worker
     woke up late and tried to re-process it. Failing loudly here prevents
     silent ledger corruption.

Valid transitions:
  PENDING    → PROCESSING
  PROCESSING → COMPLETED
  PROCESSING → FAILED

COMPLETED and FAILED are terminal. Operator retries create a NEW payout
rather than resurrecting a terminal one.
"""
from .models import Payout


class InvalidTransitionError(Exception):
    """Raised when code attempts an illegal payout state transition."""


_VALID_TRANSITIONS: dict[str, set[str]] = {
    Payout.Status.PENDING: {Payout.Status.PROCESSING},
    Payout.Status.PROCESSING: {Payout.Status.COMPLETED, Payout.Status.FAILED},
    Payout.Status.COMPLETED: set(),  # terminal
    Payout.Status.FAILED: set(),     # terminal
}


def transition(payout: Payout, new_status: str) -> None:
    """
    Apply a status transition to a Payout instance in-place.

    Does NOT save — the caller commits inside an atomic block together with
    any ledger entries so that both writes succeed or both roll back.
    """
    allowed = _VALID_TRANSITIONS.get(payout.status, set())
    if new_status not in allowed:
        raise InvalidTransitionError(
            f"Payout #{payout.pk}: {payout.status!r} → {new_status!r} is not allowed. "
            f"Allowed: {sorted(allowed) or 'none (terminal state)'}."
        )
    payout.status = new_status
