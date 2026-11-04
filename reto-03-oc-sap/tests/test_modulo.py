"""El bonus del PRD §9.4 exige que las tres piezas del módulo **no divergen**
de las que usa la aplicación. Estos tests fallan si alguien edita una copia.

Regenerar: `uv run python modulo/construir.py`
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "modulo"


def _cuerpo(ruta: Path) -> str:
    """Devuelve el cuerpo del archivo generado, sin frontmatter ni aviso.

    El frontmatter se corta con `maxsplit=2`, así que los `---` que el cuerpo
    usa como separadores horizontales sobreviven. El aviso se elimina por
    índices del comentario HTML, no filtrando líneas por su contenido.
    """
    texto = ruta.read_text(encoding="utf-8")

    if texto.startswith("---"):
        texto = texto.split("---", 2)[2]

    inicio = texto.find("<!--")
    fin = texto.find("-->", inicio)
    if inicio != -1 and fin != -1:
        texto = texto[:inicio] + texto[fin + len("-->") :]

    return texto.strip()


def test_agent_md_no_diverge_del_prompt() -> None:
    assert _cuerpo(MODULO / "agent.md") == (RAIZ / "agent" / "prompt.md").read_text(
        encoding="utf-8"
    ).strip()


def test_skill_md_no_diverge_del_conocimiento() -> None:
    fuente = (RAIZ / "app" / "knowledge" / "ordenes-compra.md").read_text(encoding="utf-8")
    assert _cuerpo(MODULO / "skill" / "ordenes-compra" / "SKILL.md") == fuente.strip()


def test_agent_md_tiene_el_frontmatter_que_pide_el_prd() -> None:
    texto = (MODULO / "agent.md").read_text(encoding="utf-8")
    assert texto.startswith("---")
    for exigido in ("description:", "mode: primary", "edit: deny", "bash: deny"):
        assert exigido in texto, exigido


def test_skill_md_tiene_el_frontmatter_que_pide_el_prd() -> None:
    texto = (MODULO / "skill" / "ordenes-compra" / "SKILL.md").read_text(encoding="utf-8")
    assert texto.startswith("---")
    assert "name: ordenes-compra" in texto
    assert "description:" in texto


def test_las_herramientas_del_modulo_son_las_mismas_objetos() -> None:
    # No "equivalentes": los mismos objetos en memoria. Es una reexportación,
    # no una copia, así que una herramienta nueva aparece sola.
    from app.tools.oc import HERRAMIENTAS as DE_LA_APP
    from modulo.tools.oc import HERRAMIENTAS as DEL_MODULO

    assert DEL_MODULO is DE_LA_APP
    # `name`, no `nombre`: con `@tool` el nombre lo pone LangChain desde la
    # función, y es el que ve el modelo.
    assert [h.name for h in DEL_MODULO] == [
        "oc_leer_paquete",
        "oc_validar",
        "oc_generar_evidencia",
        "oc_construir_payload",
        "oc_crear",
    ]


def test_el_modulo_se_importa_sin_el_servidor() -> None:
    # PRD §9.4: "las mismas herramientas, importables sin el servidor".
    # Importar el módulo no debe arrastrar FastAPI ni el ciclo del agente.
    import sys

    for modulo_servidor in ("app.api", "app.core.agente", "fastapi"):
        sys.modules.pop(modulo_servidor, None)

    from modulo.tools.oc import HERRAMIENTAS, Contexto

    assert len(HERRAMIENTAS) == 5
    assert Contexto(raiz=RAIZ, session_id="x").session_id == "x"
    assert "app.api" not in sys.modules
    assert "app.core.agente" not in sys.modules


def test_cada_herramienta_expone_los_tres_miembros_del_contrato() -> None:
    """Los tres miembros del PRD §6.2, con los nombres que les pone LangChain.

    `description` sigue llamándose igual; `args` (el esquema Pydantic) pasa a ser
    `args_schema` y `execute` pasa a ser `ainvoke`. El `ctx` inyectado no aparece
    en el esquema: el modelo no lo ve y no puede fijar la raíz del proyecto.
    """
    from modulo.tools.oc import HERRAMIENTAS

    for herramienta in HERRAMIENTAS:
        assert herramienta.description.strip(), herramienta.name
        esquema = herramienta.tool_call_schema.model_json_schema()
        assert esquema["type"] == "object", herramienta.name
        # Cada campo describe para qué sirve: es lo único que el modelo lee.
        for campo in esquema.get("properties", {}).values():
            assert campo.get("description"), herramienta.name
        assert "ctx" not in esquema.get("properties", {}), herramienta.name
        assert callable(herramienta.ainvoke), herramienta.name
