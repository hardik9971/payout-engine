"""
Balance query for a merchant's ledger.

Balance is ALWAYS derived from the ledger via a single DB aggregation.
We never store a running balance field on the Merchant row. Reasons:

  1. A stored balance field would need to be updated atomically with every
     ledger write. Under concurrent requests this creates a write-write conflict
     that's harder to reason about than the read-then-write we already have.

  2. The ledger is the ground truth. If there's ever a discrepancy between a
     stored balance and what the ledger says, you have to trust one — and it
     should be the ledger. Computing from it avoids that ambiguity.

  3. We can reconstruct the balance at any point in time by filtering the
     ledger by created_at. A stored field doesn't give you that.

Tradeoff: as the ledger grows, this aggregation gets more expensive.
At ~10M rows per merchant the query starts taking hundreds of milliseconds.
The standard fix is periodic balance snapshots (a checkpoint CREDIT row) plus
summing only the delta since the last checkpoint. Not implemented here.

TODO: If read traffic on /api/v1/balance/ becomes a bottleneck, cache the
      result in Redis with a short TTL (e.g. 1s). Invalidate on every ledger
      write. The cache can serve stale-by-1s reads; the DB is still the source
      of truth for the write path.
"""
from django.db.models import BigIntegerField, Case, ExpressionWrapper, F, Sum, When

from .models import LedgerEntry, Merchant


def get_balance(merchant: Merchant) -> tuple[int, int]:
    """
    Return (available_paise, held_paise) for the merchant.

    Runs one SQL query using conditional SUM so the result is consistent with
    the transaction's current snapshot. Must be called inside the same
    transaction.atomic() block that holds the SELECT FOR UPDATE lock on the
    merchant row, otherwise a concurrent HOLD could be created between the
    balance read and the balance check.

    Returns (0, 0) when the ledger has no entries yet.
    """
    result = LedgerEntry.objects.filter(merchant=merchant).aggregate(
        available_paise=Sum(
            Case(
                When(entry_type=LedgerEntry.EntryType.CREDIT, then="amount_paise"),
                When(
                    entry_type=LedgerEntry.EntryType.DEBIT,
                    then=ExpressionWrapper(F("amount_paise") * -1, output_field=BigIntegerField()),
                ),
                When(
                    entry_type=LedgerEntry.EntryType.HOLD,
                    then=ExpressionWrapper(F("amount_paise") * -1, output_field=BigIntegerField()),
                ),
                When(entry_type=LedgerEntry.EntryType.RELEASE, then="amount_paise"),
                default=0,
                output_field=BigIntegerField(),
            )
        ),
        held_paise=Sum(
            Case(
                When(entry_type=LedgerEntry.EntryType.HOLD, then="amount_paise"),
                When(
                    entry_type=LedgerEntry.EntryType.RELEASE,
                    then=ExpressionWrapper(F("amount_paise") * -1, output_field=BigIntegerField()),
                ),
                default=0,
                output_field=BigIntegerField(),
            )
        ),
    )

    # SUM on an empty set returns None, not 0
    return result["available_paise"] or 0, result["held_paise"] or 0
