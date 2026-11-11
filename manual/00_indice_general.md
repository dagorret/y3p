# Manual Operativo de Administración — Y3P / MT^3

**Facultad de Ciencias Económicas — Universidad Nacional de Río Cuarto**  
**Versión de referencia:** v0.2.0 · **Edición:** 11 de noviembre de 2026

## Alcance

Este manual acompaña a operadores de secretarías, autoridades y administradores técnicos en el registro de vinculaciones y el seguimiento de cuentas institucionales. Describe el comportamiento del código publicado en v0.2.0; cada instalación requiere verificar su configuración y servicios.

La versión incluye identidades y vinculaciones, reglas de estados, adaptadores externos, registro de trabajos pendientes (Outbox), administración Unfold y portales HTMX. Incorpora además utilidades UUIDv8 y huellas SHA-256, y el modelo `DigitalAccount`.

> [!NOTE]
> UUIDv8 y DigitalAccount son componentes disponibles para la evolución de Lucy. En esta versión no sustituyen automáticamente las claves existentes, no sincronizan cuentas por sí solos y no disponen de una pantalla propia en el administrador.

## Mapa de lectura

| Perfil | Lectura recomendada | Responsabilidad |
|---|---|---|
| Operadores y secretarías | Capítulos 1, 2, 3 y protocolo inicial del 5 | Registrar datos y comunicar incidencias sin duplicar inscripciones |
| Autoridades | Capítulos 1 y 3; estados del capítulo 2 | Interpretar avances, límites y decisiones institucionales |
| Administración técnica / TI | Manual completo, especialmente 4 y 5 | Configurar servicios, permisos, integraciones y recuperación |

Los perfiles de esta tabla expresan responsabilidades de trabajo; no constituyen grupos de permisos preconfigurados.

## Capítulos

1. [Propósito y visión general](01_proposito_y_vision_general.md).
2. [Entornos de trabajo](02_entornos_de_trabajo.md).
3. [Ciclo de vida y estados](03_ciclo_de_vida_y_estados.md).
4. [Puesta en marcha y operación técnica](04_puesta_en_marcha_y_operacion.md).
5. [Contingencias y preguntas frecuentes](05_contingencias_y_faq.md).

## Documentación de respaldo

- [Diseño de entidades y estados](../docs/01_01_entidades_base_y_diseno.md).
- [Clientes de integración](../docs/01_02_clientes_integracion.md).
- [Outbox y orquestación](../docs/01_03_outbox_y_orquestador.md).

Las indicaciones operativas se contrastaron con el código. Los documentos de diseño aportan contexto y pueden describir objetivos más amplios que la implementación disponible.
