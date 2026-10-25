# 1.00. Patrón Adaptador para APIs Externas e Idempotencia

**Proyecto:** Mt^3 / Y3P (FCE - UNRC)  
**Arquitectura y Diseño:** Carmesí  
**Implementación Técnica:** Blanca (Codex)  
**Fecha:** 24 de octubre de 2026  
**Estado:** Propuesta de Diseño — Aprobada la Abstracción

---

## 1. El Diagnóstico: ¿Por qué falló el CRM anterior?

En el sistema heredado (`CRM-FCE` / `repo_tempo_y3`), las llamadas a APIs externas sufrían de dos vicios arquitectónicos graves:

1. **Acoplamiento Directo en Vistas:** Las pantallas web y los hilos en segundo plano invocaban directamente a los endpoints remotos o a librerías HTTP sueltas.
2. **El Fallo H-03 (Falsa Simulación):** El flag `CRM_DRY_RUN` se chequeaba de manera despareja con sentencias `if`. Si un desarrollador olvidaba un condicional en una ruta o en la preparación de grupos, el sistema ejecutaba escrituras reales sobre Microsoft Graph o Moodle mientras creía estar simulando.

---

## 2. ¿Qué es el Patrón Adaptador (Adapter Pattern)?

El **Patrón Adaptador** es un diseño estructural que actúa como intermediario entre la lógica de negocio de nuestra aplicación y los servicios externos. 

En lugar de que el sistema sepa cómo habla Microsoft Graph, cómo responde Moodle REST o cómo entrega SIAL/UTI:

* La aplicación define un **Contrato o Interfaz Estándar** en Python (usando `abc.ABC` o `typing.Protocol`).
* La aplicación **solo habla con esa interfaz**.
* Por debajo, existen dos implementaciones intercambiables:
  1. **Adaptador Real (`HttpAdapter`):** Abre conexiones de red seguras (con reintentos, timeouts y headers) hacia los servidores de la facultad o Microsoft.
  2. **Adaptador Simulado (`MockAdapter`):** No toca internet; lee y escribe de forma inmediata y persistente contra nuestras tablas de PostgreSQL (`apps/platform_mocks`).

```text
       ┌────────────────────────────────────────────────────────┐
       │   Capa de Aplicación (People / Provisioning / Auditing) │
       └───────────────────────────┬────────────────────────────┘
                                   │ Invoca métodos del contrato
                                   ▼
                      ┌───────────────────────────┐
                      │    Interfaz Abstracta     │
                      │    (ej: MoodleClient)     │
                      └─────────────┬─────────────┘
                                    │
           ┌────────────────────────┴────────────────────────┐
           ▼                                                 ▼
   [MockMoodleAdapter]                              [RealMoodleAdapter]
 (Si ENABLE_LOCAL_MOCKS=True)                      (Si ENABLE_LOCAL_MOCKS=False)
           │                                                 │
           ▼                                                 ▼
  PostgreSQL 16 Local                               Internet / HTTPS REST
 (apps/platform_mocks)                           (moodle.eco.unrc.edu.ar)
```

## 3. Beneficios Técnicos Clave

1. **Aislamiento Total del Modo Desarrollo:** Con `ENABLE_LOCAL_MOCKS=True`, el sistema físicamente no abre sockets hacia internet para estas tareas; es matemáticamente imposible que borre cuentas o cree grupos reales por error.

2. **Pruebas Automatizadas Rápidas:** Las suites de test corren en milisegundos contra PostgreSQL local, sin depender de que los servidores de la UNRC estén caídos, lentos o en mantenimiento.

3. **Punto Único de Verdad (Single Point of Truth):** Manejo centralizado de timeouts, cuotas de peticiones (rate limiting), registro de auditoría (logs) y reintentos exponenciales.

## 4. Estructura Conceptual del Código (Para Blanca / PyCharm)

Cada integración externa residirá en su propio paquete bajo `apps/integrations/` o en servicios especializados, estructurado en tres componentes:

### A. Tipos de Datos y Resultados Fuertemente Tipados (DTOs / Dataclasses)

Ningún cliente devolverá diccionarios crudos (`dict`). Devolverán objetos inmutables con estados claros:

Python

```
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProvisioningResult:
    success: bool
    external_id: Optional[str]
    action_taken: str  # 'created', 'already_exists', 'simulated', 'failed'
    error_message: Optional[str] = None
```

### B. El Contrato Abstracto (Interfaz Base)

Define las operaciones que el sistema necesita, sin importar el destino:

Python

```
from abc import ABC, abstractmethod


class BaseMoodleAdapter(ABC):

    @abstractmethod
    def get_or_create_user(        self, username: str, email: str, firstname: str, lastname: str    ) -> ProvisioningResult:
        """Busca o da de alta un usuario en Moodle."""
        pass

    @abstractmethod
    def enroll_user(        self, user_id: str, course_shortname: str, role_id: int    ) -> ProvisioningResult:
        """Matricula al usuario en un curso específico."""
        pass
```

### C. La Fábrica de Adaptadores (Factory Pattern)

El núcleo del sistema nunca instancia la clase concreta con `RealMoodleAdapter()`. Llama a un constructor que decide en base al entorno:

Python

```
from django.conf import settings


def get_moodle_adapter() -> BaseMoodleAdapter:
    if settings.ENABLE_LOCAL_MOCKS:
        from apps.platform_mocks.adapters import MockMoodleAdapter

        return MockMoodleAdapter()
    from apps.integrations.adapters import RealMoodleAdapter

    return RealMoodleAdapter(
        base_url=settings.MOODLE_BASE_URL, token=settings.MOODLE_REST_TOKEN
    )
```

## 5. Próximos Pasos de Revisión

1. **Revisión de Flujos de Negocio:**

   - Contrastar con el código del CRM viejo el catálogo de funciones de Moodle y Microsoft Graph.

   - Analizar el protocolo real de SIAL / UTI para verificar el comportamiento de los padrones (Preinscriptos, Aspirantes, Ingresantes).

2. **Definición de Esquemas:**

   - Una vez consensuados los campos que el negocio necesita, Blanca procederá al modelado de tablas relacionales.
