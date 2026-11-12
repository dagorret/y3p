# Contratos de integración externa — v0.4.6

[Índice documental](README.md) · [Manual relacionado](manual/05_espejos_y_vencimientos.md)

## Adaptadores reales

Los contratos de `apps/integrations/services/base.py` y resultados de `dataclasses.py` aíslan Graph, Moodle, SIAL y correo del ORM. Las fábricas construyen clientes con la configuración efectiva. Los servicios de dominio y el orquestador deciden la política y persistencia; un adaptador no debe decidir permisos académicos.

`MicrosoftGraphClient` administra cuentas, licencias, sesiones, equipos y sus catálogos. Las lecturas paginadas deben completar el inventario y validar continuaciones. `MoodleRestClient` resuelve usuarios, cursos, matrículas y grupos; los errores lógicos de Moodle pueden viajar sobre HTTP 200 y deben traducirse a excepciones tipadas. `SialClient` aporta la integración académica correspondiente.

`MailerClient` utiliza SMTP y devuelve resultados de entrega. Su configuración es independiente del backend `MAILERS` de Django. El cliente soporta STARTTLS en 587 y SSL en 465; verificar el transporte configurado y probar entrega real antes de producción. No registrar credenciales ni respuestas remotas sin saneamiento.

## Datos observados

Los espejos alimentan selectores y consultas de consola. Las observaciones de `DigitalAccount` permiten calcular salud local. Ninguna observación sustituye una autorización en el proveedor. Un canal privado observado en un equipo no prueba acceso individual al canal.

## Simulación y pruebas

`LUCY_DRY_RUN` simula escrituras y puede conservar lecturas remotas. Habilitar la app de mocks no redirige automáticamente todas las fábricas. Los tests deben inyectar clientes locales o interceptar proveedores explícitamente; la suite no valida por sí sola credenciales reales.
