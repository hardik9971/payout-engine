"""
Seed command: create a merchant and seed initial credit balance.

Usage:
    python manage.py seed
    python manage.py seed --amount 500000  # ₹5000 in paise
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from payouts.models import LedgerEntry, Merchant


class Command(BaseCommand):
    help = "Seed a demo merchant with initial credit balance."

    def add_arguments(self, parser):
        parser.add_argument(
            "--amount",
            type=int,
            default=100000,  # ₹1000 = 100,000 paise
            help="Initial credit in paise (default: 100000 = ₹1000)",
        )
        parser.add_argument(
            "--name",
            type=str,
            default="Demo Merchant",
            help='Merchant name (default: "Demo Merchant")',
        )

    def handle(self, *args, **options):
        amount_paise = options["amount"]
        name = options["name"]

        with transaction.atomic():
            merchant, created = Merchant.objects.get_or_create(name=name)

            if created:
                self.stdout.write(self.style.SUCCESS(f"Created merchant: {merchant.name} (id={merchant.pk})"))
            else:
                self.stdout.write(f"Merchant already exists: {merchant.name} (id={merchant.pk})")

            LedgerEntry.objects.create(
                merchant=merchant,
                entry_type=LedgerEntry.EntryType.CREDIT,
                amount_paise=amount_paise,
                payout=None,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded Rs.{amount_paise / 100:.2f} credit to {merchant.name}"
            )
        )
