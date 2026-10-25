"""Cliente Microsoft Graph para cuentas, licencias e informes de actividad.

La implementación usa el flujo OAuth2 client credentials con scope Graph
``.default`` y conserva el access token en memoria hasta poco antes de expirar.
El módulo no importa modelos Django; las escrituras retornan DTOs.
"""

import threading
import time
from urllib.parse import quote

from apps.integrations.dataclasses import GraphUserResult, LicenseResult
from apps.integrations.exceptions import (
    AuthenticationError,
    ConfigurationError,
    IntegrationError,
    RateLimitExceededError,
    ResourceNotFoundError,
)

from .base import BaseIntegrationClient


class MicrosoftGraphClient(BaseIntegrationClient):
    """Cliente Graph con token cacheado y escrituras interceptables.

    Args:
        tenant_id: Tenant Microsoft Entra ID.
        client_id: Identificador de aplicación confidencial.
        client_secret: Secreto de aplicación, nunca escrito en logs.
        dry_run: Intercepta crear, licenciar y borrar sin token ni HTTP.
        timeout: Timeout por petición.
        session: Transporte HTTP compatible con ``requests``.
        graph_base_url: Base Graph configurable para pruebas.
        token_url: Endpoint OAuth2 opcional; por defecto deriva del tenant.
        max_rate_limit_retries: Reintentos acotados para HTTP 429.
        retry_backoff: Base de espera exponencial en segundos.

    ``get_user``, reportes y equipos son lecturas y siguen consultando Graph en
    dry-run. Las escrituras producen DTOs con ``simulated=True`` y no adquieren
    token.
    """

    service_name = "microsoft_graph"

    def __init__(
        self,
        *,
        tenant_id=None,
        client_id=None,
        client_secret=None,
        dry_run=False,
        timeout=10.0,
        logger=None,
        session=None,
        graph_base_url="https://graph.microsoft.com/v1.0",
        token_url=None,
        max_rate_limit_retries=3,
        retry_backoff=0.5,
    ):
        """Configura credenciales, endpoints y caché local sin abrir conexiones."""

        super().__init__(dry_run=dry_run, timeout=timeout, logger=logger, session=session)
        #FGRAPH01-1001: Reintentos acotados evitan bucles indefinidos ante 429.
        if max_rate_limit_retries < 0 or retry_backoff < 0:
            #FGRAPH01-1001: Rechaza política de reintento inválida.
            raise ValueError("Los reintentos y el backoff deben ser no negativos.")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.graph_base_url = graph_base_url.rstrip("/")
        self.token_url = token_url
        self.max_rate_limit_retries = max_rate_limit_retries
        self.retry_backoff = retry_backoff
        self._access_token_value = None
        self._access_token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def _validate_configuration(self):
        """Exige todos los datos OAuth antes de una lectura o escritura real."""

        if not all((self.tenant_id, self.client_id, self.client_secret)):
            #FGRAPH02-1002: No intenta pedir token con credenciales incompletas.
            raise ConfigurationError(
                "Microsoft Graph requiere tenant_id, client_id y client_secret."
            )

    def _get_access_token(self):
        """Retorna token vigente o solicita uno nuevo con client credentials.

        La caché se protege con un lock de proceso para evitar renovaciones
        paralelas. El token conserva margen de 60 segundos y no se registra.
        """

        self._validate_configuration()
        now = time.monotonic()
        if self._access_token_value and now < self._access_token_expires_at - 60:
            #FGRAPH03-1003: Reutiliza token vigente sin efectuar solicitud OAuth.
            return self._access_token_value

        with self._token_lock:
            now = time.monotonic()
            if self._access_token_value and now < self._access_token_expires_at - 60:
                #FGRAPH04-1004: Revalida caché después de adquirir lock.
                return self._access_token_value

            token_url = self.token_url or (
                f"https://login.microsoftonline.com/{quote(self.tenant_id, safe='')}/"
                "oauth2/v2.0/token"
            )
            payload = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }
            with self.safe_request():
                #FGRAPH05-1005: Solicitud OAuth solo para operaciones reales.
                response = self.session.post(
                    token_url,
                    data=payload,
                    timeout=self.timeout,
                )
            if response.status_code == 400:
                #FGRAPH25-1025: OAuth 400 indica credencial o solicitud de token inválida.
                raise AuthenticationError("Microsoft Entra rechazó la solicitud de token.")
            self._raise_for_status(response, resource="access token")
            try:
                token_body = response.json()
                token = token_body["access_token"]
                expires_in = int(token_body.get("expires_in", 3600))
            except (ValueError, KeyError, TypeError) as error:
                #FGRAPH06-1006: Respuesta OAuth incompleta no se trata como token válido.
                raise AuthenticationError("Microsoft Entra devolvió un token inválido.") from error
            if not token:
                #FGRAPH07-1007: El token vacío no autoriza solicitudes Graph.
                raise AuthenticationError("Microsoft Entra devolvió un token vacío.")
            self._access_token_value = token
            self._access_token_expires_at = time.monotonic() + max(0, expires_in)
            return token

    def _graph_request(self, method, path, *, params=None, json=None):
        """Ejecuta una llamada autenticada y valida el estado HTTP."""

        token = self._get_access_token()
        url = f"{self.graph_base_url}/{path.lstrip('/')}"
        request_method = getattr(self.session, method.lower(), None)
        if request_method is None:
            #FGRAPH26-1026: Transporte inyectado debe implementar el verbo HTTP requerido.
            raise IntegrationError(f"El transporte no soporta HTTP {method.upper()}.")
        with self.safe_request():
            #FGRAPH08-1008: Token solo se envía en cabecera Authorization sobre HTTPS.
            response = request_method(
                url,
                params=params,
                json=json,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=self.timeout,
            )
        self._raise_for_status(response, resource=path)
        return response

    def get_user(self, upn: str) -> GraphUserResult | None:
        """Busca un usuario por UPN y retorna ``None`` solo ante HTTP 404."""

        if not upn:
            #FGRAPH09-1009: UPN vacío no identifica un recurso consultable.
            raise ValueError("upn no puede estar vacío.")
        try:
            response = self._graph_request("GET", f"users/{quote(upn, safe='')}")
        except ResourceNotFoundError:
            #FGRAPH10-1010: 404 estructurado es ausencia, otros fallos se propagan.
            return None
        body = self._json_object(response, "usuario Graph")
        return GraphUserResult(
            id=str(body.get("id", "")),
            upn=str(body.get("userPrincipalName", upn)),
            display_name=str(body.get("displayName", "")),
            created=False,
            raw=body,
        )

    def create_user(
        self,
        first_name: str,
        last_name: str,
        upn: str,
        initial_password: str,
        force_change_password: bool = True,
    ) -> GraphUserResult:
        """Crea un usuario y eleva 409 como conflicto tipado.

        Dry-run retorna un identificador local sintético sin obtener token ni
        hacer HTTP. En modo real el proveedor confirma creación con respuesta
        2xx; cualquier HTTP 409 se convierte en ``ResourceAlreadyExistsError``.
        """

        if not all((first_name, last_name, upn, initial_password)):
            #FGRAPH11-1011: Campos mínimos evitan enviar un objeto inválido a Graph.
            raise ValueError("first_name, last_name, upn e initial_password son obligatorios.")
        display_name = f"{first_name} {last_name}".strip()
        if self._intercept_dry_run(
            "create_user",
            first_name=first_name,
            last_name=last_name,
            upn=upn,
            initial_password=initial_password,
        ):
            #FGRAPH12-1012: Bypass H-03 antecede token y escritura HTTP.
            return GraphUserResult(
                id=f"simulated:{upn}",
                upn=upn,
                display_name=display_name,
                created=True,
                simulated=True,
            )

        local_part = upn.split("@", 1)[0]
        payload = {
            "accountEnabled": True,
            "displayName": display_name,
            "mailNickname": local_part,
            "userPrincipalName": upn,
            "givenName": first_name,
            "surname": last_name,
            "passwordProfile": {
                "password": initial_password,
                "forceChangePasswordNextSignIn": force_change_password,
            },
        }
        response = self._graph_request("POST", "users", json=payload)
        body = self._json_object(response, "usuario Graph")
        return GraphUserResult(
            id=str(body.get("id", "")),
            upn=str(body.get("userPrincipalName", upn)),
            display_name=str(body.get("displayName", display_name)),
            created=True,
            raw=body,
        )

    def assign_license(self, user_id_or_upn: str, sku_id: str) -> LicenseResult:
        """Asigna una licencia y reintenta HTTP 429 con backoff acotado.

        El estado de licencia no se deduce de textos remotos: solo una respuesta
        2xx confirma la operación; al agotarse los reintentos se propaga un
        ``RateLimitExceededError`` con el último ``retry_after``.
        """

        self._validate_license_inputs(user_id_or_upn, sku_id)
        if self._intercept_dry_run(
            "assign_license", user_id_or_upn=user_id_or_upn, sku_id=sku_id
        ):
            #FGRAPH13-1013: Bypass de licencia evita token y POST remoto.
            return LicenseResult(user_id_or_upn, sku_id, assigned=True, simulated=True)

        payload = {"addLicenses": [{"skuId": sku_id}], "removeLicenses": []}
        for attempt in range(self.max_rate_limit_retries + 1):
            try:
                self._graph_request(
                    "POST",
                    f"users/{quote(user_id_or_upn, safe='')}/assignLicense",
                    json=payload,
                )
            except RateLimitExceededError as error:
                #FGRAPH27-1027: 429 usa backoff y un máximo finito de intentos.
                if attempt >= self.max_rate_limit_retries:
                    #FGRAPH14-1014: No se oculta rate-limit agotado.
                    raise
                delay = error.retry_after
                if delay is None:
                    delay = self.retry_backoff * (2**attempt)
                time.sleep(delay)
                continue
            return LicenseResult(user_id_or_upn, sku_id, assigned=True)
        #FGRAPH15-1015: Bucle acotado debe retornar o lanzar; esta protección evita retorno falso.
        raise IntegrationError("No se pudo confirmar la asignación de licencia.")

    def remove_license(self, user_id_or_upn: str, sku_id: str) -> LicenseResult:
        """Retira una licencia SKU del usuario de manera idempotente por estado deseado."""

        self._validate_license_inputs(user_id_or_upn, sku_id)
        if self._intercept_dry_run(
            "remove_license", user_id_or_upn=user_id_or_upn, sku_id=sku_id
        ):
            #FGRAPH16-1016: Bypass impide mutación remota de licencias.
            return LicenseResult(user_id_or_upn, sku_id, assigned=False, simulated=True)
        payload = {"addLicenses": [], "removeLicenses": [sku_id]}
        self._graph_request(
            "POST",
            f"users/{quote(user_id_or_upn, safe='')}/assignLicense",
            json=payload,
        )
        return LicenseResult(user_id_or_upn, sku_id, assigned=False)

    def delete_user(self, user_id_or_upn: str) -> bool:
        """Elimina una cuenta Entra, que queda bajo la política de papelera Graph."""

        if not user_id_or_upn:
            #FGRAPH17-1017: No permite borrar una ruta sin identificador.
            raise ValueError("user_id_or_upn no puede estar vacío.")
        if self._intercept_dry_run("delete_user", user_id_or_upn=user_id_or_upn):
            #FGRAPH18-1018: Bypass impide DELETE y adquisición de token.
            return True
        self._graph_request("DELETE", f"users/{quote(user_id_or_upn, safe='')}")
        return True

    def get_teams_activity_d180(self) -> bytes:
        """Descarga el CSV del informe de actividad de Teams de los últimos 180 días."""

        response = self._graph_request(
            "GET", "reports/getTeamsUserActivityUserDetail(period='D180')"
        )
        #FGRAPH19-1019: El reporte puede ser CSV y se retorna sin decodificar.
        return response.content

    def get_user_joined_teams(self, user_id: str) -> list[str]:
        """Devuelve IDs de equipos de los que el usuario es miembro."""

        if not user_id:
            #FGRAPH20-1020: Un usuario identificado es requerido por la ruta.
            raise ValueError("user_id no puede estar vacío.")
        response = self._graph_request(
            "GET", f"users/{quote(user_id, safe='')}/joinedTeams"
        )
        body = self._json_object(response, "equipos Graph")
        values = body.get("value", [])
        if not isinstance(values, list):
            #FGRAPH21-1021: Forma incorrecta del resultado no se normaliza a lista vacía.
            raise IntegrationError("Microsoft Graph devolvió joinedTeams en formato inválido.")
        return [str(team["id"]) for team in values if isinstance(team, dict) and team.get("id")]

    def _validate_license_inputs(self, user_id_or_upn, sku_id):
        """Valida identificadores mínimos antes de construir una mutación."""

        if not user_id_or_upn or not sku_id:
            #FGRAPH22-1022: Sin usuario y SKU no se puede construir assignLicense.
            raise ValueError("user_id_or_upn y sku_id son obligatorios.")

    def _json_object(self, response, description):
        """Decodifica un objeto JSON y rechaza respuestas con otra forma."""

        try:
            body = response.json()
        except ValueError as error:
            #FGRAPH23-1023: Cuerpo inválido no puede convertirse en DTO exitoso.
            raise IntegrationError(f"Respuesta JSON inválida de {description}.") from error
        if not isinstance(body, dict):
            #FGRAPH24-1024: Los endpoints Graph de objeto requieren JSON object.
            raise IntegrationError(f"Respuesta inesperada de {description}.")
        return body
