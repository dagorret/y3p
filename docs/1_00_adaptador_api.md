# Patrón adaptador e idempotencia — v0.4.6

[Índice documental](README.md) · [Manual relacionado](manual/04_outbox_y_recuperacion.md)

## Por qué se separan las fronteras

Una transición académica no debe depender de que Microsoft, Moodle y SMTP respondan dentro de la petición web. El dominio registra intención; el orquestador ejecuta pasos y llama a contratos que cada proveedor implementa. Las vistas sólo autorizan, validan entradas y presentan resultados.

## Repetición y éxito parcial

Crear por un identificador estable y comprobar estado remoto permite repetir tras una interrupción sin duplicar recursos. La saga conserva puntos de control; si Moodle falla después de crear una cuenta, el reintento debe continuar sin repetir innecesariamente los pasos confirmados. El rearme administrativo no borra esos puntos de control.

Esta estrategia requiere idempotencia por operación y no promete entrega exactamente una vez en una red distribuida. Una notificación exige especial cuidado ante caídas entre entrega SMTP y confirmación local. No guardar la contraseña para resolver esa incertidumbre.

## Observación local

Las grillas leen PostgreSQL para mantener latencia y número de consultas estables. Las operaciones de refresco puntual y sincronización son explícitas. El monitor consulta estados locales y el indicador HTMX se carga por separado. La interfaz informa avance asíncrono, no inventa éxito remoto porque se haya confirmado un formulario.
