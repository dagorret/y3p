"""Prueba el mapeo HTTP 409 de Microsoft Graph a conflicto tipado."""

import unittest
from unittest.mock import Mock

from apps.integrations.exceptions import ResourceAlreadyExistsError
from apps.integrations.services.graph import MicrosoftGraphClient


class GraphConflictTests(unittest.TestCase):
    """Verifica que una colisión estructurada no se interprete como éxito."""

    def test_create_user_409_raises_resource_already_exists(self):
        """HTTP 409 produce ResourceAlreadyExistsError con detalles de respuesta."""

        response = Mock(status_code=409)
        response.headers = {}
        response.json.return_value = {
            "error": {"code": "ObjectConflict", "message": "A user with this UPN exists."}
        }
        session = Mock()
        session.post.return_value = response
        client = MicrosoftGraphClient(
            tenant_id="tenant-id",
            client_id="client-id",
            client_secret="client-secret",
            session=session,
        )
        client._access_token_value = "test-access-token"
        client._access_token_expires_at = float("inf")

        with self.assertRaises(ResourceAlreadyExistsError) as raised:
            client.create_user("Ada", "Lovelace", "ada@example.edu", "Secret123!")

        self.assertEqual(raised.exception.resource, "users")
        self.assertEqual(raised.exception.details["error"]["code"], "ObjectConflict")
        session.post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
