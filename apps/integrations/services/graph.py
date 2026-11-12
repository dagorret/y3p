"""Cliente Microsoft Graph para cuentas, licencias e informes de actividad.

La implementación usa el flujo OAuth2 de credenciales de aplicación con alcance Graph
``.default`` y conserva el token de acceso en memoria hasta poco antes de expirar.
El módulo no importa modelos Django; las escrituras retornan DTOs.
"""

import threading
import time
from urllib.parse import quote

from apps.integrations.dataclasses import (
    ExternalActionResult,
    TeamMemberResult,
    GraphAccountResult,
    GraphUserResult,
    LicenseResult,
    PasswordSetResult,
)
from apps.integrations.exceptions import (
    AuthenticationError,
    ConfigurationError,
    IntegrationError,
    RateLimitExceededError,
    ResourceAlreadyExistsError,
    ResourceNotFoundError,
)

from .base import BaseIntegrationClient


class MicrosoftGraphClient(BaseIntegrationClient):
    """Cliente Graph con token cacheado y escrituras interceptables.

    Parámetros:
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
            #FINTEGRATIONSSERVICESGRAPH004-8004: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
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

    def _write(self, method, path, *, json=None):
        """Envía una escritura autenticada de Graph por el traductor común de errores.

        Centraliza tiempos de espera, autenticación y errores HTTP. El llamador
        debe interceptar primero la simulación; así una escritura simulada nunca
        solicita un token ni accede a la red.
        """

        #FGRAPH28-1028: Todas las escrituras atraviesan el mismo límite HTTP autenticado.
        if method.upper() not in {"POST", "PATCH", "DELETE"}:
            #FGRAPH28-1028: Rechaza un verbo no permitido o sin efecto de escritura.
            raise ValueError("_write solo admite POST, PATCH y DELETE.")
        return self._graph_request(method, path, json=json)

    def get_user(self, upn: str) -> GraphUserResult | None:
        """Busca un usuario por UPN y retorna ``None`` solo ante HTTP 404."""

        if not upn:
            #FGRAPH09-1009: UPN vacío no identifica un recurso consultable.
            raise ValueError("upn no puede estar vacío.")
        try:
            response = self._graph_request("GET", f"users/{quote(upn, safe='')}")
        #FINTEGRATIONSSERVICESGRAPH010-8010: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
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

        if not all((first_name, upn, initial_password)):
            #FGRAPH11-1011: Campos mínimos evitan enviar un objeto inválido a Graph.
            raise ValueError("first_name, upn e initial_password son obligatorios.")
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
            "usageLocation": "AR",
            "passwordProfile": {
                "password": initial_password,
                "forceChangePasswordNextSignIn": force_change_password,
            },
        }
        if not last_name:
            payload.pop('surname')
        response = self._write("POST", "users", json=payload)
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
                self._write(
                    "POST",
                    f"users/{quote(user_id_or_upn, safe='')}/assignLicense",
                    json=payload,
                )
            #FINTEGRATIONSSERVICESGRAPH012-8012: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
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
        self._write(
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
        self._write("DELETE", f"users/{quote(user_id_or_upn, safe='')}")
        return True

    def disable_user(self, user_identifier: str) -> GraphAccountResult:
        """Deshabilita una cuenta Entra mediante PATCH y un resultado tipado.

        Fijar accountEnabled en falso es idempotente: repetir la orden conserva
        el estado deseado. La simulación retorna antes de OAuth o HTTP para
        impedir cambios accidentales durante las pruebas.
        """

        if not user_identifier:
            #FGRAPH29-1029: La ruta exige un identificador de usuario.
            raise ValueError("user_identifier no puede estar vacío.")
        if self._intercept_dry_run("disable_user", user_identifier=user_identifier):
            #FGRAPH30-1030: La simulación impide obtener un token o ejecutar PATCH.
            return GraphAccountResult(user_identifier, enabled=False, simulated=True)
        self._write(
            "PATCH",
            f"users/{quote(user_identifier, safe='')}",
            json={"accountEnabled": False},
        )
        return GraphAccountResult(user_identifier, enabled=False)

    def set_temporary_password(self, user_identifier: str, password: str) -> PasswordSetResult:
        """Asigna una clave de un solo uso sin devolverla ni almacenarla en el resultado."""

        if not user_identifier or not password:
            #FGRAPH31-1031: La escritura exige usuario y contraseña.
            raise ValueError("user_identifier y password son obligatorios.")
        if self._intercept_dry_run(
            "set_temporary_password", user_identifier=user_identifier, password=password
        ):
            #FGRAPH32-1032: Las credenciales simuladas no llegan a OAuth ni a Graph.
            return PasswordSetResult(user_identifier, simulated=True)
        self._write(
            "PATCH",
            f"users/{quote(user_identifier, safe='')}",
            json={
                "passwordProfile": {
                    "password": password,
                    "forceChangePasswordNextSignIn": True,
                }
            },
        )
        return PasswordSetResult(user_identifier)

    def revoke_sign_in_sessions(self, user_id_or_upn: str) -> ExternalActionResult:
        if not user_id_or_upn:
            #FINTEGRATIONSSERVICESGRAPH018-8018: Origina ValueError; el llamador conserva la causa y decide su tratamiento.
            raise ValueError("Debe indicar usuario.")
        if self._intercept_dry_run("revoke_sign_in_sessions", user_identifier=user_id_or_upn):
            return ExternalActionResult("revoke_sessions", simulated=True)
        response = self._write("POST", f"users/{quote(user_id_or_upn, safe='')}/revokeSignInSessions", json={})
        if self._json_object(response, "revocación Graph").get("value") is not True:
            #FINTEGRATIONSSERVICESGRAPH019-8019: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
            raise IntegrationError("Graph no confirmó la revocación de sesiones.")
        return ExternalActionResult("revoke_sessions")

    def reset_password_temporary(self, user_id_or_upn: str, temp_password: str) -> ExternalActionResult:
        result = self.set_temporary_password(user_id_or_upn, temp_password)
        return ExternalActionResult("reset_password", simulated=result.simulated)

    def toggle_user_status(self, user_id_or_upn: str, enable: bool) -> ExternalActionResult:
        if not user_id_or_upn or not isinstance(enable, bool):
            #FINTEGRATIONSSERVICESGRAPH020-8020: Origina ValueError; el llamador conserva la causa y decide su tratamiento.
            raise ValueError("Usuario y estado booleano son obligatorios.")
        if self._intercept_dry_run("toggle_user_status", user_identifier=user_id_or_upn, enable=enable):
            return ExternalActionResult("toggle_status", simulated=True)
        self._write("PATCH", f"users/{quote(user_id_or_upn, safe='')}", json={"accountEnabled": enable})
        return ExternalActionResult("toggle_status")

    def toggle_license(self, user_id_or_upn: str, sku_id: str, assign: bool) -> ExternalActionResult:
        if not isinstance(assign, bool):
            #FINTEGRATIONSSERVICESGRAPH021-8021: Origina ValueError; el llamador conserva la causa y decide su tratamiento.
            raise ValueError("assign debe ser booleano.")
        result = (self.assign_license if assign else self.remove_license)(user_id_or_upn, sku_id)
        return ExternalActionResult("assign_license" if assign else "remove_license", simulated=result.simulated)

    def get_user_license_ids(self, user_id_or_upn: str) -> tuple[str, ...]:
        """Lee el estado real para conmutar; la simulación no abre conexiones."""
        if not user_id_or_upn:
            #FINTEGRATIONSSERVICESGRAPH022-8022: Origina ValueError; el llamador conserva la causa y decide su tratamiento.
            raise ValueError("Debe indicar usuario.")
        if self.dry_run:
            return ()
        response = self._graph_request("GET", f"users/{quote(user_id_or_upn, safe='')}", params={"$select": "assignedLicenses"})
        licenses = self._json_object(response, "licencias Graph").get("assignedLicenses")
        if not isinstance(licenses, list) or any(not isinstance(item, dict) or not item.get("skuId") for item in licenses):
            #FINTEGRATIONSSERVICESGRAPH023-8023: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
            raise IntegrationError("Graph no devolvió el estado de licencias.")
        return tuple(str(item["skuId"]) for item in licenses)

    def create_team_from_template(self, display_name: str, description: str, owner_upn: str) -> ExternalActionResult:
        if not display_name.strip() or not owner_upn:
            #FINTEGRATIONSSERVICESGRAPH024-8024: Origina ValueError; el llamador conserva la causa y decide su tratamiento.
            raise ValueError("Nombre y propietario del equipo son obligatorios.")
        if self._intercept_dry_run("create_team_from_template", display_name=display_name, owner_upn=owner_upn):
            return ExternalActionResult("create_team", simulated=True, accepted=True)
        owner = self.get_user(owner_upn)
        if owner is None or not owner.id:
            #FINTEGRATIONSSERVICESGRAPH025-8025: Origina ResourceNotFoundError; el llamador conserva la causa y decide su tratamiento.
            raise ResourceNotFoundError("No existe el propietario del equipo.")
        response = self._write("POST", "teams", json={
            "template@odata.bind": f"{self.graph_base_url}/teamsTemplates('educationClass')",
            "displayName": display_name.strip(), "description": description.strip(),
            "members": [{"@odata.type": "#microsoft.graph.aadUserConversationMember", "roles": ["owner"],
                         "user@odata.bind": f"{self.graph_base_url}/users('{quote(owner.id, safe='')}')"}],
        })
        # Graph confirma aceptación (202); la creación continúa asincrónicamente.
        return ExternalActionResult("create_team", accepted=response.status_code == 202,
                                    resource_id=response.headers.get("Content-Location") or response.headers.get("Location"))

    def get_team_members(self, team_id: str) -> tuple[TeamMemberResult, ...]:
        if not team_id:
            #FINTEGRATIONSSERVICESGRAPH026-8026: Origina ValueError; el llamador conserva la causa y decide su tratamiento.
            raise ValueError('Debe indicar el equipo.')
        path = f"teams/{quote(team_id, safe='')}/members"
        results, visited = [], set()
        while path:
            if path in visited:
                #FINTEGRATIONSSERVICESGRAPH027-8027: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                raise IntegrationError('Paginación circular de miembros Graph.')
            visited.add(path)
            body = self._json_object(self._graph_request('GET', path), 'miembros Graph')
            if not isinstance(body.get('value'), list):
                #FINTEGRATIONSSERVICESGRAPH028-8028: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                raise IntegrationError('Graph devolvió miembros inválidos.')
            for member in body['value']:
                if not isinstance(member, dict) or not member.get('id') or not member.get('userId'):
                    #FINTEGRATIONSSERVICESGRAPH029-8029: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                    raise IntegrationError('Graph devolvió un miembro sin identificador.')
                user = self.get_user(str(member['userId']))
                if user is None:
                    #FINTEGRATIONSSERVICESGRAPH030-8030: Origina ResourceNotFoundError; el llamador conserva la causa y decide su tratamiento.
                    raise ResourceNotFoundError('No fue posible identificar al miembro del equipo.')
                results.append(TeamMemberResult(str(member['id']), str(member['userId']), user.upn,
                                               str(member.get('displayName', '')), 'owner' in member.get('roles', [])))
            next_link = body.get('@odata.nextLink')
            if next_link:
                prefix = f"{self.graph_base_url}/teams/{quote(team_id, safe='')}/members?"
                if not isinstance(next_link, str) or not next_link.startswith(prefix):
                    #FINTEGRATIONSSERVICESGRAPH031-8031: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                    raise IntegrationError('Destino de paginación Graph inválido.')
                path = next_link[len(self.graph_base_url) + 1:]
            else:
                path = None
        return tuple(results)

    def add_team_member(self, team_id: str, user_id_or_upn: str) -> ExternalActionResult:
        if not team_id or not user_id_or_upn:
            #FINTEGRATIONSSERVICESGRAPH032-8032: Origina ValueError; el llamador conserva la causa y decide su tratamiento.
            raise ValueError('Equipo y usuario son obligatorios.')
        if self._intercept_dry_run('add_team_member', team_id=team_id, user_identifier=user_id_or_upn):
            return ExternalActionResult('add_team_member', simulated=True)
        added = 1
        try:
            self._write('POST', f"teams/{quote(team_id, safe='')}/members", json={
                '@odata.type': '#microsoft.graph.aadUserConversationMember', 'roles': [],
                'user@odata.bind': f"{self.graph_base_url}/users('{quote(user_id_or_upn, safe='')}')",
            })
        #FINTEGRATIONSSERVICESGRAPH033-8033: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except ResourceAlreadyExistsError:
            members = self.get_team_members(team_id)
            if not any(m.upn.casefold() == user_id_or_upn.casefold() or m.user_id == user_id_or_upn for m in members):
                #FINTEGRATIONSSERVICESGRAPH034-8034: Propaga el fallo original sin convertirlo en éxito.
                raise
            added = 0
        return ExternalActionResult('add_team_member', affected_count=added)

    def purge_team_members(self, team_id: str, keep_owners: bool = True) -> ExternalActionResult:
        if not team_id or not isinstance(keep_owners, bool):
            #FINTEGRATIONSSERVICESGRAPH035-8035: Origina ValueError; el llamador conserva la causa y decide su tratamiento.
            raise ValueError("Equipo y política de propietarios son obligatorios.")
        if self._intercept_dry_run("purge_team_members", team_id=team_id, keep_owners=keep_owners):
            return ExternalActionResult("purge_team", simulated=True)
        path = f"teams/{quote(team_id, safe='')}/members"
        members, visited = [], set()
        while path:
            if path in visited:
                #FINTEGRATIONSSERVICESGRAPH036-8036: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                raise IntegrationError("Paginación circular de miembros Graph.")
            visited.add(path)
            body = self._json_object(self._graph_request("GET", path), "miembros Graph")
            page = body.get("value")
            if not isinstance(page, list) or any(not isinstance(m, dict) or not m.get("id") or not isinstance(m.get("roles"), list) for m in page):
                #FINTEGRATIONSSERVICESGRAPH037-8037: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                raise IntegrationError("Graph devolvió miembros inválidos.")
            members.extend(page)
            next_link = body.get("@odata.nextLink")
            if next_link:
                if not isinstance(next_link, str) or not next_link.startswith(f"{self.graph_base_url}/teams/{quote(team_id, safe='')}/members?"):
                    #FINTEGRATIONSSERVICESGRAPH038-8038: Origina IntegrationError; el llamador conserva la causa y decide su tratamiento.
                    raise IntegrationError("Destino de paginación Graph inválido.")
                path = next_link[len(self.graph_base_url) + 1:]
            else:
                path = None
        removed = 0
        for member in members:
            if keep_owners and "owner" in member["roles"]:
                continue
            self._write("DELETE", f"teams/{quote(team_id, safe='')}/members/{quote(member['id'], safe='')}")
            removed += 1
        return ExternalActionResult("purge_team", affected_count=removed)

    def get_teams_activity_d180(self) -> bytes:
        """Descarga el CSV del informe de actividad de Teams de los últimos 180 días."""

        response = self._graph_request(
            "GET", "reports/getTeamsUserActivityUserDetail(period='D180')"
        )
        #FGRAPH19-1019: El reporte puede ser CSV y se retorna sin decodificar.
        return response.content

    def _catalog_pages(self, path, *, params=None):
        """Lee todas las páginas y restringe continuaciones al mismo recurso Graph."""
        original = path
        rows, visited = [], set()
        while path:
            #FCATGRAPH01-9001: Rechaza continuaciones circulares antes de consultar la red.
            if path in visited:
                raise IntegrationError('Paginación circular del catálogo Graph.')
            visited.add(path)
            body = self._json_object(self._graph_request('GET', path, params=params), 'catálogo Graph')
            params = None
            #FCATGRAPH02-9002: Una respuesta incompleta no equivale a un catálogo vacío.
            if not isinstance(body.get('value'), list) or any(not isinstance(row, dict) or not row.get('id') for row in body['value']):
                raise IntegrationError('Graph devolvió una página de catálogo inválida.')
            rows.extend(body['value'])
            link = body.get('@odata.nextLink')
            #FCATGRAPH03-9003: No se envían credenciales a otros destinos o recursos.
            if link and (not isinstance(link, str) or not link.startswith(f'{self.graph_base_url}/{original}?')):
                raise IntegrationError('Destino de paginación del catálogo Graph inválido.')
            path = link[len(self.graph_base_url) + 1:] if link else None
        return rows

    def list_teams(self):
        """Lista los grupos que respaldan equipos, incluyendo alias y descripción."""
        return self._catalog_pages('groups', params={
            '$filter': "resourceProvisioningOptions/Any(x:x eq 'Team')",
            '$select': 'id,displayName,mailNickname,description',
        })

    def get_team(self, team_id):
        """Obtiene el grupo asociado al equipo para un refresco individual."""
        return self._json_object(self._graph_request('GET', f"groups/{quote(team_id, safe='')}",
            params={'$select': 'id,displayName,mailNickname,description'}), 'equipo Graph')

    def get_team_channels(self, team_id):
        """Consulta canales estándar y privados; no obtiene registros de acceso."""
        return self._catalog_pages(f"teams/{quote(team_id, safe='')}/channels",
                                   params={'$select': 'id,displayName,membershipType'})

    def get_user_joined_teams(self, user_id):
        """Devuelve todas las pertenencias directas confirmadas, con paginación."""
        #FCATGRAPH04-9004: Un identificador vacío no representa una identidad remota.
        if not user_id:
            raise ValueError('Debe indicar el identificador del usuario.')
        return [str(row['id']) for row in self._catalog_pages(f"users/{quote(user_id, safe='')}/joinedTeams")]

    def get_user_state(self, upn):
        """Lee habilitación y cambio de clave, sin consultar actividad ni accesos."""
        return self._json_object(self._graph_request('GET', f"users/{quote(upn, safe='')}",
            params={'$select': 'id,userPrincipalName,accountEnabled,passwordProfile'}), 'estado de usuario Graph')

    def _validate_license_inputs(self, user_id_or_upn, sku_id):
        """Valida identificadores mínimos antes de construir una mutación."""

        if not user_id_or_upn or not sku_id:
            #FGRAPH22-1022: Sin usuario y SKU no se puede construir assignLicense.
            raise ValueError("user_id_or_upn y sku_id son obligatorios.")

    def _json_object(self, response, description):
        """Decodifica un objeto JSON y rechaza respuestas con otra forma."""

        try:
            body = response.json()
        #FINTEGRATIONSSERVICESGRAPH042-8042: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except ValueError as error:
            #FGRAPH23-1023: Cuerpo inválido no puede convertirse en DTO exitoso.
            raise IntegrationError(f"Respuesta JSON inválida de {description}.") from error
        if not isinstance(body, dict):
            #FGRAPH24-1024: Los endpoints Graph de objeto requieren JSON object.
            raise IntegrationError(f"Respuesta inesperada de {description}.")
        return body
