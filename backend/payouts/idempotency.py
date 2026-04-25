"""
Idempotency slot service.

We store idempotency keys in PostgreSQL rather than Redis for two reasons:
  1. The slot commits in the SAME transaction as the Payout row and HOLD entry.
     There is no window where a payout exists but the slot doesn't, or vice versa.
  2. If a worker restarts mid-flight, the slot survives. A Redis key with a short
     TTL could evict before the request finishes and silently lose the in-progress
     marker, letting a retry create a duplicate payout.

Tradeoff: every POST does an extra DB round-trip for the idempotency lookup.
At high throughput this table becomes a hotspot. The index on (merchant_id, key)
keeps reads fast, but we'd still want read replicas or a Redis L1 cache in front
of this for thousands of requests per second.

TODO: Add a periodic cleanup task to DELETE slots where expires_at < NOW() - 48h.
      We only handle expiry lazily (on read). The table grows unboundedly otherwise.

Slot lifecycle:
  claimed (response_body=NULL) → owned by an in-flight request holding a row lock
  settled (response_body=<JSON>) → any retry with the same key replays this response
"""
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from .models import IdempotencyKey, Merchant

_TTL_HOURS = 24


def claim_idempotency_slot(
    merchant: Merchant, key: str
) -> tuple[IdempotencyKey, bool]:
    """
    Claim or retrieve an idempotency slot for (merchant, key).

    Must be called inside transaction.atomic() — the slot must commit in the
    same transaction as the Payout and HOLD entry so they're always consistent.

    Returns (slot, is_new_claim):
      is_new_claim=True  → first request; caller should run business logic
      is_new_claim=False → slot already settled; caller should replay the response

    If the slot exists but response_body is NULL, a prior request claimed it and
    then rolled back (crashed, timed out, etc.). We re-claim it rather than
    returning 409 — poisoning a key permanently because of a transient failure
    is worse than letting the next caller retry cleanly.
    """
    if not connection.in_atomic_block:
        raise RuntimeError(
            "claim_idempotency_slot() must be called inside transaction.atomic()"
        )

    expires_at = timezone.now() + timedelta(hours=_TTL_HOURS)

    slot, created = IdempotencyKey.objects.get_or_create(
        merchant=merchant,
        key=key,
        defaults={"response_body": None, "status_code": 0, "expires_at": expires_at},
    )

    if created:
        return slot, True

    if slot.is_expired():
        # Both concurrent callers might see expiry simultaneously; filter delete
        # is a no-op if the other caller already removed it, then only one
        # succeeds at re-creation due to the unique constraint.
        IdempotencyKey.objects.filter(pk=slot.pk).delete()
        slot = IdempotencyKey.objects.create(
            merchant=merchant,
            key=key,
            response_body=None,
            status_code=0,
            expires_at=expires_at,
        )
        return slot, True

    # Lock the existing row so we can safely inspect response_body.
    # A concurrent request with the same key blocks here until we commit.
    slot = IdempotencyKey.objects.select_for_update().get(pk=slot.pk)

    if slot.response_body is None:
        # Prior owner rolled back — take over the slot.
        return slot, True

    return slot, False


def settle_idempotency_slot(
    slot: IdempotencyKey, response_body: dict, status_code: int
) -> None:
    """
    Mark the slot as settled with the final response.

    Must be called in the same transaction as claim_idempotency_slot() so
    the settlement commits atomically with the Payout and HOLD ledger entry.
    """
    slot.response_body = response_body
    slot.status_code = status_code
    slot.save(update_fields=["response_body", "status_code"])
