"""
DRF serializers for the payouts app.
"""
from rest_framework import serializers

from .models import LedgerEntry, Payout


def _paise_to_rupees(paise: int) -> str:
    """Format an integer paise amount as a display string (e.g. '₹6.00')."""
    return f"₹{paise / 100:.2f}"


class PayoutCreateSerializer(serializers.Serializer):
    """Validates the POST /api/v1/payouts/ request body."""

    amount_paise = serializers.IntegerField(min_value=1)
    bank_account_id = serializers.CharField(max_length=255, allow_blank=False)


class PayoutSerializer(serializers.ModelSerializer):
    """Read-only serializer for Payout responses."""

    amount_rupees = serializers.SerializerMethodField()

    class Meta:
        model = Payout
        fields = [
            "id",
            "amount_paise",
            "amount_rupees",
            "status",
            "idempotency_key",
            "bank_account_id",
            "retry_count",
            "created_at",
            "updated_at",
        ]

    def get_amount_rupees(self, obj: Payout) -> str:
        return _paise_to_rupees(obj.amount_paise)


class LedgerEntrySerializer(serializers.ModelSerializer):
    """Read-only serializer for ledger entries."""

    amount_rupees = serializers.SerializerMethodField()

    class Meta:
        model = LedgerEntry
        fields = [
            "id",
            "entry_type",
            "amount_paise",
            "amount_rupees",
            "payout_id",
            "created_at",
        ]

    def get_amount_rupees(self, obj: LedgerEntry) -> str:
        return _paise_to_rupees(obj.amount_paise)


class BalanceSerializer(serializers.Serializer):
    """Serializer for GET /api/v1/balance/ responses."""

    available_paise = serializers.IntegerField()
    held_paise = serializers.IntegerField()
    available_rupees = serializers.SerializerMethodField()
    held_rupees = serializers.SerializerMethodField()

    def get_available_rupees(self, obj: dict[str, int]) -> str:
        return _paise_to_rupees(obj["available_paise"])

    def get_held_rupees(self, obj: dict[str, int]) -> str:
        return _paise_to_rupees(obj["held_paise"])
