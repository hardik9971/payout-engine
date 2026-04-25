"""
API views for the payout engine.

POST /api/v1/payouts/ critical path:
  1. Validate request body
  2. Require Idempotency-Key header
  3. Resolve merchant from X-Merchant-ID header
  4. Open one atomic transaction:
     a. claim_idempotency_slot() — SELECT FOR UPDATE serializes concurrent
        requests with the same key before any balance check happens
     b. If slot is already settled, replay the cached response
     c. SELECT FOR UPDATE on Merchant row — serializes concurrent balance checks
        across DIFFERENT keys for the same merchant
     d. Balance check via DB aggregation (never Python-level sum)
     e. Reject with 400 if insufficient; store failure in slot
     f. Create Payout (PENDING) + HOLD ledger entry
     g. Store 201 response in slot
     h. Commit — Payout, HOLD, and IdempotencyKey slot commit atomically
  5. Enqueue Celery task AFTER commit so the worker sees the committed rows
  6. Return 201

Concurrency note: steps 4c–4h are serialized per-merchant by the row lock.
This is intentional — we'd rather have correct sequential execution than a
race condition that allows double-spend. The throughput cost is one in-flight
balance check per merchant at a time.

TODO: Add per-merchant rate limiting. A runaway client can monopolize the
      merchant row lock and block all other legitimate requests.
"""
import uuid

from django.db import IntegrityError, transaction
from django.http import HttpRequest
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .balance import get_balance
from .idempotency import claim_idempotency_slot, settle_idempotency_slot
from .models import LedgerEntry, Merchant, Payout
from .serializers import (
    BalanceSerializer,
    LedgerEntrySerializer,
    PayoutCreateSerializer,
    PayoutSerializer,
)
from .tasks import execute_payout_task


def _get_merchant_from_request(request: HttpRequest) -> Merchant | None:
    """
    Resolve the Merchant for this request.

    Reads X-Merchant-ID header; falls back to the first merchant in the DB
    so the demo works without setting headers manually.

    In a real multi-tenant system this would be tied to authentication
    (e.g. API key → merchant lookup) rather than a header we trust from the client.
    """
    merchant_id = request.headers.get("X-Merchant-ID")
    if merchant_id:
        return Merchant.objects.get(pk=merchant_id)
    return Merchant.objects.order_by("id").first()


class PayoutListCreateView(APIView):
    """
    GET  /api/v1/payouts/  — list payouts for the merchant (newest first)
    POST /api/v1/payouts/  — create a payout (requires Idempotency-Key header)
    """

    def get(self, request: HttpRequest) -> Response:
        merchant = _get_merchant_from_request(request)
        if merchant is None:
            return Response([])
        payouts = Payout.objects.filter(merchant=merchant).order_by("-created_at")
        return Response(PayoutSerializer(payouts, many=True).data)

    def post(self, request: HttpRequest) -> Response:
        body = PayoutCreateSerializer(data=request.data)
        if not body.is_valid():
            return Response(body.errors, status=status.HTTP_400_BAD_REQUEST)

        amount_paise: int = body.validated_data["amount_paise"]
        bank_account_id: str = body.validated_data["bank_account_id"]

        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return Response(
                {"error": "Idempotency-Key header is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            merchant = _get_merchant_from_request(request)
        except Merchant.DoesNotExist:
            return Response({"error": "Merchant not found."}, status=status.HTTP_404_NOT_FOUND)

        if merchant is None:
            return Response(
                {"error": "No merchants exist. Run: python manage.py seed"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            with transaction.atomic():
                slot, is_new_claim = claim_idempotency_slot(merchant, idempotency_key)

                if not is_new_claim:
                    if slot.response_body is not None:
                        # A prior request already committed — replay the stored response.
                        return Response(slot.response_body, status=slot.status_code)
                    # The slot exists but response_body is NULL: a concurrent request
                    # claimed the slot and then rolled back. We now hold the row lock,
                    # so we tell the caller to retry with the same key.
                    return Response(
                        {"error": "Concurrent request with same idempotency key in progress."},
                        status=status.HTTP_409_CONFLICT,
                    )

                # Lock the merchant row so concurrent requests for different idempotency
                # keys don't race on the balance check. Without this, two requests could
                # both read the same available balance and both pass the sufficiency check.
                merchant = Merchant.objects.select_for_update().get(pk=merchant.pk)
                available_paise, _ = get_balance(merchant)

                if available_paise < amount_paise:
                    error_response = {
                        "error": "Insufficient balance.",
                        "available_paise": available_paise,
                        "requested_paise": amount_paise,
                    }
                    settle_idempotency_slot(slot, error_response, 400)
                    return Response(error_response, status=status.HTTP_400_BAD_REQUEST)

                payout = Payout.objects.create(
                    merchant=merchant,
                    amount_paise=amount_paise,
                    bank_account_id=bank_account_id,
                    idempotency_key=idempotency_key,
                    status=Payout.Status.PENDING,
                )
                LedgerEntry.objects.create(
                    merchant=merchant,
                    entry_type=LedgerEntry.EntryType.HOLD,
                    amount_paise=amount_paise,
                    payout=payout,
                )
                response_data = PayoutSerializer(payout).data
                settle_idempotency_slot(slot, response_data, 201)

        except IntegrityError:
            # The unique constraint on (merchant, idempotency_key) fired — a concurrent
            # request with the same key won the race to create the Payout row.
            return Response(
                {"error": "Concurrent request with same idempotency key in progress."},
                status=status.HTTP_409_CONFLICT,
            )

        # Enqueue AFTER the transaction commits so the worker is guaranteed to
        # find the Payout and HOLD rows. Enqueueing inside the transaction would
        # let the worker start before the rows are visible.
        execute_payout_task.delay(payout.pk)
        return Response(response_data, status=status.HTTP_201_CREATED)


class BalanceView(APIView):
    """GET /api/v1/balance/ — available and held balance in paise and rupees."""

    def get(self, request: HttpRequest) -> Response:
        try:
            merchant = _get_merchant_from_request(request)
        except Merchant.DoesNotExist:
            return Response({"error": "Merchant not found."}, status=status.HTTP_404_NOT_FOUND)

        if merchant is None:
            return Response(
                {"available_paise": 0, "held_paise": 0,
                 "available_rupees": "Rs.0.00", "held_rupees": "Rs.0.00"}
            )

        available_paise, held_paise = get_balance(merchant)
        return Response(
            BalanceSerializer({"available_paise": available_paise, "held_paise": held_paise}).data
        )


class LedgerView(APIView):
    """GET /api/v1/ledger/ — all ledger entries for the merchant, newest first."""

    def get(self, request: HttpRequest) -> Response:
        try:
            merchant = _get_merchant_from_request(request)
        except Merchant.DoesNotExist:
            return Response({"error": "Merchant not found."}, status=status.HTTP_404_NOT_FOUND)

        if merchant is None:
            return Response([])

        entries = LedgerEntry.objects.filter(merchant=merchant).order_by("-created_at")
        return Response(LedgerEntrySerializer(entries, many=True).data)


class OperatorRetryView(APIView):
    """
    POST /api/v1/payouts/<payout_id>/retry/

    Operator-initiated re-queue for FAILED payouts.

    When a payout fails after exhausting automatic retries, this endpoint lets
    ops manually trigger a fresh attempt. Rather than mutating the FAILED payout
    (which would break the audit trail), we create a NEW Payout record with the
    same amount and bank account. The original FAILED payout is preserved as-is.

    The original FAILED payout had a RELEASE entry that returned the held funds
    to available. So we check balance again before creating the new HOLD — the
    merchant might have spent those funds in the meantime.

    Requires X-Operator-Override: true to prevent accidental POSTs. In a real
    system this would be tied to an internal ops role, not a plain header.

    TODO: Track which operator triggered the retry (currently anonymous).
    TODO: Rate-limit retries per payout to prevent hammer loops from ops tooling.
    """

    def post(self, request: HttpRequest, payout_id: int) -> Response:
        if request.headers.get("X-Operator-Override") != "true":
            return Response(
                {"error": "X-Operator-Override: true header is required for manual retries."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            merchant = _get_merchant_from_request(request)
        except Merchant.DoesNotExist:
            return Response({"error": "Merchant not found."}, status=status.HTTP_404_NOT_FOUND)

        if merchant is None:
            return Response({"error": "No merchant."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        try:
            original = Payout.objects.get(pk=payout_id, merchant=merchant)
        except Payout.DoesNotExist:
            return Response({"error": "Payout not found."}, status=status.HTTP_404_NOT_FOUND)

        if original.status != Payout.Status.FAILED:
            return Response(
                {"error": f"Only FAILED payouts can be retried. Current status: {original.status}"},
                status=status.HTTP_409_CONFLICT,
            )

        # Each operator retry is a distinct payout transaction with its own idempotency key.
        new_idempotency_key = str(uuid.uuid4())

        try:
            with transaction.atomic():
                merchant = Merchant.objects.select_for_update().get(pk=merchant.pk)
                available_paise, _ = get_balance(merchant)

                if available_paise < original.amount_paise:
                    return Response(
                        {
                            "error": "Insufficient balance for retry. The original RELEASE returned "
                                     "the funds, but they may have been used for another payout.",
                            "available_paise": available_paise,
                            "required_paise": original.amount_paise,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                retry_payout = Payout.objects.create(
                    merchant=merchant,
                    amount_paise=original.amount_paise,
                    bank_account_id=original.bank_account_id,
                    idempotency_key=new_idempotency_key,
                    status=Payout.Status.PENDING,
                )
                LedgerEntry.objects.create(
                    merchant=merchant,
                    entry_type=LedgerEntry.EntryType.HOLD,
                    amount_paise=original.amount_paise,
                    payout=retry_payout,
                )
        except IntegrityError:
            return Response(
                {"error": "Concurrent retry request in progress."},
                status=status.HTTP_409_CONFLICT,
            )

        execute_payout_task.delay(retry_payout.pk)

        return Response(
            {
                "original_payout_id": original.pk,
                "retry_payout": PayoutSerializer(retry_payout).data,
            },
            status=status.HTTP_201_CREATED,
        )
