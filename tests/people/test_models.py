"""Persistencia e integridad de cuentas digitales por plataforma."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.people.identifiers import calculate_content_hash
from apps.people.models import DigitalAccount, Identity


class DigitalAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.identity = Identity.objects.create(
            category=Identity.Category.FISICA, identifier="12345678",
            first_name="Ana", last_name="Pérez",
        )
        cls.other_identity = Identity.objects.create(
            category=Identity.Category.FISICA, identifier="23456789",
            first_name="Eva", last_name="López",
        )

    def create_account(self, **overrides):
        values = {
            "identity": self.identity,
            "platform": DigitalAccount.Platform.TEAMS,
            "account_identifier": "ana@eco.unrc.edu.ar",
        }
        values.update(overrides)
        return DigitalAccount.objects.create(**values)

    def test_creation_defaults_and_reverse_relation(self):
        account = self.create_account()
        account.refresh_from_db()
        self.assertIsInstance(account.pk, int)
        self.assertTrue(account.is_active)
        self.assertIsNone(account.content_hash)
        self.assertIsNotNone(account.created_at)
        self.assertIsNotNone(account.updated_at)
        self.assertEqual(account.identity, self.identity)
        self.assertEqual(list(self.identity.digital_accounts.all()), [account])

    def test_hash_and_activation_can_be_updated(self):
        account = self.create_account()
        previous_updated_at = account.updated_at
        account.content_hash = calculate_content_hash({"name": "Ana"})
        account.is_active = False
        account.save()
        account.refresh_from_db()
        self.assertEqual(account.content_hash, calculate_content_hash({"name": "Ana"}))
        self.assertFalse(account.is_active)
        self.assertGreater(account.updated_at, previous_updated_at)

    def test_account_is_unique_per_platform_even_across_identities(self):
        self.create_account()
        for identity in (self.identity, self.other_identity):
            with self.subTest(identity=identity.pk):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.create_account(identity=identity)

    def test_same_identifier_is_allowed_on_different_platforms(self):
        for platform in DigitalAccount.Platform.values:
            self.create_account(platform=platform)
        self.assertEqual(self.identity.digital_accounts.count(), 5)

    def test_identity_can_have_multiple_accounts_on_one_platform(self):
        self.create_account()
        self.create_account(account_identifier="ana.alternativa@eco.unrc.edu.ar")
        self.assertEqual(self.identity.digital_accounts.count(), 2)

    def test_deleting_identity_cascades_only_its_accounts(self):
        account = self.create_account()
        other_account = self.create_account(
            identity=self.other_identity, account_identifier="eva@eco.unrc.edu.ar"
        )
        self.identity.delete()
        self.assertFalse(DigitalAccount.objects.filter(pk=account.pk).exists())
        self.assertTrue(DigitalAccount.objects.filter(pk=other_account.pk).exists())
