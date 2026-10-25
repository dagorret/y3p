"""Prueba pacing Moodle y mapeo de errores JSON estructurados."""

import unittest
from unittest.mock import Mock, patch

from apps.integrations.exceptions import AuthenticationError
from apps.integrations.services import moodle as moodle_module
from apps.integrations.services.moodle import MoodleRestClient


class MoodleRateLimitAndErrorTests(unittest.TestCase):
    """Comprueba el intervalo mínimo y que errorcode controla la excepción."""

    def setUp(self):
        """Aísla el reloj de rate limit entre pruebas."""

        MoodleRestClient._last_request_started = None

    def test_requests_are_spaced_and_json_auth_error_is_typed(self):
        """Dos llamadas se separan 0.4s y invalidtoken se vuelve AuthenticationError."""

        success = Mock(status_code=200)
        success.json.return_value = {"sitename": "FCE Moodle"}
        auth_error = Mock(status_code=200)
        auth_error.json.return_value = {
            "exception": "moodle_exception",
            "errorcode": "invalidtoken",
            "message": "Invalid token",
        }
        session = Mock()
        session.post.side_effect = [success, auth_error]
        client = MoodleRestClient(
            base_url="https://moodle.example.edu",
            token="test-token",
            session=session,
        )

        with (
            patch.object(moodle_module.time, "monotonic", side_effect=[10.0, 10.0, 10.1, 10.4]),
            patch.object(moodle_module.time, "sleep") as sleep,
        ):
            self.assertEqual(client.check_connection()["sitename"], "FCE Moodle")
            with self.assertRaises(AuthenticationError):
                client.check_connection()

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.3)
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(
            [call_args.kwargs["timeout"] for call_args in session.post.call_args_list],
            [10.0, 10.0],
        )

    def test_already_enrolled_errorcode_is_idempotent_success(self):
        """Solo el errorcode conocido useralreadyenrolled satisface la matrícula."""

        response = Mock(status_code=200)
        response.json.return_value = {
            "exception": "moodle_exception",
            "errorcode": "useralreadyenrolled",
            "message": "An unrelated localized message",
        }
        session = Mock()
        session.post.return_value = response
        client = MoodleRestClient(
            base_url="https://moodle.example.edu",
            token="test-token",
            session=session,
        )

        result = client.enrol_user(12, 34)

        self.assertTrue(result.success)
        self.assertFalse(result.simulated)
        self.assertEqual(result.user_id, 12)
        session.post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
