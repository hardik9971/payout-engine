"""
Celery tasks for payout processing.

execute_payout_task(payout_id):
  Drives a payout through PENDING → PROCESSING → COMPLETED/FAILED.
  Gateway outcome is simulated (no real bank integration yet):
    70% → COMPLETED  (DEBIT entry created, hold cleared)
    20% → FAILED     (RELEASE entry created, funds unblocked)
    10% → stays PROCESSING (picked up by requeue_stuck_payouts beat task)

  We use SELECT FOR UPDATE (nowait=True) on the payout row. If another worker
  already holds the lock, we back off without burning a retry slot — lock
  contention is transient, not a business error.

  Retry strategy: 3 attempts with [1, 2, 4] second delays.
  TODO: Celery 5.4+ supports autoretry_for with exponential_backoff=True, which
        would be cleaner than the manual retry logic here. We're on 5.3.6.

  On final failure, _mark_payout_failed_and_release_hold() ensures funds are
  always unblocked even if we crash between the status update and the RELEASE
  entry. Both happen in one atomic transaction.

requeue_stuck_payouts():
  Beat task (every 60s). Detects PROCESSING payouts older than 30s and re-queues
  them. This covers the 10% simulated "stuck" case and real-world worker crashes.

  Tradeoff: the 30s threshold is shorter than the max retry window (~7s total).
  A legitimately slow gateway call (e.g. 40s bank timeout) would get re-queued
  while the original worker is still waiting. The nowait lock in the task
  prevents double-execution, but the original worker wastes a retry attempt.
  Raising the threshold to 90s would reduce unnecessary re-queues.
"""
import logging
import random
from datetime import timedelta

from celery import shared_task
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from .models import IdempotencyKey, LedgerEntry, Payout
from .state_machine import InvalidTransitionError, transition

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
# Exponential-ish backoff. Kept as a list so it's easy to tune without math.
_RETRY_DELAYS = [1, 2, 4]  # seconds; index = retry attempt number (0-based)
_STALE_THRESHOLD_SECONDS = 30


def _simulate_gateway_response() -> str:
    """
    Placeholder for a real payment gateway call.

    In production this would call the bank/gateway API and return the outcome.
    The 70/20/10 split is intentional: it exercises all three state machine
    paths (COMPLETED, FAILED, stuck-then-recovered) in a short demo session.

    TODO: Replace with a real gateway client. The client should handle:
      - Gateway-side idempotency (send our payout_id as the gateway reference)
      - Connection timeouts (separate from business-logic failures)
      - Gateway returning "pending" (map that to our PROCESSING/stuck path)
    """
    roll = random.random()
    if roll < 0.70:
        return Payout.Status.COMPLETED
    if roll < 0.90:
        return Payout.Status.FAILED
    return Payout.Status.PROCESSING  # simulates a stuck/slow gateway response


@shared_task(bind=True, max_retries=MAX_RETRIES, name="payouts.tasks.execute_payout_task")
def execute_payout_task(self, payout_id: int) -> dict:
    """
    Execute a single payout end-to-end, driving it through the state machine.

    We lock the payout row with nowait=True. If another worker holds the lock
    (e.g. the beat task and a retry worker arrive simultaneously), we back off
    with a fixed 2s delay rather than burning a retry slot — the contention is
    transient, not a failure we want to count against MAX_RETRIES.
    """
    logger.info("Processing payout #%s (attempt %s)", payout_id, self.request.retries + 1)

    try:
        with transaction.atomic():
            try:
                payout = Payout.objects.select_for_update(nowait=True).get(pk=payout_id)
            except Payout.DoesNotExist:
                logger.error("Payout #%s not found — task may have been queued with a bad ID.", payout_id)
                return {"status": "not_found", "payout_id": payout_id}
            except OperationalError:
                # Another worker holds the row lock — back off without burning a retry slot.
                logger.warning("Payout #%s locked by another worker; backing off.", payout_id)
                raise self.retry(countdown=2)

            if payout.status not in (Payout.Status.PENDING, Payout.Status.PROCESSING):
                logger.warning("Payout #%s is already %s; nothing to do.", payout_id, payout.status)
                return {"status": "skipped", "payout_status": payout.status}

            if payout.status == Payout.Status.PENDING:
                transition(payout, Payout.Status.PROCESSING)
                payout.processing_started_at = timezone.now()
                payout.save(update_fields=["status", "processing_started_at", "updated_at"])

            outcome = _simulate_gateway_response()

            if outcome == Payout.Status.PROCESSING:
                # Gateway didn't give us a definitive answer. We leave the payout
                # in PROCESSING and let requeue_stuck_payouts pick it up after 30s.
                logger.warning("Payout #%s: gateway returned no outcome (stuck). Beat will recover.", payout_id)
                return {"status": "stuck", "payout_id": payout_id}

            transition(payout, outcome)
            payout.save(update_fields=["status", "updated_at"])

            # DEBIT on success (funds leave the ledger), RELEASE on failure
            # (hold is lifted so the merchant can use those funds again).
            entry_type = (
                LedgerEntry.EntryType.DEBIT
                if outcome == Payout.Status.COMPLETED
                else LedgerEntry.EntryType.RELEASE
            )
            LedgerEntry.objects.create(
                merchant=payout.merchant,
                entry_type=entry_type,
                amount_paise=payout.amount_paise,
                payout=payout,
            )
            logger.info("Payout #%s → %s. %s entry created.", payout_id, outcome, entry_type)

    except InvalidTransitionError as exc:
        # State machine violation. This shouldn't happen in normal operation —
        # it means the payout was somehow mutated outside the task. Log loudly.
        logger.error("Payout #%s invalid transition: %s", payout_id, exc)
        return {"status": "invalid_transition", "error": str(exc)}

    except Exception as exc:
        retry_number = self.request.retries
        if retry_number < MAX_RETRIES:
            delay = _RETRY_DELAYS[min(retry_number, len(_RETRY_DELAYS) - 1)]
            logger.warning(
                "Payout #%s error (retry %s/%s in %ss): %s",
                payout_id, retry_number + 1, MAX_RETRIES, delay, exc,
            )
            raise self.retry(exc=exc, countdown=delay)

        logger.error("Payout #%s exhausted %s retries; force failing.", payout_id, MAX_RETRIES)
        _mark_payout_failed_and_release_hold(payout_id)
        return {"status": "max_retries_exceeded", "payout_id": payout_id}

    return {"status": outcome, "payout_id": payout_id}


def _mark_payout_failed_and_release_hold(payout_id: int) -> None:
    """
    Transition a payout to FAILED and create a RELEASE entry to unblock funds.

    Both happen in one atomic transaction. If we crash between the two writes,
    the next beat cycle re-runs this function (status is still PROCESSING, age
    exceeds the stale threshold) — creating a duplicate RELEASE would be bad.
    The lock prevents concurrent execution; we re-check the terminal state guard
    at the top to make the function idempotent.

    Always routes through PENDING → PROCESSING → FAILED via the state machine
    so the audit trail is consistent regardless of where we were interrupted.
    """
    with transaction.atomic():
        try:
            payout = Payout.objects.select_for_update().get(pk=payout_id)
        except Payout.DoesNotExist:
            return

        if payout.status in (Payout.Status.COMPLETED, Payout.Status.FAILED):
            return  # already terminal, nothing to do

        try:
            if payout.status == Payout.Status.PENDING:
                transition(payout, Payout.Status.PROCESSING)
                payout.processing_started_at = timezone.now()
                payout.save(update_fields=["status", "processing_started_at", "updated_at"])

            transition(payout, Payout.Status.FAILED)
            payout.save(update_fields=["status", "updated_at"])
        except InvalidTransitionError as exc:
            logger.error("_mark_payout_failed_and_release_hold: unexpected state for #%s: %s", payout_id, exc)
            return

        LedgerEntry.objects.create(
            merchant=payout.merchant,
            entry_type=LedgerEntry.EntryType.RELEASE,
            amount_paise=payout.amount_paise,
            payout=payout,
        )
        logger.info("Payout #%s force-failed. RELEASE entry created, funds unblocked.", payout_id)


@shared_task(name="payouts.tasks.requeue_stuck_payouts")
def requeue_stuck_payouts() -> dict:
    """
    Re-queue PROCESSING payouts that have been stuck longer than the stale threshold.

    We snapshot stuck payout IDs first, then lock each row individually inside
    its own atomic block. This prevents one locked row from blocking recovery of
    the others, and avoids a single long-held transaction.

    We use nowait=True on each row so concurrent beat workers (unlikely but
    possible in misconfigured deployments) don't block each other.
    """
    stale_cutoff = timezone.now() - timedelta(seconds=_STALE_THRESHOLD_SECONDS)

    stale_ids = list(
        Payout.objects.filter(
            status=Payout.Status.PROCESSING,
            processing_started_at__lt=stale_cutoff,
        ).values_list("id", flat=True)
    )

    re_queued = 0
    force_failed = 0

    for payout_id in stale_ids:
        with transaction.atomic():
            try:
                payout = Payout.objects.select_for_update(nowait=True).get(pk=payout_id)
            except (Payout.DoesNotExist, OperationalError):
                continue  # processed by another worker or locked — skip

            if payout.status != Payout.Status.PROCESSING:
                continue  # resolved between snapshot and lock acquisition

            if payout.retry_count >= MAX_RETRIES:
                logger.warning("Stuck payout #%s hit retry ceiling; force failing.", payout_id)
                _mark_payout_failed_and_release_hold(payout_id)
                force_failed += 1
            else:
                Payout.objects.filter(pk=payout_id).update(retry_count=F("retry_count") + 1)
                transaction.on_commit(
                    lambda pid=payout_id: execute_payout_task.apply_async(args=[pid])
                )
                logger.info("Stuck payout #%s re-queued (retry %s).", payout_id, payout.retry_count + 1)
                re_queued += 1

    return {"re_queued": re_queued, "force_failed": force_failed}


@shared_task(name="payouts.tasks.purge_expired_idempotency_slots")
def purge_expired_idempotency_slots() -> dict:
    """
    Delete idempotency slots that have passed their expiry timestamp.

    claim_idempotency_slot() handles expiry lazily on read, but that only
    removes individual rows when they happen to be re-used. Without a
    periodic sweep, slots for one-shot requests (the vast majority) never
    get cleaned up and the table grows unboundedly.

    We add a 48-hour buffer beyond expires_at before deleting so that any
    in-flight debugging or replay window is preserved after expiry.
    """
    cutoff = timezone.now() - timedelta(hours=48)
    deleted_count, _ = IdempotencyKey.objects.filter(expires_at__lt=cutoff).delete()
    logger.info("purge_expired_idempotency_slots: deleted %s expired slots.", deleted_count)
    return {"deleted": deleted_count}
