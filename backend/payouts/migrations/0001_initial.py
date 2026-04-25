"""
Initial migration for payouts app.
Auto-generated but manually reviewed for fintech correctness:
- BigIntegerField for all monetary amounts (amount_paise)
- PROTECT on FKs to prevent accidental cascade deletes of ledger records
- Unique constraints on (merchant, idempotency_key) and (merchant, key)
"""
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Merchant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "merchants"},
        ),
        migrations.CreateModel(
            name="Payout",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "merchant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payouts",
                        to="payouts.merchant",
                    ),
                ),
                ("amount_paise", models.BigIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("PROCESSING", "Processing"),
                            ("COMPLETED", "Completed"),
                            ("FAILED", "Failed"),
                        ],
                        db_index=True,
                        default="PENDING",
                        max_length=15,
                    ),
                ),
                ("idempotency_key", models.CharField(db_index=True, max_length=255)),
                ("bank_account_id", models.CharField(max_length=255)),
                ("retry_count", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("processing_started_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "payouts"},
        ),
        migrations.CreateModel(
            name="LedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "merchant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ledger_entries",
                        to="payouts.merchant",
                    ),
                ),
                (
                    "entry_type",
                    models.CharField(
                        choices=[
                            ("CREDIT", "Credit"),
                            ("DEBIT", "Debit"),
                            ("HOLD", "Hold"),
                            ("RELEASE", "Release"),
                        ],
                        max_length=10,
                    ),
                ),
                ("amount_paise", models.BigIntegerField()),
                (
                    "payout",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ledger_entries",
                        to="payouts.payout",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "ledger_entries"},
        ),
        migrations.CreateModel(
            name="IdempotencyKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "merchant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="idempotency_keys",
                        to="payouts.merchant",
                    ),
                ),
                ("key", models.CharField(max_length=255)),
                ("response_body", models.JSONField()),
                ("status_code", models.IntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
            ],
            options={"db_table": "idempotency_keys"},
        ),
        migrations.AddIndex(
            model_name="ledgerentry",
            index=models.Index(fields=["merchant", "entry_type"], name="ledger_entr_merchan_idx"),
        ),
        migrations.AddIndex(
            model_name="idempotencykey",
            index=models.Index(fields=["merchant", "key"], name="idempotency_merchan_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="payout",
            unique_together={("merchant", "idempotency_key")},
        ),
        migrations.AlterUniqueTogether(
            name="idempotencykey",
            unique_together={("merchant", "key")},
        ),
    ]
