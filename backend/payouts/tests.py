"""
Tests for the payout engine — post-audit version.

FIX (Bug #7): The original ConcurrencyTest used Django's TestCase which wraps
each test in a transaction. This means SELECT FOR UPDATE inside the view runs
within the TEST's own transaction, preventing real inter-thread locking from
working. The test either passed by luck (GIL timing) or deadlocked.

Fix: Use TransactionTestCase which commits and rolls back using TRUNCATE instead
of wrapping in a transaction. This allows select_for_update() to truly block
across threads.

IMPORTANT: TransactionTestCase is slower than TestCase because it hits the real
DB (no rollback wrapping). That is the correct trade-off for concurrency tests
that involve real locking.

Tests:
  1. ConcurrencyTest          — 2 threads, ₹60 each, ₹100 balance → 1 wins, 1 fails
  2. IdempotencyTest          — same key sent twice sequentially → identical responses
  3. IdempotencyRaceTest      — same key sent from 2 threads simultaneously → 1 wins,
                                2nd gets 201 from stored response (not a duplicate payout)
  4. StateMachineTest         — assert invalid transitions raise
  5. LedgerIntegrityTest      — assert balance formula is correct across entry types
  6. ForceFailStateMachineTest — assert _mark_payout_failed_and_release_hold follows PENDING→PROCESSING→FAILED
"""
import threading
import uuid

from django.test import TransactionTestCase, Client
from django.db import transaction

from payouts.models import LedgerEntry, Merchant, Payout, IdempotencyKey
from payouts.balance import get_balance
from payouts.state_machine import transition, InvalidTransitionError
from payouts.tasks import _mark_payout_failed_and_release_hold


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _seed_merchant(name="Test Merchant", credit_paise=10_000):
    """Create merchant + CREDIT ledger entry."""
    merchant = Merchant.objects.create(name=name)
    LedgerEntry.objects.create(
        merchant=merchant,
        entry_type=LedgerEntry.EntryType.CREDIT,
        amount_paise=credit_paise,
        payout=None,
    )
    return merchant


def _post_payout(merchant_pk, amount_paise, idempotency_key, results, index):
    """Thread target: POST a payout request and store the HTTP status code."""
    client = Client()  # Each thread needs its own client for thread safety
    resp = client.post(
        "/api/v1/payouts/",
        data={"amount_paise": amount_paise, "bank_account_id": "BANK001"},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=idempotency_key,
        HTTP_X_MERCHANT_ID=str(merchant_pk),
    )
    results[index] = (resp.status_code, resp.json())


# ─── Test 1: Concurrency ──────────────────────────────────────────────────────

class ConcurrencyTest(TransactionTestCase):
    """
    Two simultaneous payout requests for ₹60 each against a ₹100 balance.
    Exactly one must succeed (201) and one must fail (400).
    Uses TransactionTestCase so SELECT FOR UPDATE works correctly across threads.
    """

    def setUp(self):
        self.merchant = _seed_merchant(credit_paise=10_000)  # ₹100

    def test_concurrent_payouts_only_one_succeeds(self):
        results = [None, None]

        # Use distinct idempotency keys so the requests don't hit idempotency logic
        t1 = threading.Thread(
            target=_post_payout,
            args=(self.merchant.pk, 6_000, str(uuid.uuid4()), results, 0),
        )
        t2 = threading.Thread(
            target=_post_payout,
            args=(self.merchant.pk, 6_000, str(uuid.uuid4()), results, 1),
        )

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        status_codes = sorted(r[0] for r in results)

        self.assertIn(
            status_codes, [[201, 400]],
            msg=f"Expected one 201 and one 400, got: {status_codes}",
        )

        payout_count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(payout_count, 1, f"Expected 1 Payout in DB, got {payout_count}")

        hold_entries = LedgerEntry.objects.filter(
            merchant=self.merchant, entry_type=LedgerEntry.EntryType.HOLD
        )
        self.assertEqual(hold_entries.count(), 1)
        self.assertEqual(hold_entries.first().amount_paise, 6_000)

        # Balance: ₹100 credit − ₹60 hold = ₹40 available
        available, held = get_balance(self.merchant)
        self.assertEqual(available, 4_000)
        self.assertEqual(held, 6_000)


# ─── Test 2: Idempotency (sequential) ────────────────────────────────────────

class IdempotencyTest(TransactionTestCase):
    """
    Same payout request sent twice sequentially with the same Idempotency-Key.
    """

    def setUp(self):
        self.merchant = _seed_merchant(credit_paise=50_000)
        self.client = Client()
        self.idempotency_key = str(uuid.uuid4())

    def _post(self):
        return self.client.post(
            "/api/v1/payouts/",
            data={"amount_paise": 10_000, "bank_account_id": "BANK002"},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=self.idempotency_key,
            HTTP_X_MERCHANT_ID=str(self.merchant.pk),
        )

    def test_idempotent_request_same_response(self):
        r1 = self._post()
        r2 = self._post()

        self.assertEqual(r1.status_code, 201, "First request must succeed")
        self.assertEqual(r2.status_code, 201, "Second request must return 201 from stored key")
        self.assertEqual(r1.json(), r2.json(), "Idempotent responses must be identical")

        self.assertEqual(Payout.objects.filter(merchant=self.merchant).count(), 1)
        self.assertEqual(
            IdempotencyKey.objects.filter(merchant=self.merchant, key=self.idempotency_key).count(),
            1,
        )
        # Exactly one HOLD — not two
        hold_count = LedgerEntry.objects.filter(
            merchant=self.merchant, entry_type=LedgerEntry.EntryType.HOLD
        ).count()
        self.assertEqual(hold_count, 1)


# ─── Test 3: Idempotency Race (concurrent same key) ──────────────────────────

class IdempotencyRaceTest(TransactionTestCase):
    """
    Two threads send the SAME idempotency key simultaneously.
    One thread wins the slot (creates payout), the other gets either:
      a) 201 from the stored idempotency response (if first committed first), OR
      b) 409 (if first is still in-flight when second arrives)
    In NEITHER case should there be 2 Payout rows or 2 HOLD entries.
    """

    def setUp(self):
        self.merchant = _seed_merchant(credit_paise=100_000)  # ₹1000

    def test_concurrent_same_key_no_duplicate_payout(self):
        shared_key = str(uuid.uuid4())
        results = [None, None]

        t1 = threading.Thread(
            target=_post_payout,
            args=(self.merchant.pk, 30_000, shared_key, results, 0),
        )
        t2 = threading.Thread(
            target=_post_payout,
            args=(self.merchant.pk, 30_000, shared_key, results, 1),
        )

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        codes = [r[0] for r in results]

        # Both 201, or one 201 + one 409 — never two independent payouts
        for code in codes:
            self.assertIn(code, [201, 409], f"Unexpected status code: {code}")

        # Critical: at most 1 Payout created
        payout_count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertLessEqual(payout_count, 1, f"Expected ≤1 Payout in DB, got {payout_count}")

        # At most 1 HOLD in ledger
        hold_count = LedgerEntry.objects.filter(
            merchant=self.merchant, entry_type=LedgerEntry.EntryType.HOLD
        ).count()
        self.assertLessEqual(hold_count, 1, f"Expected ≤1 HOLD entry, got {hold_count}")


# ─── Test 4: State Machine ────────────────────────────────────────────────────

class StateMachineTest(TransactionTestCase):
    """Assert the state machine enforces valid transitions and rejects invalid ones."""

    def setUp(self):
        self.merchant = _seed_merchant()
        self.payout = Payout.objects.create(
            merchant=self.merchant,
            amount_paise=5_000,
            bank_account_id="BANK003",
            idempotency_key=str(uuid.uuid4()),
            status=Payout.Status.PENDING,
        )

    def test_valid_pending_to_processing(self):
        transition(self.payout, Payout.Status.PROCESSING)
        self.assertEqual(self.payout.status, Payout.Status.PROCESSING)

    def test_invalid_pending_to_completed(self):
        with self.assertRaises(InvalidTransitionError):
            transition(self.payout, Payout.Status.COMPLETED)

    def test_invalid_pending_to_failed(self):
        with self.assertRaises(InvalidTransitionError):
            transition(self.payout, Payout.Status.FAILED)

    def test_invalid_completed_to_any(self):
        self.payout.status = Payout.Status.COMPLETED
        with self.assertRaises(InvalidTransitionError):
            transition(self.payout, Payout.Status.FAILED)
        with self.assertRaises(InvalidTransitionError):
            transition(self.payout, Payout.Status.PROCESSING)

    def test_invalid_failed_to_completed(self):
        self.payout.status = Payout.Status.FAILED
        with self.assertRaises(InvalidTransitionError):
            transition(self.payout, Payout.Status.COMPLETED)


# ─── Test 5: Ledger Balance Integrity ────────────────────────────────────────

class LedgerIntegrityTest(TransactionTestCase):
    """Assert the balance formula is correct across all entry types."""

    def setUp(self):
        self.merchant = _seed_merchant(credit_paise=0)  # start empty

    def _create_entry(self, entry_type, amount_paise):
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=entry_type,
            amount_paise=amount_paise,
        )

    def test_credit_increases_available(self):
        self._create_entry(LedgerEntry.EntryType.CREDIT, 10_000)
        available, held = get_balance(self.merchant)
        self.assertEqual(available, 10_000)
        self.assertEqual(held, 0)

    def test_hold_reduces_available_increases_held(self):
        self._create_entry(LedgerEntry.EntryType.CREDIT, 10_000)
        self._create_entry(LedgerEntry.EntryType.HOLD, 3_000)
        available, held = get_balance(self.merchant)
        self.assertEqual(available, 7_000)
        self.assertEqual(held, 3_000)

    def test_release_restores_available(self):
        self._create_entry(LedgerEntry.EntryType.CREDIT, 10_000)
        self._create_entry(LedgerEntry.EntryType.HOLD, 3_000)
        self._create_entry(LedgerEntry.EntryType.RELEASE, 3_000)
        available, held = get_balance(self.merchant)
        self.assertEqual(available, 10_000)
        self.assertEqual(held, 0)

    def test_debit_reduces_available(self):
        self._create_entry(LedgerEntry.EntryType.CREDIT, 10_000)
        self._create_entry(LedgerEntry.EntryType.HOLD, 3_000)
        self._create_entry(LedgerEntry.EntryType.DEBIT, 3_000)
        available, held = get_balance(self.merchant)
        # Credit 10000, Hold -3000 (to available), Debit -3000
        # available = 10000 - 3000 - 3000 = 4000
        self.assertEqual(available, 4_000)
        self.assertEqual(held, 3_000)


# ─── Test 6: Force-Fail State Machine Path ───────────────────────────────────

class ForceFailStateMachineTest(TransactionTestCase):
    """
    _mark_payout_failed_and_release_hold must follow the state machine:
      PENDING → PROCESSING → FAILED (two transitions, not a direct jump)
    """

    def setUp(self):
        self.merchant = _seed_merchant(credit_paise=20_000)

    def test_force_fail_from_pending_follows_state_machine(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            amount_paise=5_000,
            bank_account_id="BANK004",
            idempotency_key=str(uuid.uuid4()),
            status=Payout.Status.PENDING,
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.EntryType.HOLD,
            amount_paise=5_000,
            payout=payout,
        )

        _mark_payout_failed_and_release_hold(payout.pk)

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.FAILED)
        # RELEASE must exist to unblock funds
        release_count = LedgerEntry.objects.filter(
            merchant=self.merchant, entry_type=LedgerEntry.EntryType.RELEASE
        ).count()
        self.assertEqual(release_count, 1)
        # After force-fail, balance should be restored to full credit
        available, held = get_balance(self.merchant)
        self.assertEqual(available, 20_000)
        self.assertEqual(held, 0)

    def test_force_fail_from_processing_goes_directly_to_failed(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            amount_paise=5_000,
            bank_account_id="BANK005",
            idempotency_key=str(uuid.uuid4()),
            status=Payout.Status.PROCESSING,
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.EntryType.HOLD,
            amount_paise=5_000,
            payout=payout,
        )

        _mark_payout_failed_and_release_hold(payout.pk)

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.FAILED)

    def test_force_fail_on_completed_payout_is_noop(self):
        """Force-fail on an already COMPLETED payout must not create a RELEASE."""
        payout = Payout.objects.create(
            merchant=self.merchant,
            amount_paise=5_000,
            bank_account_id="BANK006",
            idempotency_key=str(uuid.uuid4()),
            status=Payout.Status.COMPLETED,
        )

        _mark_payout_failed_and_release_hold(payout.pk)

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.COMPLETED)  # unchanged
        release_count = LedgerEntry.objects.filter(
            merchant=self.merchant, entry_type=LedgerEntry.EntryType.RELEASE
        ).count()
        self.assertEqual(release_count, 0)
