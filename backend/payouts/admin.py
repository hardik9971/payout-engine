from django.contrib import admin
from .models import Merchant, LedgerEntry, Payout, IdempotencyKey


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "created_at"]


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ["id", "merchant", "entry_type", "amount_paise", "payout_id", "created_at"]
    list_filter = ["entry_type"]


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ["id", "merchant", "amount_paise", "status", "retry_count", "created_at"]
    list_filter = ["status"]


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ["id", "merchant", "key", "status_code", "created_at", "expires_at"]
