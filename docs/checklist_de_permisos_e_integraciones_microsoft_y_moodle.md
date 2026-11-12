# Checklist de Permisos, Roles y Configuración Remota — Sistema Y3P (FCE / UNRC)

Este documento reúne los requisitos exactos de autorización que el servidor de **Y3P** necesita en **Microsoft Entra ID (Graph API)**, **Moodle FCE (Web Services REST)** y **Correo Institucional (SMTP)** para operar en producción sin fallos de permisos.

---

## 1. Microsoft Entra ID / Microsoft Graph (Teams y Licencias M365)

Y3P no utiliza contraseñas de administradores humanos. Opera mediante una **App Registration** en Microsoft Entra ID usando autenticación OAuth2 de aplicación (`ClientSecretCredential` con scope `https://graph.microsoft.com/.default`).

### 1.1. Permisos de API requeridos (Tipo: *Application permissions*, NO *Delegated*)
Todos los permisos siguientes deben contar con **Consentimiento de Administrador (*Admin Consent* = Granted)** en el tenant de la FCE:

| Permiso Microsoft Graph | Tipo | Para qué lo usa Y3P | Endpoints invocados |
| :--- | :--- | :--- | :--- |
| **`User.ReadWrite.All`** | Application | Consultar existencia por UPN, dar de alta cuentas institucionales (`@eco.unrc.edu.ar`), fijar `usageLocation="AR"`, actualizar contraseña temporal (`forceChangePasswordNextSignIn=True`) y deshabilitar/eliminar cuentas en bajas. | `GET /v1.0/users/{upn}`<br>`POST /v1.0/users`<br>`PATCH /v1.0/users/{id}`<br>`DELETE /v1.0/users/{id}` |
| **`LicenseAssignment.ReadWrite.All`** | Application | Asignar licencias educativas A1 (Alumnos o Docentes) al confirmar vinculación y retirarlas al vencer TTL o darse de baja. | `POST /v1.0/users/{id}/assignLicense` |
| **`Organization.Read.All`** *(o `Directory.Read.All`)* | Application | Leer los SKUs suscriptos disponibles en el tenant para validar cuotas y GUIDs de licencias A1. | `GET /v1.0/subscribedSkus` |
| **`Reports.Read.All`** | Application | Descargar el reporte CSV de actividad de Teams/Microsoft 365 de los últimos 180 días (D180) para auditorías de inactividad. | `GET /v1.0/reports/getTeamsUserActivityUserDetail(period='D180')` |

### 1.2. Configuración obligatoria en Microsoft 365 Admin Center
* **Desactivar anonimización de reportes (Crítico para D180):**
  * **Ruta:** *Microsoft 365 Admin Center* $\rightarrow$ *Settings* $\rightarrow$ *Org settings* $\rightarrow$ *Reports* $\rightarrow$ **Desmarcar** *"Display concealed user, group, and site names in all reports"*.
  * **Motivo:** Si esta casilla está marcada, Microsoft devuelve hashes SHA-256 anónimos en lugar de los correos `@eco.unrc.edu.ar`. Por seguridad, Y3P aborta el proceso (`ReportConcealedError`) si detecta hashes ocultos para evitar revocar licencias por falsa inactividad.
* **Bloqueo de acceso web en Enterprise Applications (Seguridad):**
  * En *Entra ID* $\rightarrow$ *Enterprise Applications*, la App Registration de Y3P **no debe** habilitar inicio de sesión web abierto por dominio (el acceso al portal web de Y3P se gestiona exclusivamente con usuarios locales y 2FA propio).

### 1.3. Valores a completar en `.env` de Producción
```env
AZURE_TENANT_ID=<GUID del tenant UNRC / FCE>
AZURE_CLIENT_ID=<Application (client) ID de la App Registration>
AZURE_CLIENT_SECRET=<Secreto vigente de la App Registration>
AZURE_STUDENT_SKU_ID=<GUID del SKU Microsoft 365 A1 for Students>
AZURE_TEACHER_SKU_ID=<GUID del SKU Microsoft 365 A1 for Faculty>
```

---

## 2. Moodle FCE (Web Services REST)

Y3P se comunica con Moodle mediante peticiones `POST` a `/webservice/rest/server.php` (`moodlewsrestformat=json`) utilizando un token asociado a un usuario de servicio dedicado.

### 2.1. Configuración previa en Moodle
1. **Activar Web Services:** *Administración del sitio* $\rightarrow$ *Características avanzadas* $\rightarrow$ *Habilitar servicios web* = **Sí**.
2. **Activar Protocolo REST:** *Administración del sitio* $\rightarrow$ *Servidor* $\rightarrow$ *Servicios web* $\rightarrow$ *Administrar protocolos* $\rightarrow$ Habilitar **REST**.
3. **Autenticación OpenID Connect (`auth_oidc`):** Debe estar activa, ya que Y3P crea los usuarios vinculando `username` y `email` al UPN `@eco.unrc.edu.ar` con `auth="oidc"`.
4. **Roles numéricos estándar verificados:**
   * Rol Estudiante (`roleid`): **`5`**
   * Rol Profesor / Docente (`roleid`): **`3`**

### 2.2. Funciones exactas a habilitar en el Servicio Web del Token
En *Administración del sitio* $\rightarrow$ *Servidor* $\rightarrow$ *Servicios web* $\rightarrow$ *Servicios externos* $\rightarrow$ *(Servicio Y3P/CRM)* $\rightarrow$ *Funciones*, deben figurar **todas** estas funciones:

| Función Web Service (`wsfunction`) | Operación | Uso en Y3P |
| :--- | :--- | :--- |
| **`core_webservice_get_site_info`** | Lectura | Verificación de salud, versión de Moodle y lista de funciones autorizadas al token. |
| **`core_user_get_users_by_field`** | Lectura | Buscar usuario por `username` (UPN institucional) antes de intentar crearlo (idempotencia). |
| **`core_user_create_users`** | Escritura | Crear ficha de usuario en Moodle con `auth="oidc"` y UPN institucional. |
| **`core_course_get_courses_by_field`** | Lectura | Resolver el `courseid` numérico a partir del `shortname` de la materia/aula (ej. `2`, `3`, `MBA-FIN-2026`). |
| **`enrol_manual_enrol_users`** | Escritura | Matricular al usuario en el aula con `roleid=5` (estudiante) o `roleid=3` (docente). *(Requiere método de matriculación "Manual enrolments" habilitado en los cursos destino)*. |
| **`enrol_manual_unenrol_users`** | Escritura | Desmatricular al usuario al vencer su TTL o darse de baja. |
| **`core_group_get_course_groups`** | Lectura | Listar los grupos/comisiones existentes dentro de un curso (ej. `COMISION 1`). |
| **`core_group_create_groups`** | Escritura | Crear el grupo/comisión dentro del curso si aún no existe. |
| **`core_group_add_group_members`** | Escritura | Asignar al estudiante al grupo de su comisión tras matricularlo. |
| **`core_group_delete_group_members`** | Escritura | Retirar al estudiante de una comisión anterior cuando cambia de grupo. |

> **Nota importante sobre grupos:** Si el token actual heredado del CRM viejo ya tiene habilitadas las funciones de usuarios y matrículas, **verificá específicamente que estén agregadas las 4 funciones de grupos (`core_group_*`)** y `core_webservice_get_site_info`. Si falta alguna, Moodle responderá `accessexception`.

### 2.3. Capacidades del rol del usuario propietario del Token en Moodle
El usuario de Moodle al que pertenece el `wstoken` debe tener un rol de sistema (o gestor) con las siguientes capacidades permitidas (`Allow`):
* `webservice/rest:use`
* `moodle/user:viewdetails`, `moodle/user:viewhiddendetails`, `moodle/user:create`
* `moodle/course:view`, `moodle/course:viewhiddencourses`
* `enrol/manual:enrol`, `enrol/manual:unenrol`
* `moodle/course:managegroups`

### 2.4. Valores a completar en `.env` de Producción
```env
MOODLE_BASE_URL=https://<dominio-moodle-fce>
MOODLE_WS_TOKEN=<Token generado para el servicio externo Y3P>
MOODLE_STUDENT_ROLE_ID=5
MOODLE_TEACHER_ROLE_ID=3
```

---

## 3. Correo Institucional (SMTP Saliente)

Para el envío de credenciales temporales de bienvenida a los correos personales de los ingresantes:
* **Servidor SMTP:** Puerto `587` con **STARTTLS** obligatorio (validación estricto de certificado TLS) o puerto `465` con **SMTP_SSL**.
* **Credencial:** Si la cuenta remitente institucional (ej. `cuentas@eco.unrc.edu.ar`) tiene verificación en dos pasos (2FA), generar una **Contraseña de Aplicación** específica para Y3P.

```env
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=cuentas@eco.unrc.edu.ar
EMAIL_HOST_PASSWORD=<Contraseña de aplicación de 16 caracteres>
DEFAULT_FROM_EMAIL=cuentas@eco.unrc.edu.ar
```

---

## 4. Protocolo de Validación en Vivo (1 Canario Real antes de Producción)

Una vez que la UTI confirme estos permisos y cargues las credenciales en el `.env` de producción (con `LUCY_DRY_RUN=True` inicialmente):
1. **Lectura segura en vivo:** Ejecutar la verificación de conexión a Moodle (`core_webservice_get_site_info`) y listado de SKUs en Graph (`GET /subscribedSkus`) para confirmar que los tokens autentican sin escribir datos.
2. **Prueba de humo con 1 único usuario sintético:** Desactivar simulación solo para **1 identidad de prueba controlada** (ej. DNI `99999999`), verificar su aparición real en Entra ID, licencia A1, aula/grupo de Moodle y recepción del correo de bienvenida, y eliminar inmediatamente la cuenta de prueba en ambas plataformas antes de abrir la operación masiva.