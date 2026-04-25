# Payout Engine

A Django-based payout disbursement system built to handle the hard parts: double-spend prevention, concurrent balance checks, idempotent retries, and stuck-worker recovery.

This was built as a take-home project, but designed as if real merchant money is flowing through it.

---

## The Problem

Merchants accumulate a credit balance from sales. They request payouts to a bank account. The happy path is trivial — the hard part is:

- **Double disbursement** when a client retries a request that already succeeded
- **Race conditions** when two requests hit the same balance simultaneously
- **Stuck payouts** when a worker crashes between PENDING and COMPLETED
- **Frozen funds** when a HOLD is never released after a failure

A naive "check balance → create payout → debit" will fail under any of these. This engine handles all four.

---

## Architecture

```
Client (React)
    │  POST /api/v1/payouts/
    ▼
Django View
    ├── Idempotency check (DB slot, SELECT FOR UPDATE)
    ├── SELECT FOR UPDATE on Merchant row
    ├── Balance check (SUM+CASE in DB, same transaction)
    ├── Create Payout (PENDING) + HOLD ledger entry (atomic)
    └── Enqueue Celery task (after commit)
                │
                ▼
        Celery Worker
            ├── SELECT FOR UPDATE (nowait) on Payout row
            ├── PENDING → PROCESSING
            ├── Call gateway (simulated)
            └── PROCESSING → COMPLETED (DEBIT) or FAILED (RELEASE)
                        │
                        ▼
              Celery Beat (every 60s)
                └── Find PROCESSING payouts > 30s old
                    └── Re-queue or force-fail + RELEASE
```

---

## Key Decisions

### 1. Row-level locking instead of application-level locks

We `SELECT FOR UPDATE` the Merchant row before every balance check. Concurrent requests for the same merchant serialize at the database level.

**Why not a Redis distributed lock?** Redis locks have expiry edge cases. If a lock TTL expires while the holder is still in-flight (slow DB write), a second caller acquires the lock and you have two concurrent balance checks. PostgreSQL row locks tie lock lifetime to the transaction — exactly the boundary we need.

**Tradeoff:** One in-flight balance check per merchant at a time. At high request volume this becomes a bottleneck. The fix is per-merchant database sharding or a credit reservation queue, neither of which is here.

### 2. Idempotency keys in PostgreSQL, not Redis

Every `POST /api/v1/payouts/` requires an `Idempotency-Key: <uuid>` header. The key is stored as a DB row (slot). The slot commits in the **same transaction** as the Payout and HOLD entry — there is no window where one exists without the other.

If the slot exists but `response_body` is NULL, a prior request claimed it and died mid-flight. We re-claim it rather than returning 409 permanently — a transient crash shouldn't poison the key forever.

**Why not Redis?** We'd need a two-phase commit across Redis and PostgreSQL to get the same consistency guarantee. PostgreSQL gives us that for free.

**Tradeoff:** Every POST does an extra DB read. The index on `(merchant_id, key)` keeps it fast, but the table grows unboundedly. There's no cleanup job yet — expired slots are only pruned on read.

### 3. Append-only ledger, balance computed from entries

We never UPDATE or DELETE ledger rows. Balance is always `SUM(CASE WHEN entry_type = ...)` over all entries for a merchant.

**Why?** A stored balance field is a derived value that can diverge from the ledger if any write path is buggy. The ledger is the ground truth; computing from it means there's nothing to get out of sync.

**Tradeoff:** The aggregation gets more expensive as the ledger grows. At millions of rows per merchant, this needs balance snapshots (periodic checkpoint rows) plus delta summation. Not implemented here.

### 4. State machine in one place

```
PENDING → PROCESSING → COMPLETED
                     → FAILED
```

Every status change goes through `transition(payout, new_status)`. Invalid transitions raise `InvalidTransitionError`. Terminal states have empty allowed sets — a late-waking retry worker cannot accidentally re-complete a COMPLETED payout.

**Why not scattered if/elif guards?** One adjacency map is the single source of truth. As the codebase grows, you can't accidentally add a new status path by forgetting to update a check somewhere.

---

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/payouts/` | Create payout (requires `Idempotency-Key` header) |
| `GET` | `/api/v1/payouts/` | List all payouts for the merchant |
| `POST` | `/api/v1/payouts/<id>/retry/` | Operator retry for FAILED payouts |
| `GET` | `/api/v1/balance/` | Available + held balance |
| `GET` | `/api/v1/ledger/` | All ledger entries |

### POST /api/v1/payouts/

**Headers:**
```
Idempotency-Key: <uuid>
X-Merchant-ID: 1
Content-Type: application/json
```

**Body:**
```json
{ "amount_paise": 60000, "bank_account_id": "HDFC_XXXX1234" }
```

**Responses:**

| Code | Meaning |
|---|---|
| `201` | Payout created and queued |
| `400` | Insufficient balance or validation error |
| `409` | Concurrent request with same key in-flight |

### POST /api/v1/payouts/\<id\>/retry/

Operator-initiated re-queue for a FAILED payout. Creates a new Payout with the same amount and bank account. The original FAILED payout is preserved.

**Headers:**
```
X-Operator-Override: true
X-Merchant-ID: 1
```

**Responses:**

| Code | Meaning |
|---|---|
| `201` | Retry payout created and queued |
| `400` | Insufficient balance (original RELEASE may have been re-spent) |
| `403` | Missing X-Operator-Override header |
| `409` | Payout is not in FAILED status |

---

## Failure Scenarios

| Scenario | What Happens |
|---|---|
| Client retries same request | Idempotency slot returns the cached 201, no duplicate payout |
| Two concurrent requests, same balance | `SELECT FOR UPDATE` serializes them; second gets 400 |
| Worker crashes mid-PROCESSING | Beat detects > 30s stuck, re-queues up to `MAX_RETRIES` |
| Worker exhausts retries | `_mark_payout_failed_and_release_hold()` transitions to FAILED atomically and creates RELEASE |
| FAILED payout needs manual retry | `POST /api/v1/payouts/<id>/retry/` with `X-Operator-Override: true` |
| Beat and retry worker hit same stuck payout | `nowait=True` — one gets the lock, the other skips cleanly |

---

## Tradeoffs and Known Gaps

**Retry delays are capped at 3 attempts with [1, 2, 4] second backoff.** Celery 5.4+ supports `autoretry_for` with `exponential_backoff=True` which would be cleaner. We're on 5.3.6.

**The 30s stuck threshold can fire on slow-but-legitimate gateway calls.** If a bank takes 40s to respond, beat re-queues the payout while the original worker is still waiting. The `nowait` lock prevents double-execution, but the original worker wastes a retry attempt. Raising the threshold to 90s would reduce unnecessary re-queues.

**`python manage.py seed` adds credits on every run.** It's not idempotent on balance — running it twice gives the merchant two credits. That's intentional (merchants receive multiple credits in real life) but surprising for a seed command.

**Celery on Windows requires `--pool=solo`.** Windows doesn't support `fork()`, and `billiard`'s shared memory IPC fails with `PermissionError: [WinError 5]`. Solo pool runs tasks in the main process thread — fine for development, not for production.

**No webhook delivery for status updates.** The React dashboard polls every 3 seconds. Production systems should push status changes via webhooks to merchant backends and WebSocket to ops dashboards.

**Operator retry identity is not tracked.** The `/retry/` endpoint accepts `X-Operator-Override: true` but doesn't record which operator triggered it. In production this would be tied to an authenticated session.

---

## Future Improvements

- **Balance snapshots** to bound ledger aggregation cost as merchant history grows
- **Idempotency slot cleanup job** — currently slots only expire lazily on read, the table grows unboundedly
- **Webhook delivery** for payout status changes to merchant systems  
- **Per-merchant rate limiting** on the payout API to prevent runaway disbursement loops
- **Real gateway integration** — `_simulate_gateway_response()` is a stub; replace with a proper client that handles gateway timeouts, partial failures, and gateway-side idempotency
- **Operator audit log** — track who triggered manual retries and when

---

## Running Locally

### Prerequisites
- Python 3.11+, Node 18+, Docker + Docker Compose

### 1. Start infrastructure

```bash
docker-compose up -d
# PostgreSQL on localhost:5432, Redis on localhost:6379
```

### 2. Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r ../requirements.txt
python manage.py migrate
python manage.py seed        # creates Demo Merchant with Rs.1000 credit
python manage.py runserver
```

### 3. Celery worker + beat

```bash
# Note: --pool=solo is required on Windows
celery -A payout_engine worker -l info --pool=solo

# In a separate terminal:
celery -A payout_engine beat -l info
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```

### 5. Run tests

```bash
cd backend
python manage.py test payouts.tests -v 2
```

---

## Project Structure

```
playto/
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── backend/
│   ├── manage.py
│   ├── payout_engine/
│   │   ├── settings.py
│   │   ├── celery.py
│   │   └── urls.py
│   └── payouts/
│       ├── models.py          # Merchant, LedgerEntry, Payout, IdempotencyKey
│       ├── balance.py         # SUM+CASE balance query, WHY no stored balance field
│       ├── state_machine.py   # Transition enforcement, WHY adjacency map
│       ├── idempotency.py     # Slot claim/settle, WHY DB not Redis
│       ├── serializers.py
│       ├── views.py           # PayoutListCreateView, OperatorRetryView
│       ├── tasks.py           # execute_payout_task, requeue_stuck_payouts
│       ├── tests.py           # Concurrency, idempotency, state machine, ledger tests
│       └── management/commands/seed.py
└── frontend/
    └── src/
        ├── App.jsx            # Root with 3s polling
        ├── api.js
        └── components/
            ├── BalanceCard.jsx
            ├── PayoutForm.jsx
            ├── PayoutHistory.jsx
            └── LedgerTable.jsx
```

---

## Money Rules

- All amounts are **integers in paise** (`BigIntegerField`). No floats anywhere.
- Balance is **never computed in Python** — always via `SUM+CASE` DB aggregation inside the same transaction that holds the row lock.
- `LedgerEntry` is **append-only** — never updated or deleted.
- State transitions go through `transition()` — direct `payout.status = ...` assignments are not used.
