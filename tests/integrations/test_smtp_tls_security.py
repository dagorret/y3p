"""Prueba que SMTP 587 negocia STARTTLS con contexto de confianza del sistema."""

import unittest
from unittest.mock import MagicMock, patch

from apps.integrations.services.mailer import MailerClient


class SmtpTlsSecurityTests(unittest.TestCase):
    """Evita regresiones que permitan STARTTLS sin validación de certificados."""

    def test_starttls_receives_default_ssl_context(self):
        """SMTP 587 crea contexto default y lo pasa explícitamente a starttls."""

        tls_context = object()
        server = MagicMock()
        server.send_message.return_value = {}
        smtp_context = MagicMock()
        smtp_context.__enter__.return_value = server
        client = MailerClient(
            host="smtp.example.edu",
            port=587,
            from_email="noreply@example.edu",
            smtp_factory=MagicMock(return_value=smtp_context),
        )

        with patch("apps.integrations.services.mailer.ssl.create_default_context",
                   return_value=tls_context) as create_context:
            result = client.send_email("person@example.edu", "Asunto", "Texto")

        self.assertTrue(result.success)
        create_context.assert_called_once_with()
        server.starttls.assert_called_once_with(context=tls_context)
        server.ehlo.assert_called()
        server.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
