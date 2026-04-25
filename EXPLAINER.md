# EXPLAINER.md — Payout Engine Design Decisions

---

## 1. Ledger Design & Balance Query

### Why an Append-Only Ledger?

Money systems need a perfect audit trail. A ledger that only ever receives new rows means:
- Every state change is permanently recorded
- You can reconstruct any historical balance
- No UPDATE or DELETE can silently corrupt the financial record

The `LedgerEntry` model has four types:

| Type | Effect on Available Balance |
|---|---|
| `CREDIT` | +balance (funds received) |
| `DEBIT` | -balance (funds disbursed) |
| `HOLD` | -available (reserved for payout) |
| `RELEASE` | +available (hold lifted on failure) |

### Balance Query (Single DB Aggregation)

```python
# backend/payouts/balance.py
result = LedgerEntry.objects.filter(merchant=merchant).aggregate(
    available_paise=Sum(
        Case(
            When(entry_type='CREDIT',  then='amount_paise'),
            When(entry_type='DEBIT',   then=F('amount_paise') * -1),
            When(entry_type='HOLD',    then=F('amount_paise') * -1),
            When(entry_type='RELEASE', then='amount_paise'),
            default=0,
        )
    ),
    held_paise=Sum(
        Case(
            When(entry_type='HOLD',    then='amount_paise'),
            When(entry_type='RELEASE', then=F('amount_paise') * -1),
            default=0,
        )
    ),
)
```

This is equivalent to:
```sql
SELECT
  SUM(CASE WHEN entry_type='CREDIT'  THEN amount_paise
           WHEN entry_type='DEBIT'   THEN -amount_paise
           WHEN entry_type='HOLD'    THEN -amount_paise
           WHEN entry_type='RELEASE' THEN  amount_paise
           ELSE 0 END) AS available_paise,
  SUM(CASE WHEN entry_type='HOLD'    THEN  amount_paise
           WHEN entry_type='RELEASE' THEN -amount_paise
           ELSE 0 END) AS held_paise
FROM ledger_entries
WHERE merchant_id = %s;
```

**Why not fetch rows and sum in Python?** Python-level aggregation is:
1. Slower (fetches N rows across the network)
2. Incorrect under concurrency (stale reads if not inside a transaction)
3. Prone to floating-point bugs if you ever mistakenly use floats

---

## 2. Concurrency Locking (SELECT FOR UPDATE)

### The Problem

Merchant has ₹100. Two API requests for ₹60 arrive simultaneously. Without locking, both threads could read ₹100, both see "sufficient funds", and both create a ₹60 HOLD — resulting in a ₹120 hold on a ₹100 balance.

### The Solution

```python
# backend/payouts/views.py
with transaction.atomic():
    # Row-level lock on the merchant row.
    # Thread 2 blocks here until Thread 1's transaction commits or rolls back.
    merchant = Merchant.objects.select_for_update().get(pk=merchant.pk)

    available_paise, _ = get_balance(merchant)

    if available_paise < amount_paise:
        return Response({"error": "Insufficient balance."}, status=400)

    payout = Payout.objects.create(...)
    LedgerEntry.objects.create(entry_type='HOLD', ...)
```

`SELECT FOR UPDATE` acquires a PostgreSQL row-level lock. The second concurrent request blocks at that line until the first transaction either commits or rolls back. When Thread 1 commits the HOLD, Thread 2 re-reads the balance and correctly sees only ₹40 remaining — insufficient — and returns 400.

This is tested in `ConcurrencyTest.test_concurrent_payouts_only_one_succeeds`.

---

## 3. Idempotency Handling

### Why Idempotency?

Networks are unreliable. A client might not receive the 201 response but the server already processed the request. Without idempotency, retrying creates a duplicate payout.

### Implementation

Every `POST /api/v1/payouts/` requires an `Idempotency-Key: <uuid>` header.

On the **first** request:
1. No record exists → business logic runs → response is stored in `IdempotencyKey` table
2. TTL: 24 hours

On **subsequent** requests with the same key:
1. Record found → stored `(response_body, status_code)` is returned immediately
2. No DB writes, no task dispatch — purely a lookup

### Concurrent Duplicate Requests

If two requests arrive simultaneously with the same key before either has stored the `IdempotencyKey` record:

1. Thread 1 enters the atomic block, creates a `Payout` row with `(merchant, idempotency_key)` unique together
2. Thread 2 also enters the atomic block and tries to create a `Payout` — PostgreSQL raises `IntegrityError` (unique constraint violation)
3. Thread 2's view catches `IntegrityError` and returns `HTTP 409 Conflict`
4. The client retries with the same key → hits the stored idempotency record → gets the original 201

This means the DB unique constraint on `(merchant, idempotency_key)` acts as the concurrency guard for duplicate key races.

---

## 4. State Machine Enforcement

```python
# backend/payouts/state_machine.py
_VALID_TRANSITIONS = {
    "PENDING":    {"PROCESSING"},
    "PROCESSING": {"COMPLETED", "FAILED"},
    "COMPLETED":  set(),   # terminal
    "FAILED":     set(),   # terminal
}

def transition(payout, new_status):
    allowed = _VALID_TRANSITIONS.get(payout.status, set())
    if new_status not in allowed:
        raise InvalidTransitionError(...)
    payout.status = new_status
```

`transition()` is called before **every** status update — in the API view and in the Celery task. It does NOT call `payout.save()` — that's left to the caller so it can be batched with ledger entry creation inside `transaction.atomic()`.

**Why not use `if/elif` guards scattered around?** A single adjacency map is:
1. Easy to reason about (the entire state machine fits on one screen)
2. Safe by default — any unlisted transition fails loudly
3. Tested automatically by the type checker (exhaustive dict)

---

## 5. One Bug That Was Found and Fixed

### Original Code (Incorrect)

During development, the balance query was first written as:

```python
# WRONG — This returns floats and computes in Python
entries = LedgerEntry.objects.filter(merchant=merchant)
available = sum(
    e.amount_paise if e.entry_type in ('CREDIT', 'RELEASE') else -e.amount_paise
    for e in entries
)
```

**Why this is wrong:**
1. It fetches all ledger rows into Python memory — O(N) on a potentially large table
2. Under concurrent requests, it reads rows outside the transaction's lock, so two threads can both see pre-HOLD balances
3. If `amount_paise` were ever accidentally a Decimal or float (wrong migration), Python would silently produce fractional paise

### Fix Applied

Replaced with a single `aggregate()` call using `Sum + Case + When` as shown in Section 1. The entire balance computation runs inside PostgreSQL within the same `transaction.atomic()` block that holds the `SELECT FOR UPDATE` lock — making the read and the write perfectly serialized.

---

## 6. Retry Logic

The Celery task uses `self.retry(countdown=delay)` with exponential backoff:

| Attempt | Delay |
|---|---|
| 1st retry | 1 second |
| 2nd retry | 2 seconds |
| 3rd retry | 4 seconds |
| After 3rd | Force FAILED + RELEASE |

The force-fail path (`_force_fail_payout`) is also atomic — it creates the `RELEASE` ledger entry and updates status in the same transaction, so the merchant's funds are always unblocked even if the worker crashes mid-retry.

The Beat task (`recover_stale_payouts`) runs every 60 seconds and re-queues any payout that has been in `PROCESSING` for more than 30 seconds — covering the 10% simulated "stuck" outcome and real-world worker crashes.
