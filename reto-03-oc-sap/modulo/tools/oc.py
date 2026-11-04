"""Las herramientas del agente, importables sin el servidor (PRD §9.4).

Este archivo **no es una copia**: reexporta `app.tools.oc`, que es el mismo
módulo que usa la aplicación. Si una herramienta cambia, el módulo cambia con
ella. El PRD evalúa justamente que no haya copias divergentes.

Uso desde otra plataforma de agentes:

    from modulo.tools.oc import HERRAMIENTAS, Contexto

    ctx = Contexto(raiz=Path("."), session_id="mi-sesion")
    for herramienta in HERRAMIENTAS:
        print(herramienta.name, herramienta.description)
        print(herramienta.args)                      # JSON Schema de los campos

    respuesta = await HERRAMIENTAS[0].ainvoke({"caso": "sol-001", "ctx": ctx})

Cada herramienta es un `BaseTool` de LangChain (rama `refactor/langchain`), así
que los tres miembros del contrato del PRD §6.2 se llaman `description` (la frase
que el modelo lee para decidir cuándo llamarla), `args_schema` (el esquema
Pydantic del que sale el JSON Schema del tool-calling) y `ainvoke` (que devuelve
un string JSON y nunca lanza). El `Contexto` no aparece en el esquema del modelo
—es un `InjectedToolArg`— pero sí hay que pasarlo en el dict de `ainvoke`.
"""

from app.tools.oc import (
    HERRAMIENTAS,
    Contexto,
    casos_disponibles,
    oc_construir_payload,
    oc_crear,
    oc_generar_evidencia,
    oc_leer_paquete,
    oc_validar,
)

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
