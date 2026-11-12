# 8. Preparación de Seguridad y Producción — pendiente para v1.0.0

v0.4.6 consolida funcionalidad, legibilidad y documentación. La certificación E2E y la suite sobre PostgreSQL no habilitan por sí solas el despliegue institucional ni prueban conectividad con tenants reales. Este capítulo es una lista de trabajo pendiente, no una afirmación de controles instalados.

## Acceso y transporte

- [ ] Definir Nginx como terminación TLS y proxy, límites de petición, certificados y entrega de estáticos recolectados.
- [ ] Acordar y probar restricciones de IP / GeoIP2, actualización de base geográfica y excepciones institucionales. GeoIP no reemplaza autenticación ni RBAC.
- [ ] Incorporar y probar 2FA TOTP, enrolamiento, recuperación y revocación para operadores y administradores.
- [ ] Activar redirección HTTPS, cookies seguras, HSTS y parámetros de proxy confiable; revisar CSRF, hosts y sesiones.
- [ ] Resolver dependencias CDN, compilación de estilos y política CSP de recursos y scripts.

## Secretos y proveedores

- [ ] Retirar `.env` del seguimiento del repositorio mediante una transición controlada; revisar historial y rotar todo secreto que hubiera sido expuesto. Hoy sigue versionado: no copiar sus valores a documentos.
- [ ] Reemplazar clave de desarrollo y credenciales de ejemplo por un gestor de secretos y permisos mínimos.
- [ ] Configurar credenciales reales de Microsoft, Moodle, SIAL y SMTP; validar permisos, destinos y licencias institucionales en un entorno de prueba autorizado.
- [ ] Resolver `mail.E001` del backend de consola en `MAILERS`. El MailerClient propio tiene configuración SMTP separada; uno no corrige automáticamente al otro.
- [ ] Comprobar transporte SMTP real, entrega, reintentos y protección de logs sin contraseñas, tokens o datos personales innecesarios.

## Explotación y continuidad

- [ ] Desactivar `DEBUG`, garantizar PostgreSQL y Redis protegidos, copias restaurables y retención de auditoría.
- [ ] Supervisar servidor de aplicación, trabajadores de ambas colas y una sola instancia de Beat.
- [ ] Configurar `STATIC_ROOT`, ejecutar recolección y probar recursos bajo HTTPS desde rutas anidadas.
- [ ] Acordar alertas por `FAILED`, atraso de cola, catálogos y TTL, y procedimiento de recuperación.
- [ ] Repetir `check --deploy`, migraciones, suite y recorrido manual de roles, móvil, accesibilidad y proveedores reales antes de emitir la aprobación productiva.

## Deuda conocida del chequeo de despliegue local

La evidencia pre-seguridad registra `mail.E001` y los avisos `security.W004` (HSTS), `W008` (redirección SSL), `W009` (clave de desarrollo), `W012` (cookie de sesión), `W016` (cookie CSRF) y `W018` (`DEBUG`). El chequeo normal `manage.py check` puede estar en verde mientras `check --deploy` exige resolver estas condiciones. Verificar de nuevo en la configuración final, sin suprimir avisos para aparentar un despliegue listo.
