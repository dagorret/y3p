"""Tests the Graph account-disable write and secret-free credential DTO contract."""

import unittest
from unittest.mock import Mock

from apps.integrations.services.graph import MicrosoftGraphClient


class GraphProvisioningMethodTests(unittest.TestCase):
    """Exercise Graph provisioning writes without external network access."""

    def test_disable_user_uses_patch_payload_through_write_boundary(self):
        """The disable intent maps exactly to accountEnabled false via PATCH."""

        response = Mock(status_code=204)
        session = Mock()
        session.patch.return_value = response
        client = MicrosoftGraphClient(
            tenant_id="tenant",
            client_id="client",
            client_secret="secret",
            session=session,
        )
        client._access_token_value = "cached-token"
        client._access_token_expires_at = float("inf")

        result = client.disable_user("ada@example.edu")

        self.assertFalse(result.enabled)
        self.assertFalse(result.simulated)
        session.patch.assert_called_once()
        args, kwargs = session.patch.call_args
        self.assertEqual(args[0], "https://graph.microsoft.com/v1.0/users/ada%40example.edu")
        self.assertEqual(kwargs["json"], {"accountEnabled": False})


if __name__ == "__main__":
    unittest.main()
