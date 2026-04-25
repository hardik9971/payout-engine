"""
Migration 0003: Add composite index on (status, processing_started_at).

Used by recover_stale_payouts() which filters:
  status='PROCESSING' AND processing_started_at < stale_cutoff

Without this index that query does a full table scan as the payouts table grows.
Also removes the duplicate explicit Index on IdempotencyKey(merchant, key) —
the unique_together constraint already creates that index.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payouts", "0002_idempotency_nullable_response"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="payout",
            index=models.Index(
                fields=["status", "processing_started_at"],
                name="payout_status_started_idx",
            ),
        ),
        migrations.RemoveIndex(
            model_name="idempotencykey",
            name="idempotency_merchan_idx",
        ),
        migrations.RenameIndex(
            model_name="ledgerentry",
            old_name="ledger_entr_merchan_idx",
            new_name="ledger_merchant_type_idx",
        ),
    ]
