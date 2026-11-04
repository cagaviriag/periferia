#!/usr/bin/env python
"""Genera las piezas de `modulo/` desde las fuentes de la aplicación.

El PRD §9.4 evalúa que las tres piezas del módulo sean **las mismas que usa la
aplicación, no copias divergentes**. Hay tres formas de garantizarlo y aquí se
usan las tres:

1. `modulo/tools/oc.py` **reexporta** `app.tools.oc`. No es una copia: es el
   mismo módulo, importado. Si cambia una herramienta, el módulo cambia con ella
   sin que nadie tenga que acordarse.
2. `modulo/agent.md` y `modulo/skill/ordenes-compra/SKILL.md` se **generan** con
   este script a partir de `agent/prompt.md` y `app/knowledge/ordenes-compra.md`,
   añadiéndoles el frontmatter que pide el PRD.
3. `tests/test_modulo.py` **falla** si un archivo generado dejó de coincidir con
   su fuente. Regenerar es `uv run python modulo/construir.py`.

    uv run python modulo/construir.py
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "modulo"

PROMPT = RAIZ / "agent" / "prompt.md"
CONOCIMIENTO = RAIZ / "app" / "knowledge" / "ordenes-compra.md"

AVISO = (
    "<!-- GENERADO por modulo/construir.py desde {fuente}. No lo edites a mano:\n"
    "     el PRD §9.4 exige que esta pieza no divergir de la que usa la aplicación,\n"
    "     y tests/test_modulo.py falla si divergen. Edita la fuente y regenera. -->\n"
)

FRONTMATTER_AGENTE = """---
description: >-
  Asistente de Órdenes de Compra de Periferia IT Group. Lee un paquete de compra
  (solicitud, cotización y aprobación del líder), lo valida contra los diez
  controles de la compañía y crea la orden de compra en SAP solo cuando
  corresponde. Devuelve al humano las excepciones en lugar de forzarlas.
mode: primary
permission:
  edit: deny
  bash: deny
---
"""

FRONTMATTER_SKILL = """---
name: ordenes-compra
description: >-
  Conocimiento del proceso de órdenes de compra de Periferia IT Group: los diez
  controles RC1-RC10, la diferencia entre bloqueo, confirmación y valor derivado,
  y qué hacer ante cada excepción. Consúltalo para explicar por qué una orden
  está bloqueada, no solo que lo está.
---
"""

REEXPORTACION = '''"""Las herramientas del agente, importables sin el servidor (PRD §9.4).

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
'''


def _escribir(destino: Path, contenido: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(contenido, encoding="utf-8")
    print(f"  escrito  {destino.relative_to(RAIZ)}")


def construir() -> None:
    _escribir(
        MODULO / "agent.md",
        FRONTMATTER_AGENTE
        + AVISO.format(fuente="agent/prompt.md")
        + "\n"
        + PROMPT.read_text(encoding="utf-8"),
    )
    _escribir(
        MODULO / "skill" / "ordenes-compra" / "SKILL.md",
        FRONTMATTER_SKILL
        + AVISO.format(fuente="app/knowledge/ordenes-compra.md")
        + "\n"
        + CONOCIMIENTO.read_text(encoding="utf-8"),
    )
    _escribir(MODULO / "tools" / "oc.py", REEXPORTACION)
    _escribir(MODULO / "tools" / "__init__.py", "")
    _escribir(MODULO / "__init__.py", "")


if __name__ == "__main__":
    print("Generando modulo/ desde las fuentes de la aplicación:")
    construir()
    print("\nListo. `uv run pytest tests/test_modulo.py` verifica que no divergen.")
