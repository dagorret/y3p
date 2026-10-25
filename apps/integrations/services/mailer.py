"""Transporte SMTP con TLS estricto y resultados no excepcionales para workers."""

import smtplib
import ssl
from html import escape
from email.message import EmailMessage
from email.utils import make_msgid

from apps.integrations.dataclasses import MailDeliveryResult
from apps.integrations.exceptions import ConfigurationError

from .base import BaseIntegrationClient


class MailerClient(BaseIntegrationClient):
    """Entrega mensajes por STARTTLS 587 o TLS implícito 465.

    Solo admite los dos modos TLS seguros solicitados. STARTTLS usa siempre el
    contexto de certificados del sistema; nunca degrada a SMTP sin cifrado.
    En dry-run el bypass se produce antes de crear sockets o mensajes SMTP.
    Errores de validación, renderizado y transporte se devuelven en un DTO de
    fallo para que el worker pueda registrar el resultado y continuar.
    """

    service_name = "mailer"

    def __init__(
        self,
        *,
        host=None,
        port=587,
        username=None,
        password=None,
        from_email=None,
        dry_run=False,
        timeout=10.0,
        logger=None,
        smtp_factory=None,
        smtp_ssl_factory=None,
    ):
        """Guarda configuración SMTP e inyectores sin abrir conexión."""

        super().__init__(dry_run=dry_run, timeout=timeout, logger=logger)
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.from_email = from_email or username
        self.smtp_factory = smtp_factory
        self.smtp_ssl_factory = smtp_ssl_factory

    def _validate_configuration(self):
        """Exige host, remitente y un puerto TLS admitido antes de conectar."""

        if not self.host or not self.from_email:
            #FMAIL03-4003: Configuración SMTP incompleta no abre un socket.
            raise ConfigurationError("Mailer requiere host y from_email.")
        if self.port not in (465, 587):
            #FMAIL04-4004: Se rechaza SMTP plano o puerto TLS no soportado.
            raise ConfigurationError("Mailer solo admite puerto 587 STARTTLS o 465 SSL.")

    def send_email(
        self,
        recipient: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ) -> MailDeliveryResult:
        """Envía un correo y encapsula errores en ``MailDeliveryResult``.

        Args:
            recipient: Dirección del destinatario.
            subject: Asunto del mensaje.
            text_body: Cuerpo de texto obligatorio.
            html_body: Alternativa HTML opcional.

        Returns:
            Un resultado exitoso, simulado o fallido. El método no propaga errores
            de renderizado y conexión para no abortar el worker consumidor.
        """

        if self._intercept_dry_run(
            "send_email",
            recipient=recipient,
            subject=subject,
            text_body_length=len(text_body),
        ):
            #FMAIL02-4002: Bypass ocurre antes del contexto SSL y del socket SMTP.
            return MailDeliveryResult(
                recipient=recipient,
                subject=subject,
                success=True,
                message_id=None,
                simulated=True,
            )

        try:
            #FMAIL05-4005: Toda validación y renderizado queda dentro del resultado de fallo.
            self._validate_configuration()
            if not recipient or not subject or not text_body:
                #FMAIL06-4006: Rechaza mensaje sin destinatario, asunto o texto base.
                raise ValueError("recipient, subject y text_body son obligatorios.")

            message = EmailMessage()
            message["From"] = self.from_email
            message["To"] = recipient
            message["Subject"] = subject
            message["Message-ID"] = make_msgid()
            message.set_content(text_body)
            if html_body is not None:
                #FMAIL07-4007: HTML se adjunta como alternativa y conserva texto plano.
                message.add_alternative(html_body, subtype="html")

            #FMAIL01-4001: Contexto SSL valida certificados para ambos transportes TLS.
            tls_context = ssl.create_default_context()
            if self.port == 587:
                factory = self.smtp_factory or smtplib.SMTP
                #FMAIL08-4008: SMTP conecta explícitamente al puerto dedicado STARTTLS.
                with factory(self.host, self.port, timeout=self.timeout) as server:
                    server.ehlo()
                    #FMAIL09-4009: No envía credenciales antes de negociar TLS validado.
                    server.starttls(context=tls_context)
                    server.ehlo()
                    self._login_if_configured(server)
                    refused = server.send_message(message)
            else:
                factory = self.smtp_ssl_factory or smtplib.SMTP_SSL
                #FMAIL10-4010: SSL implícito usa el mismo contexto de confianza estricto.
                with factory(
                    self.host,
                    self.port,
                    timeout=self.timeout,
                    context=tls_context,
                ) as server:
                    self._login_if_configured(server)
                    refused = server.send_message(message)

            if refused:
                #FMAIL11-4011: SMTP reporta destinatario rechazado sin excepción.
                return MailDeliveryResult(
                    recipient=recipient,
                    subject=subject,
                    success=False,
                    message_id=message["Message-ID"],
                    error_message=(
                        "InvalidRecipientError: el servidor SMTP rechazó uno o más destinatarios."
                    ),
                )
            return MailDeliveryResult(
                recipient=recipient,
                subject=subject,
                success=True,
                message_id=message["Message-ID"],
            )
        except Exception as error:
            #FMAIL12-4012: Captura renderizado, configuración y SMTP para no abortar worker.
            self.logger.exception(
                "mail_delivery_failed",
                extra={
                    "integration": self.service_name,
                    "operation": "send_email",
                    "recipient": "[REDACTED]",
                    "error_type": error.__class__.__name__,
                },
            )
            return MailDeliveryResult(
                recipient=recipient,
                subject=subject,
                success=False,
                simulated=False,
                error_message=f"{error.__class__.__name__}: {error}",
            )

    def send_credentials_email(self, recipient: str, full_name: str, upn: str, password: str):
        """Render and send initial account credentials without logging the secret.

        The caller owns the ephemeral password lifetime. This method builds the
        only message body containing it, delegates transport handling to
        ``send_email`` and returns the delivery DTO; it never stores the value.
        """

        subject = "Credenciales de acceso institucional"
        text_body = (
            f"Hola {full_name},\n\n"
            "Tu cuenta institucional está disponible.\n"
            f"Usuario: {upn}\n"
            f"Contraseña temporal: {password}\n\n"
            "Al iniciar sesión se te pedirá cambiar la contraseña. "
            "Si recibiste más de un mensaje, usá el más reciente."
        )
        html_body = (
            f"<p>Hola {escape(full_name)},</p><p>Tu cuenta institucional está disponible.</p>"
            f"<p>Usuario: <strong>{escape(upn)}</strong><br>"
            f"Contraseña temporal: <strong>{escape(password)}</strong></p>"
            "<p>Al iniciar sesión se te pedirá cambiar la contraseña. "
            "Si recibiste más de un mensaje, usá el más reciente.</p>"
        )
        #FMAIL15-4015: Secret is passed only to the SMTP body; structured logs retain length only.
        return self.send_email(recipient, subject, text_body, html_body)

    def _login_if_configured(self, server):
        """Autentica solo si se configuró usuario; SMTP anónimo sigue en TLS."""

        if self.username:
            if not self.password:
                #FMAIL13-4013: No intenta autenticación SMTP con secreto ausente.
                raise ConfigurationError("Se configuró username SMTP sin password.")
            #FMAIL14-4014: LOGIN sucede después de completar negociación TLS.
            server.login(self.username, self.password)
