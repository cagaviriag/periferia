"""Las herramientas del agente, importables sin el servidor (PRD §9.4).

Este archivo **no es una copia**: reexporta `app.tools.oc`, que es el mismo
módulo que usa la aplicación. Si una herramienta cambia, el módulo cambia con
ella. El PRD evalúa justamente que no haya copias divergentes.

Uso desde otra plataforma de agentes:

    from modulo.tools.oc import HERRAMIENTAS, Contexto

    ctx = Contexto(raiz=Path("."), session_id="mi-sesion")
    for herramienta in HERRAMIENTAS:
        print(herramienta.nombre, herramienta.description)
        print(herramienta.args.model_json_schema())

Cada `Herramienta` lleva los tres miembros del contrato del PRD §6.2:
`description` (la frase que el modelo lee para decidir cuándo llamarla), `args`
(el esquema Pydantic del que sale el JSON Schema del tool-calling) y `execute`
(que devuelve un string JSON y nunca lanza).
"""

from app.tools.oc import (
    HERRAMIENTAS,
    ArgsCaso,
    ArgsConstruirPayload,
    ArgsCrear,
    ArgsValidar,
    Contexto,
    Herramienta,
    casos_disponibles,
    oc_construir_payload,
    oc_crear,
    oc_generar_evidencia,
    oc_leer_paquete,
    oc_validar,
)

__all__ = [
    "HERRAMIENTAS",
    "ArgsCaso",
    "ArgsConstruirPayload",
    "ArgsCrear",
    "ArgsValidar",
    "Contexto",
    "Herramienta",
    "casos_disponibles",
    "oc_construir_payload",
    "oc_crear",
    "oc_generar_evidencia",
    "oc_leer_paquete",
    "oc_validar",
]
