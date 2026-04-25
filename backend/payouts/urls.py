from django.urls import path
from .views import BalanceView, LedgerView, OperatorRetryView, PayoutListCreateView

urlpatterns = [
    path("payouts/", PayoutListCreateView.as_view(), name="payouts"),
    path("payouts/<int:payout_id>/retry/", OperatorRetryView.as_view(), name="payout-operator-retry"),
    path("balance/", BalanceView.as_view(), name="balance"),
    path("ledger/", LedgerView.as_view(), name="ledger"),
]
