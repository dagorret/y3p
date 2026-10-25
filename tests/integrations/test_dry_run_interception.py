"""Prueba que dry-run evita mutaciones HTTP y SMTP en todos los clientes."""

import unittest
from unittest.mock import Mock, patch

from apps.integrations.services.graph import MicrosoftGraphClient
from apps.integrations.services.mailer import MailerClient
from apps.integrations.services.moodle import MoodleRestClient
from apps.integrations.services.sial import SialClient


class DryRunInterceptionTests(unittest.TestCase):
    """Comprueba el bypass H-03 sin abrir conexiones ni depender de servicios."""

    def test_mutations_are_simulated_before_http_or_smtp(self):
        """Cada escritura retorna resultado simulado y no invoca transporte externo."""

        graph = MicrosoftGraphClient(dry_run=True)
        moodle = MoodleRestClient(dry_run=True)
        sial = SialClient(dry_run=True)
        smtp_factory = Mock()
        smtp_ssl_factory = Mock()
        mailer = MailerClient(dry_run=True, smtp_factory=smtp_factory,
                              smtp_ssl_factory=smtp_ssl_factory)

        with (
            patch("requests.post") as http_post,
            patch("requests.delete") as http_delete,
            patch("requests.request") as http_request,
            patch("smtplib.SMTP.send_message") as smtp_send_message,
        ):
            graph_user = graph.create_user("Ada", "Lovelace", "ada@example.edu", "Secret123!")
            license_add = graph.assign_license("ada@example.edu", "sku-a1")
            license_remove = graph.remove_license("ada@example.edu", "sku-a1")
            deleted = graph.delete_user("ada@example.edu")
            disabled = graph.disable_user("ada@example.edu")
            password_set = graph.set_temporary_password(
                user_identifier="ada@example.edu", password="DoNotLog-Temporary!"
            )

            moodle_user = moodle.create_user(
                "ada", "Ada", "Lovelace", "ada@example.edu"
            )
            enrollment = moodle.enrol_user(1, 2)
            simulated_enrollment = moodle.enrol_user(-1, 2)
            unenrollment = moodle.unenrol_user(1, 2)
            group_add = moodle.add_user_to_group(3, 1, course_id=2)
            simulated_group_add = moodle.add_user_to_group(-1, -1, course_id=-1)
            group_remove = moodle.remove_user_from_group(3, 1, course_id=2)
            simulated_group_id = moodle.get_or_create_group(2, "COMISION 1")
            chained_group_id = moodle.get_or_create_group(-1, "COMISION 2")

            mail = mailer.send_email("ada@example.edu", "Alta", "Bienvenida")
            credentials_mail = mailer.send_credentials_email(
                "ada@example.edu", "Ada Lovelace", "ada@example.edu", "DoNotLog-Temporary!"
            )

        self.assertTrue(graph_user.simulated)
        self.assertTrue(license_add.simulated)
        self.assertTrue(license_remove.simulated)
        self.assertTrue(deleted)
        self.assertTrue(disabled.simulated)
        self.assertFalse(disabled.enabled)
        self.assertTrue(password_set.simulated)
        self.assertTrue(moodle_user.simulated)
        self.assertTrue(enrollment.simulated)
        self.assertTrue(simulated_enrollment.simulated)
        self.assertTrue(unenrollment.simulated)
        self.assertTrue(group_add.simulated)
        self.assertTrue(simulated_group_add.simulated)
        self.assertTrue(group_remove.simulated)
        self.assertEqual(simulated_group_id, -1)
        self.assertEqual(chained_group_id, -1)
        self.assertTrue(mail.success)
        self.assertTrue(mail.simulated)
        self.assertTrue(credentials_mail.success)
        self.assertTrue(credentials_mail.simulated)
        http_post.assert_not_called()
        http_delete.assert_not_called()
        http_request.assert_not_called()
        smtp_send_message.assert_not_called()
        smtp_factory.assert_not_called()
        smtp_ssl_factory.assert_not_called()
        self.assertTrue(sial.dry_run)


if __name__ == "__main__":
    unittest.main()
