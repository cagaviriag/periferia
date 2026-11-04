"""Las cinco herramientas P0 del contrato del PRD §6.2, declaradas con `@tool`.

Son la **única** fuente de valores que el agente puede afirmar (CA2): el modelo
orquesta y redacta, nunca calcula.

Este módulo es la capa delgada. La lógica vive en cuatro módulos que no conocen
LangChain ni ningún framework de agentes: `app/paquete.py` (leer el caso y correr
los diez controles), `app/evidencia.py` (el correo de aprobación y su sha256),
`app/payload.py` (el payload de SAP, su trazabilidad y la creación de la orden) y
`app/respuesta.py` (las utilidades del contrato que todas comparten). Aquí solo
quedan el contrato, las descripciones que lee el modelo y el orden del proceso.

Tres reglas que gobiernan el módulo:

1. **Toda herramienta devuelve un `str` con JSON** — `{"ok": true, "data": ...}` o
   `{"ok": false, "error": ...}` — con `ok()` y `error()` de `app/core/modelos.py`.
   Nunca un dict, nunca un modelo.

2. **Ninguna lanza** (C3). El `except` es `Exception`, **no** `ValueError`:
   `leer_json` y `leer_texto` lanzan también `IsADirectoryError` y
   `PermissionError`, que no son `ValueError` (defecto **D9** del QA de la Fase
   0). `@tool` no sustituye este envoltorio: LangChain propaga la excepción al
   ciclo en lugar de convertirla en la respuesta JSON que exige el contrato.

3. **Orden de llamada obligatorio**: `oc_generar_evidencia` va **antes** de
   `oc_construir_payload`, porque el payload consume el `sha256` de la evidencia.

`Contexto` se inyecta con `InjectedToolArg`: sigue siendo un argumento real de la
función —cada llamada lleva su propia raíz y su propia sesión, sin estado global
que dos sesiones concurrentes se peleen— pero `@tool` lo excluye del esquema que
ve el modelo, así que el LLM no puede fijar la raíz del proyecto ni suplantar una
sesión. Lo que se paga: quien llama sin LLM lo mete en el dict de `.ainvoke()`, y
ese dict ya no lo revisa mypy.

El `Any` de las firmas es la frontera de deserialización: los argumentos que manda
el modelo no tienen esquema hasta que Pydantic los valida (ADR-009).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import BaseTool, InjectedToolArg, tool
from pydantic import Field

from app.core.log import registrar_control, registrar_herramienta
from app.core.modelos import error, ok
from app.evidencia import generar_evidencia
from app.paquete import casos_disponibles, construir_paquete, validar
from app.payload import construir_payload, crear_orden
from app.respuesta import describir, fue_ok, resumen

__all__ = [
    "HERRAMIENTAS",
    "Contexto",
    "casos_disponibles",
    "oc_construir_payload",
    "oc_crear",
    "oc_generar_evidencia",
    "oc_leer_paquete",
    "oc_validar",
]


# ── Contexto (PRD §6.2) ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Contexto:
    """Lo que el llamador inyecta en cada llamada.

    `raiz` es la raíz del proyecto: todas las rutas se resuelven desde aquí y
    ninguna herramienta acepta rutas absolutas del modelo. `session_id` es la
    identidad que queda en `excepciones[].confirmado_por` (ADR-006).
    """

    raiz: Path
    session_id: str


# ── Argumentos: la descripción viaja con el campo ──────────────────────────

# `Annotated[tipo, Field(description=...)]` sustituye a los cuatro modelos
# `ArgsX` de la versión sin LangChain: `@tool` infiere el esquema desde la firma,
# y la descripción de cada campo es lo único que el modelo lee para rellenarlo.
# Los dos que se repiten en las cinco herramientas se nombran una sola vez.

Caso = Annotated[
    str,
    Field(
        description="Nombre de la carpeta del caso en fixtures/reto-03/solicitudes/, "
        "ej. 'sol-001'"
    ),
]

# El contexto no entra en el esquema del modelo: lo pone el llamador.
Ctx = Annotated[Contexto, InjectedToolArg()]

PaqueteOpcional = Annotated[
    dict[str, Any] | None,
    Field(
        description="Paquete devuelto por oc_leer_paquete. Opcional: la validación se "
        "hace siempre sobre los fixtures del caso."
    ),
]


# ── Contrato común de las cinco (C3 · CA4) ─────────────────────────────────


def _responder(nombre: str, caso: str, raiz: Path, respuesta: str) -> str:
    """Deja la línea de la llamada en `out/log.jsonl` (CA4) y devuelve la respuesta."""
    registrar_herramienta(raiz, nombre, fue_ok(respuesta), resumen(respuesta), caso)
    return respuesta


def _args_invalidos(excepcion: Exception) -> str:
    """Los argumentos que mandó el modelo no cumplen el esquema (PRD §6.2).

    Va conectada a `handle_validation_error` de cada herramienta: sin esto `@tool`
    levanta la `ValidationError` hacia el ciclo y se rompe C3, que exige que la
    herramienta responda `{"ok": false}` en vez de lanzar.
    """
    return error(f"Argumentos inválidos: {describir(excepcion)}")


# ── 1 · oc_leer_paquete (HU-1) ─────────────────────────────────────────────


@tool
async def oc_leer_paquete(caso: Caso, ctx: Ctx) -> str:
    """Lee el paquete de compra de un caso (correo, solicitud, cotización, aprobación y
    factura si existe) y devuelve sus datos normalizados, incluidos los adjuntos que faltan.
    """
    try:
        respuesta = ok(construir_paquete(ctx.raiz, caso).model_dump(mode="json"))
    except Exception as excepcion:  # noqa: BLE001
        respuesta = error(describir(excepcion))
    return _responder("oc_leer_paquete", caso, ctx.raiz, respuesta)


# ── 2 · oc_validar (HU-2) ─────────────────────────────────────────────────


@tool
async def oc_validar(caso: Caso, ctx: Ctx, paquete: PaqueteOpcional = None) -> str:
    """Evalúa los 10 controles del caso y devuelve si la orden es apta, sus bloqueos,
    las confirmaciones que requiere, los valores derivados y si es retroactiva.
    """
    try:
        respuesta = validar(ctx.raiz, caso, paquete)
    except Exception as excepcion:  # noqa: BLE001
        respuesta = error(describir(excepcion))
    return _responder("oc_validar", caso, ctx.raiz, respuesta)


# ── 3 · oc_generar_evidencia (HU-4) ───────────────────────────────────────


@tool
async def oc_generar_evidencia(caso: Caso, ctx: Ctx) -> str:
    """Escribe el correo de aprobación como archivo de evidencia en out/<caso>/aprobacion.txt
    y devuelve su ruta y su sha256. Debe llamarse ANTES de oc_construir_payload.
    """
    try:
        respuesta = generar_evidencia(ctx.raiz, caso)
    except Exception as excepcion:  # noqa: BLE001
        respuesta = error(describir(excepcion))
    return _responder("oc_generar_evidencia", caso, ctx.raiz, respuesta)


# ── 4 · oc_construir_payload (HU-3) ───────────────────────────────────────


@tool
async def oc_construir_payload(
    caso: Caso,
    ctx: Ctx,
    paquete: PaqueteOpcional = None,
    derivados: Annotated[
        dict[str, str] | None,
        Field(
            description="Valores derivados que devolvió oc_validar, ej. "
            '{"indicador_iva": "C1", "condiciones_pago": "Z030"}.'
        ),
    ] = None,
) -> str:
    """Construye el payload de la orden de compra para SAP y escribe su trazabilidad en
    out/<caso>/trazabilidad.json. Requiere haber llamado antes a oc_generar_evidencia.
    """
    try:
        respuesta = construir_payload(ctx.raiz, caso, derivados)
    except Exception as excepcion:  # noqa: BLE001
        respuesta = error(describir(excepcion))
    return _responder("oc_construir_payload", caso, ctx.raiz, respuesta)


# ── 5 · oc_crear (HU-5) ───────────────────────────────────────────────────


@tool
async def oc_crear(
    caso: Caso,
    payload: Annotated[
        dict[str, Any],
        Field(
            description="El payload de OrdenCompra que devolvió oc_construir_payload, "
            "sin modificar."
        ),
    ],
    ctx: Ctx,
    confirmado: Annotated[
        bool,
        Field(
            description="True solo si el usuario confirmó explícitamente las excepciones "
            "en su último mensaje. No levanta bloqueos."
        ),
    ] = False,
) -> str:
    """Crea la orden de compra en SAP. Rechaza la creación si hay bloqueos, y si hay
    excepciones solo crea cuando el usuario ya confirmó (confirmado=true). Es idempotente.
    """
    try:
        respuesta = await crear_orden(ctx.raiz, ctx.session_id, caso, payload, confirmado)
    except Exception as excepcion:  # noqa: BLE001
        # I6: un intento que no llegó ni a leerse también deja su fila. Como no
        # hay solicitud_id —el paquete no se pudo armar— se registra el caso.
        registrar_control(ctx.raiz, caso, "error", None, False, [], [])
        respuesta = error(describir(excepcion))
    return _responder("oc_crear", caso, ctx.raiz, respuesta)


# ── Registro de herramientas (PRD §6.2) ───────────────────────────────────

# El orden de la lista es el orden del proceso: leer, validar, evidenciar,
# construir, crear. Es el mismo que el agente debe seguir. Con `@tool` el
# registro ya no describe nada —cada objeto lleva su `name`, su `description` y su
# `args_schema`—, solo las enumera.
HERRAMIENTAS: list[BaseTool] = [
    oc_leer_paquete,
    oc_validar,
    oc_generar_evidencia,
    oc_construir_payload,
    oc_crear,
]

def _conectar_validacion(herramientas: list[BaseTool]) -> None:
    """C3 · PRD §6.2: un argumento inválido se le devuelve al modelo, no se lanza.

    `@tool` valida contra el esquema que infirió y, por omisión, levanta la
    `ValidationError`. Estas dos líneas son lo que mantiene el contrato.
    """
    for herramienta in herramientas:
        herramienta.handle_validation_error = _args_invalidos


_conectar_validacion(HERRAMIENTAS)
