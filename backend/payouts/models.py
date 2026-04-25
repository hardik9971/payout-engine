"""
Payout Engine — Data Models

Invariants:
  - LedgerEntry is APPEND-ONLY. Never UPDATE or DELETE rows.
  - All monetary amounts are integers in paise (1 ₹ = 100 paise).
  - Balances are derived exclusively via DB aggregation (see balance.py).
"""
from django.db import models
from django.utils import timezone


class Merchant(models.Model):
    """A business entity with a ledger balance."""

    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name

    class Meta:
        db_table = "merchants"


class LedgerEntry(models.Model):
    """
    Append-only financial ledger.

    Entry types and their effect on balance:
      CREDIT  → +available  (funds deposited)
      DEBIT   → -available  (funds disbursed after payout succeeds)
      HOLD    → -available  (funds reserved when payout is created)
      RELEASE → +available  (reservation lifted when payout fails)
    """

    class EntryType(models.TextChoices):
        CREDIT = "CREDIT", "Credit"
        DEBIT = "DEBIT", "Debit"
        HOLD = "HOLD", "Hold"
        RELEASE = "RELEASE", "Release"

    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="ledger_entries")
    entry_type = models.CharField(max_length=10, choices=EntryType.choices)
    amount_paise = models.BigIntegerField()
    # Null for CREDIT entries that are not tied to a specific payout.
    payout = models.ForeignKey(
        "Payout",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.entry_type} ₹{self.amount_paise / 100:.2f} [{self.merchant}]"

    class Meta:
        db_table = "ledger_entries"
        indexes = [
            models.Index(fields=["merchant", "entry_type"], name="ledger_merchant_type_idx"),
        ]


class Payout(models.Model):
    """
    A single payout request.

    Valid state transitions (enforced in state_machine.py):
      PENDING → PROCESSING → COMPLETED
      PENDING → PROCESSING → FAILED
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="payouts")
    amount_paise = models.BigIntegerField()
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    idempotency_key = models.CharField(max_length=255, db_index=True)
    bank_account_id = models.CharField(max_length=255)
    retry_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Payout #{self.pk} [{self.status}] ₹{self.amount_paise / 100:.2f}"

    class Meta:
        db_table = "payouts"
        unique_together = [("merchant", "idempotency_key")]
        indexes = [
            # Supports stale-payout recovery query: status=PROCESSING + processing_started_at
            models.Index(fields=["status", "processing_started_at"], name="payout_status_started_idx"),
        ]


class IdempotencyKey(models.Model):
    """
    Stores the response for a previous payout request.

    Replaying the same Idempotency-Key returns the cached response without
    re-executing business logic. Expires after 24 hours.

    response_body=None means a request has claimed the slot but has not yet
    committed its response (slot is in-progress). See idempotency.py.
    """

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name="idempotency_keys")
    key = models.CharField(max_length=255)
    response_body = models.JSONField(null=True, blank=True)
    status_code = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    class Meta:
        db_table = "idempotency_keys"
        unique_together = [("merchant", "key")]
        # unique_together already creates an index on (merchant, key);
        # the explicit Index below would be a duplicate — removed.
