"""
Migration 0002: Make IdempotencyKey.response_body nullable.

Required by the post-audit idempotency design where a slot is first created
with response_body=None (in-progress), then populated after the payout and
HOLD entry are committed. The nullable field is the "lock token" — a slot
with response_body=NULL is claimed but not yet settled.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payouts", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="idempotencykey",
            name="response_body",
            field=models.JSONField(null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="idempotencykey",
            name="status_code",
            field=models.IntegerField(default=0),
        ),
    ]
