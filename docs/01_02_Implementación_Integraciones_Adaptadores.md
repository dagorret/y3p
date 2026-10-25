**Módulo:** `apps.integrations` — Clientes externos, transporte y contratos de servicio

**Sistema:** Lucy / Y3P — orquestación de identidades — FCE / UNRC

**Archivos documentados:** `exceptions.py`, `dataclasses.py`, `services/base.py`, `services/graph.py`, `services/moodle.py`, `services/sial.py`, `services/mailer.py`

**Estado:** diseño aprobado, implementación base en `feature/integrations-clients`

## Índice

1. [Para qué existe este módulo](https://www.google.com/search?q=%231-para-qu%C3%A9-existe-este-m%C3%B3dulo)

2. [La idea en un párrafo](https://www.google.com/search?q=%232-la-idea-en-un-p%C3%A1rrafo)

3. [Glosario](https://www.google.com/search?q=%233-glosario)

4. [Arquitectura y desacople del ORM](https://www.google.com/search?q=%234-arquitectura-y-desacople-del-orm)

5. [Patrones de diseño utilizados](https://www.google.com/search?q=%235-patrones-de-dise%C3%B1o-utilizados)

6. [Catálogo de excepciones y DTOs](https://www.google.com/search?q=%236-cat%C3%A1logo-de-excepciones-y-dtos)

7. [Adaptadores de servicio, uno por uno](https://www.google.com/search?q=%237-adaptadores-de-servicio-uno-por-uno)

8. [Políticas de simulación (DRY_RUN) y seguridad de transporte](https://www.google.com/search?q=%238-pol%C3%ADticas-de-simulaci%C3%B3n-dry_run-y-seguridad-de-transporte)

9. [Invariantes del módulo](https://www.google.com/search?q=%239-invariantes-del-m%C3%B3dulo)

10. [Decisiones de diseño y alternativas descartadas](https://www.google.com/search?q=%2310-decisiones-de-dise%C3%B1o-y-alternativas-descartadas)

11. [Guía práctica de consumo](https://www.google.com/search?q=%2311-gu%C3%ADa-pr%C3%A1ctica-de-consumo)

12. [Vulnerabilidades y defectos del legado eliminados](https://www.google.com/search?q=%2312-vulnerabilidades-y-defectos-del-legado-eliminados)

## 1. Para qué existe este módulo

`apps.integrations` es la **frontera física de comunicación** entre el ecosistema institucional de la FCE y las plataformas externas:

- **Microsoft Entra ID / Microsoft 365 Graph:** aprovisionamiento de cuentas, asignación/retiro de licencias educativas A1 y recolección de reportes D180[cite: 2, 4, 5].

- **Moodle REST API:** validación de usuarios, alta con autenticación institucional (`oidc`), matriculación por rol y particionamiento en comisiones/grupos[cite: 2, 3, 5].

- **SIAL (Sistema de Información Académica Local):** ingesta de padrones oficiales en tres listas (preinscriptos, aspirantes, ingresantes) y legajos personales.

- **Servicio SMTP:** despacho transaccional de credenciales temporales y notificaciones masivas institucionales.

Este módulo no decide *cuándo* una persona debe crearse, matricularse o perder su licencia (esa regla pertenece al metamodelo de `apps.people` y al orquestador de `apps.provisioning`)[cite: 5]. Este módulo se encarga exclusivamente de **ejecutar la orden técnica de forma segura, tipada, idempotente y simulación-consciente**[cite: 2, 4, 5].

## 2. La idea en un párrafo

> Los adaptadores de `apps.integrations` son **clientes de servicio puros**, completamente ciegos a la base de datos de Django y a los modelos de identidad. Reciben tipos primitivos o DTOs inmutables, ejecutan llamadas de red protegidas contra cortes o sobrecargas, interceptan cualquier mutación dentro del propio cliente cuando `dry_run=True`, y devuelven estructuras de datos cerradas con el resultado exacto[cite: 2, 4, 5]. Si una llamada falla, levantan excepciones de dominio tipadas sin filtrar contraseñas ni deducir éxitos por fragmentos ambiguos de texto[cite: 2, 4, 5].

## 3. Glosario

| **Término**                           | **Significado**                                                                                                                                                                          |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Adaptador (Adapter)**               | Clase que traduce una interfaz estandarizada de Python a las particularidades de la API remota (Graph, Moodle, SIAL, SMTP).                                                              |
| **DTO (Data Transfer Object)**        | Objeto inmutable de transporte de datos (`@dataclass(frozen=True)`) que garantiza contratos cerrados a la entrada y a la salida de las integraciones[cite: 5].                           |
| **Bypass por Simulación (`DRY_RUN`)** | Intercepción obligatoria dentro de cada cliente: si `dry_run=True`, la operación de escritura no toca la red, emite log estructurado y retorna un DTO simulado con éxito[cite: 2, 4, 5]. |
| **Idempotencia**                      | Capacidad de ejecutar la misma operación técnica repetidas veces sin producir duplicación, corrupción de datos ni estados inconsistentes[cite: 2, 4, 5].                                 |
| **SKU (Stock Keeping Unit)**          | Identificador único de licencia en el tenant de Microsoft 365 (ej. A1 para Alumnos o Docentes)[cite: 2, 4, 5].                                                                           |
| **Reporte D180**                      | Informe oficial agregado de actividad de Teams y Microsoft 365 sobre una ventana móvil de 180 días[cite: 1, 2, 5].                                                                       |
| **Rate Limit / HTTP 429**             | Respuesta del servidor remoto indicando agotamiento de cuota por unidad de tiempo, requiriendo retroceso exponencial (*backoff*)[cite: 2].                                               |

## 4. Arquitectura y desacople del ORM

```
┌──────────────────────────────────────────────────────────────────┐
│  CAPA DE NEGOCIO Y ORQUESTACIÓN (apps.people / apps.provisioning)│
│  Lee/escribe modelos ORM: Identity, Affiliation, OutboxEvent     │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ Pasa solo tipos primitivos o DTOs
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  CAPA DE INTEGRACIÓN (apps.integrations.services)                │
│  - Sin dependencias con models.py ni settings de base de datos   │
│  - Clientes puros: GraphClient, MoodleClient, SialClient, Mailer │
├──────────────────────────────────────────────────────────────────┤
│  MECANISMOS INTERNOS COMPARTIDOS (BaseIntegrationClient)         │
│  - dry_run interceptado antes del socket                         │
│  - safe_request() traduciendo HTTP/Socket a exceptions.py        │
│  - Delays controlados de proceso (Rate Limiter de 0.4s)          │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ I/O de red seguro (HTTPS / STARTTLS)
                                  ▼
     [ Microsoft Graph ]   [ Moodle REST ]   [ SIAL API ]   [ SMTP ]
```

### Reglas de frontera arquitectónica:

1. **Prohibición de importar modelos:** Ningún archivo dentro de `apps/integrations/` puede importar `apps.people.models`[cite: 5].

2. **Independencia del framework:** Los clientes son clases de servicio que pueden instanciarse directamente en pruebas unitarias, comandos CLI o tareas de Celery pasando configuraciones explícitas o inicializadas mediante factorías[cite: 5].

3. **Cero persistencia directa:** Los clientes no abren transacciones ni tocan la base; retornan el resultado para que el orquestador (`apps.provisioning`) lo asiente en la bitácora transaccional.

## 5. Patrones de diseño utilizados

### 5.1 Patrón Adaptador (*Adapter Pattern*)

- **Problema:** Cada servicio externo utiliza protocolos distintos: Microsoft Graph usa OAuth2 con JSON REST[cite: 2]; Moodle usa llamadas RPC sobre un endpoint PHP con parámetros codificados en form-data[cite: 2]; SIAL utiliza Basic Auth[cite: 2]; y el correo utiliza sockets SMTP con negociación TLS[cite: 2].

- **Solución:** Cada subsistema cuenta con un cliente dedicado (`MicrosoftGraphClient`, `MoodleRestClient`, `SialClient`, `MailerClient`) que expone métodos semánticos limpios en Python (`create_user`, `assign_license`, `enrol_user`, `send_email`).

- **Precio:** Requiere mantener y actualizar la capa de traducción cuando las APIs remotas introducen cambios de versión[cite: 2].

### 5.2 Intercepción de Simulación en el Cliente (*In-Client DRY_RUN*)

- **Problema:** En el sistema anterior (H-03), la simulación se controlaba únicamente en ciertas vistas web. Cuando los procesos corrían en segundo plano o mediante submódulos auxiliares, los clientes ejecutaban llamadas destructivas a pesar de que la configuración global indicaba simulación.

- **Solución:** El valor `dry_run` se inyecta en el constructor del cliente y cada método de escritura evalúa `if self.dry_run: return self._simulate(...)` antes de abrir cualquier socket o conexión HTTP[cite: 2, 4, 5].

- **Precio:** El código de cada método mutador incluye la bifurcación de simulación, la cual debe probarse exhaustivamente en las suites unitarias.

### 5.3 Mapeo Tipado de Errores (*Exception Translation*)

- **Problema:** En el código heredado (H-17), las respuestas de error se parseaban buscando cadenas como `"already exists"` para inferir si una operación había tenido éxito. Un cambio de idioma o formato en el servidor remoto causaba fallos silenciosos.

- **Solución:** Un gestor de contexto central (`safe_request`) intercepta códigos de estado HTTP (400, 401, 403, 404, 409, 429, 503) y los mapea a una jerarquía estricta de excepciones de Python (`ResourceAlreadyExistsError`, `RateLimitExceededError`, etc.).

- **Precio:** Requiere tipar explícitamente las estructuras de error de cada API externa[cite: 2].

## 6. Catálogo de excepciones y DTOs

### 6.1 Jerarquía de Excepciones (`apps/integrations/exceptions.py`)

Plaintext

```
IntegrationError (Base)
 ├── ConfigurationError               # Credenciales faltantes o mal formadas
 ├── AuthenticationError              # Token inválido, expirado o Basic Auth erróneo
 ├── OperationForbiddenError          # Sin permisos suficientes (HTTP 403)
 ├── ResourceNotFoundError            # Usuario, curso o SKU inexistente (HTTP 404)
 ├── ResourceAlreadyExistsError       # Conflicto HTTP 409 o entidad preexistente
 ├── RateLimitExceededError           # HTTP 429 (incluye atributo retry_after)
 └── RemoteServiceUnavailableError    # Timeouts, 502/503/504 o caída de conexión
```

### 6.2 DTOs de Retorno (`apps/integrations/dataclasses.py`)

Todos los DTOs están definidos como clases inmutables con `@dataclass(frozen=True)`:

| **DTO**                  | **Campos Clave**                                                                           | **Significado**                                                   |
| ------------------------ | ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| `GraphUserResult`        | `id`, `upn`, `display_name`, `created`, `simulated`, `raw`                                 | Resultado de búsqueda o alta de cuenta en Microsoft Entra ID.     |
| `LicenseResult`          | `user_id_or_upn`, `sku_id`, `assigned`, `simulated`                                        | Registro del alta o retiro de licencia A1.                        |
| `MoodleUserResult`       | `id`, `username`, `email`, `created`, `simulated`                                          | Identidad confirmada en el campus virtual[cite: 2].               |
| `MoodleEnrollmentResult` | `user_id`, `course_id`, `role_id`, `success`, `simulated`                                  | Estado de matriculación o desmatriculación a un curso[cite: 2].   |
| `MoodleGroupResult`      | `group_id`, `course_id`, `user_id`, `action`, `success`, `simulated`                       | Incorporación o retiro de un estudiante de una comisión[cite: 2]. |
| `SialStudentDTO`         | `dni`, `first_name`, `last_name`, `personal_email`, `career_code`, `modality`, `condition` | Registro académico oficial proveniente de Bedelía/SIAL[cite: 2].  |
| `MailDeliveryResult`     | `recipient`, `subject`, `success`, `message_id`, `simulated`, `error_message`              | Comprobante técnico del despacho SMTP individual.                 |

## 7. Adaptadores de servicio, uno por uno

### 7.1 `MicrosoftGraphClient` (`services/graph.py`)

Encargado de la interacción con la API Graph v1.0.

Code snippet

```
sequenceDiagram
    participant App as Invocador
    participant G as MicrosoftGraphClient
    participant MS as Microsoft Graph API

    App->>G: create_user(first_name, last_name, upn, pwd)
    alt dry_run == True
        G-->>App: GraphUserResult(created=True, simulated=True)
    else dry_run == False
        G->>MS: POST /v1.0/users (Bearer Token)
        alt HTTP 201 Created
            MS-->>G: 201 {id, userPrincipalName...}
            G-->>App: GraphUserResult(created=True, simulated=False)
        else HTTP 409 Conflict
            MS-->>G: 409 User Already Exists
            G-->>App: raise ResourceAlreadyExistsError(upn)
        end
    end
```

- **Manejo de tokens:** Solicita el token OAuth2 mediante credenciales de cliente contra `[https://login.microsoftonline.com/](https://login.microsoftonline.com/){tenant_id}/oauth2/v2.0/token` y lo mantiene en memoria hasta su expiración[cite: 2].

- **Asignación/Retiro de licencias:** Ejecuta `POST /v1.0/users/{id}/assignLicenses`[cite: 2]. Ante respuestas con código `429`, realiza hasta 3 reintentos respetando la cabecera `Retry-After`[cite: 2].

- **Actividad D180:** Invoca `GET /v1.0/reports/getTeamsUserActivityUserDetail(period='D180')`, resuelve la redirección a la URL temporal pre-firmada y descarga el contenido CSV sin propagar la cabecera de autenticación[cite: 2].

### 7.2 `MoodleRestClient` (`services/moodle.py`)

Encargado de la interacción con el endpoint `/webservice/rest/server.php`.

- **Cadencia de peticiones (*Pacing*):** Implementa un candado de sincronización de proceso que fuerza un intervalo mínimo de 0.4 segundos entre solicitudes consecutivas, respetando los límites de carga del servidor de la facultad[cite: 2].

- **Mapeo de usuarios:** Asigna obligatoriamente `auth='oidc'` y establece el UPN institucional en los campos `username` y `email`, impidiendo que el correo personal sobrescriba el identificador institucional[cite: 2].

- **Comisiones y Grupos:** Al reasignar un alumno de comisión, el flujo garantiza la adición a la nueva antes de ejecutar la desvinculación de la anterior, reportando fallos parciales de manera estructurada[cite: 2].

### 7.3 `SialClient` (`services/sial.py`)

Encargado de la sincronización de padrones de aspirantes y alumnos de grado.

- **Autenticación:** HTTP Basic Auth sobre HTTPS con verificación estricta de certificados TLS[cite: 2].

- **Normalización de Identificadores:** Sanea la representación documental descartando el sufijo `.0` característico de campos numéricos importados de hojas de cálculo[cite: 2].

- **Verificación de Ingesta:** La consulta de listas (`preinscriptos`, `aspirantes`, `ingresantes`) es atómica en memoria: si alguna de las tres llamadas falla, la corrida de sincronización se descarta por completo para evitar cruces parciales[cite: 2].

### 7.4 `MailerClient` (`services/mailer.py`)

Encargado del despacho de credenciales temporales y notificaciones oficiales.

- **Transporte y Cifrado:**

  - Puerto 587: apertura de socket plano seguida de `STARTTLS` obligatorio utilizando `ssl.create_default_context()`[cite: 2].

  - Puerto 465: conexión directa vía `smtplib.SMTP_SSL` con contexto seguro por defecto[cite: 2].

- **Prevención de Caídas:** La construcción del mensaje MIME se encuentra aislada dentro del bloque de control; fallos de codificación o direcciones de correo mal formadas generan un `MailDeliveryResult(success=False)` sin interrumpir la ejecución de los lotes de envío[cite: 2].

## 8. Políticas de simulación (DRY_RUN) y seguridad de transporte

### 8.1 Matriz de Comportamiento por Operación y Modo

| **Servicio** | **Operación**          | **Modo dry_run=True**                                     | **Modo dry_run=False**                    |
| ------------ | ---------------------- | --------------------------------------------------------- | ----------------------------------------- |
| **Graph**    | `get_user`             | Ejecuta llamada de red (Lectura segura)[cite: 2]          | Ejecuta llamada de red[cite: 2]           |
| **Graph**    | `create_user`          | **Simula:** loguea y retorna DTO simulado[cite: 2]        | Llama `POST /v1.0/users`[cite: 2]         |
| **Graph**    | `assign_license`       | **Simula:** loguea y retorna DTO simulado[cite: 2]        | Llama `POST .../assignLicenses`[cite: 2]  |
| **Graph**    | `delete_user`          | **Simula:** loguea y retorna `True`[cite: 2]              | Llama `DELETE /v1.0/users/{id}`[cite: 2]  |
| **Moodle**   | `get_user_by_username` | Ejecuta llamada de red (Lectura segura)[cite: 2]          | Ejecuta llamada de red[cite: 2]           |
| **Moodle**   | `enrol_user`           | **Simula:** loguea y retorna DTO simulado[cite: 2]        | Llama `enrol_manual_enrol_users`[cite: 2] |
| **Moodle**   | `create_group`         | **Simula:** loguea y retorna ID simulado (-1)[cite: 2]    | Llama `core_group_create_groups`[cite: 2] |
| **SMTP**     | `send_email`           | **Simula:** no abre socket, retorna DTO simulado[cite: 2] | Negocia TLS y envía mensaje[cite: 2]      |

## 9. Invariantes del módulo

1. **Invariante de Aislamiento de Red:** Bajo `dry_run=True`, es técnicamente imposible que se transmita un solo paquete TCP de escritura hacia Microsoft Graph, Moodle o el servidor SMTP.

2. **Invariante de Tipado Estricto:** Toda función pública del módulo debe retornar un tipo atómico, un DTO inmutable o levantar una subclase de `IntegrationError`[cite: 5]. Prohibido devolver tuplas sueltas o booleanos sin contexto.

3. **Invariante de Confidencialidad de Secretos:** Ninguna excepción o traza de log generada en este módulo puede imprimir tokens de autenticación, secretos de aplicación, contraseñas de cuentas ni claves SMTP.

4. **Invariante de Integridad TLS:** Prohibido el uso de conexiones no seguras o llamadas `starttls()` sin verificación de certificados (`CERT_NONE`)[cite: 2].

## 10. Decisiones de diseño y alternativas descartadas

### 10.1 Clientes HTTP basados en `requests` / `httpx` estándar vs. SDKs oficiales

- **Elegido:** Implementación de clientes dedicados sobre librerías HTTP consolidadas[cite: 2].

- **Descartado:** Depender del Microsoft Graph Python SDK completo.

- **Motivo:** El SDK oficial arrastra un volumen considerable de dependencias acopladas, sufre cambios frecuentes en su interfaz de tipos y dificulta la intercepción quirúrgica del modo `dry_run` en operaciones sensibles[cite: 2].

### 10.2 Retención de contraseñas de cuentas en el DTO

- **Elegido:** La contraseña temporal generada se transfiere únicamente al invocador inmediato dentro del contexto de alta para su despacho seguro[cite: 2].

- **Descartado:** Almacenar la contraseña en el cliente o registrarla en archivos de volcado.

- **Motivo:** Cumplimiento de las normativas de seguridad de la auditoría: las contraseñas temporales no deben persistir en registros de texto plano.

## 11. Guía práctica de consumo

### Instanciación y llamada segura:

Python

```
from apps.integrations.services.graph import MicrosoftGraphClient
from apps.integrations.exceptions import ResourceAlreadyExistsError, IntegrationError

client = MicrosoftGraphClient(
    tenant_id="conf-tenant-id",
    client_id="conf-client-id",
    client_secret="conf-secret",
    dry_run=True  # Operación en modo seguro
)

try:
    resultado = client.create_user(
        first_name="Juan",
        last_name="Perez",
        upn="a38123456@eco.unrc.edu.ar",
        initial_password="TempPassword123!"
    )
    if resultado.simulated:
        logger.info("Operación simulada con éxito para %s", resultado.upn)
except ResourceAlreadyExistsError:
    logger.warning("La cuenta institucional ya existe en Entra ID.")
except IntegrationError as exc:
    logger.error("Fallo general de comunicación con Graph: %s", exc)
```

## 12. Vulnerabilidades y defectos del legado eliminados

| **Fallo Auditado (Octubre 2026)** | **Defecto en el Sistema Legacy**                                                                | **Solución Blindada en apps.integrations**                                                                  |
| --------------------------------- | ----------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **H-03 (Crítica)**                | Simulación controlada en vistas; llamadas de escritura reales en procesos de fondo.             | `dry_run` verificado adentro de cada cliente antes de abrir cualquier socket o llamada HTTP[cite: 2, 4, 5]. |
| **H-04 / H-05 (Alta)**            | Contraseña SMTP expuesta en formularios y STARTTLS sin validar certificado[cite: 2].            | Contraseñas protegidas y STARTTLS exigiendo `ssl.create_default_context()` obligatorio[cite: 2].            |
| **H-11 (Alta)**                   | Ambigüedad en fallos de licencia y despacho de contraseñas cruzadas.                            | Manejo desacoplado por pasos atómicos y excepciones de conflicto (`409`) tipadas.                           |
| **H-17 (Media)**                  | Detección de éxito en Moodle evaluando subcadenas como `"already exists"` en mensajes de error. | Mapeo tipado de esquemas y comprobación remota explícita de existencia e idempotencia.                      |

*Documento 01_02 de la serie técnica de arquitectura. El siguiente documento (01_03) abordará el diseño del motor de aprovisionamiento asíncrono (`apps.provisioning`), la tabla de Outbox Transaccional y la sincronización coordinada con Celery y Redis.*
