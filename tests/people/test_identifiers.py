"""Contrato del layout UUIDv8 institucional, sin acceso a base de datos."""

import hashlib
import uuid

from django.test import SimpleTestCase

from apps.people.identifiers import (
    calculate_content_hash,
    decode_institutional_uuid,
    generate_institutional_uuid,
)


class ContentHashTests(SimpleTestCase):
    def test_key_order_does_not_change_hash(self):
        self.assertEqual(
            calculate_content_hash({"name": "Ana", "active": True}),
            calculate_content_hash({"active": True, "name": "Ana"}),
        )

    def test_strips_text_without_changing_internal_spaces(self):
        self.assertEqual(
            calculate_content_hash({"name": " \tAna María\n"}),
            calculate_content_hash({"name": "Ana María"}),
        )
        self.assertNotEqual(
            calculate_content_hash({"name": "Ana  María"}),
            calculate_content_hash({"name": "Ana María"}),
        )

    def test_ascii_json_sha256_contract(self):
        expected = hashlib.sha256(b'{"active": true, "name": "Jos\\u00e9"}').hexdigest()
        self.assertEqual(calculate_content_hash({"name": "José", "active": True}), expected)
        self.assertRegex(expected, r"^[0-9a-f]{64}$")

    def test_nested_values_are_normalized_without_mutating_input(self):
        data = {"details": {"name": " Ana "}, "aliases": [" A ", None, 1]}
        self.assertEqual(
            calculate_content_hash(data),
            calculate_content_hash({"aliases": ["A", None, 1], "details": {"name": "Ana"}}),
        )
        self.assertEqual(data, {"details": {"name": " Ana "}, "aliases": [" A ", None, 1]})

    def test_changes_in_content_types_and_list_order_change_hash(self):
        for left, right in (("Ana", "Eva"), (1, "1"), (False, None), ([1, 2], [2, 1])):
            with self.subTest(left=left, right=right):
                self.assertNotEqual(
                    calculate_content_hash({"value": left}),
                    calculate_content_hash({"value": right}),
                )

    def test_requires_dictionary(self):
        with self.assertRaises(TypeError):
            calculate_content_hash([])


class InstitutionalUUIDTests(SimpleTestCase):
    def test_canonical_known_vector(self):
        self.assertEqual(
            str(generate_institutional_uuid("12345678", 2026)),
            "fce00000-0000-87ea-9011-000000bc614e",
        )

    def test_determinism_and_numeric_input_normalization(self):
        expected = generate_institutional_uuid("12345678", 2026)
        for dni in ("12345678", 12345678, " 0012345678 "):
            with self.subTest(dni=dni):
                self.assertEqual(generate_institutional_uuid(dni, 2026), expected)

    def test_distinct_inputs_have_distinct_identifiers(self):
        inputs = (
            ("12345678", 2026, 1, 1),
            ("12345679", 2026, 1, 1),
            ("12345678", 2027, 1, 1),
            ("12345678", 2026, 2, 1),
            ("12345678", 2026, 1, 2),
        )
        self.assertEqual(len({generate_institutional_uuid(*row) for row in inputs}), 5)

    def test_version_and_rfc_variant_bits(self):
        value = generate_institutional_uuid("12345678", 2026)
        self.assertEqual(value.version, 8)
        self.assertEqual(value.variant, uuid.RFC_4122)
        self.assertEqual(value.bytes[6] >> 4, 0b1000)
        self.assertEqual(value.bytes[8] >> 6, 0b10)
        self.assertEqual(value.bytes[8], 0x90)

    def test_decode_reconstructs_all_fields(self):
        value = uuid.UUID("fce00000-0000-87ea-90af-000000bc614e")
        self.assertEqual(
            decode_institutional_uuid(value),
            {"prefix": "fce", "year": 2026, "origin_code": 10,
             "profile_type_code": 15, "dni": "12345678"},
        )
        self.assertEqual(generate_institutional_uuid("12345678", 2026, 10, 15), value)

    def test_field_boundaries_round_trip(self):
        for dni, year, origin, profile in ((0, 0, 0, 0), ((1 << 48) - 1, 4095, 15, 15)):
            with self.subTest(dni=dni):
                value = generate_institutional_uuid(dni, year, origin, profile)
                self.assertEqual(decode_institutional_uuid(value), {
                    "prefix": "fce", "year": year, "origin_code": origin,
                    "profile_type_code": profile, "dni": str(dni),
                })

    def test_invalid_dni_is_rejected(self):
        for dni in ("", " ", "12.345.678", "12a", "-1", -1, "+1", "١٢", 1 << 48):
            with self.subTest(dni=dni), self.assertRaises(ValueError):
                generate_institutional_uuid(dni, 2026)
        for dni in (None, True, 123.0):
            with self.subTest(dni=dni), self.assertRaises(TypeError):
                generate_institutional_uuid(dni, 2026)

    def test_invalid_metadata_is_rejected(self):
        for field, maximum in (("year", 4095), ("origin_code", 15), ("profile_type_code", 15)):
            for invalid in (-1, maximum + 1, True, "1", 1.0):
                args = {"dni": "12345678", "year": 2026, field: invalid}
                error = TypeError if isinstance(invalid, (bool, str, float)) else ValueError
                with self.subTest(field=field, value=invalid), self.assertRaises(error):
                    generate_institutional_uuid(**args)

    def test_decode_rejects_noncanonical_layouts(self):
        for text in (
            "ace00000-0000-87ea-9011-000000bc614e",
            "fce00000-0001-87ea-9011-000000bc614e",
            "fce00000-0000-47ea-9011-000000bc614e",
            "fce00000-0000-87ea-7011-000000bc614e",
            "fce00000-0000-87ea-8011-000000bc614e",
            "fce00000-0000-87ea-9110-000000bc614e",
        ):
            with self.subTest(uuid=text), self.assertRaises(ValueError):
                decode_institutional_uuid(uuid.UUID(text))

    def test_decode_requires_uuid_instance(self):
        with self.assertRaises(TypeError):
            decode_institutional_uuid("fce00000-0000-87ea-9011-000000bc614e")
