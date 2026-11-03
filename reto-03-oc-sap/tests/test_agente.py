"""Tests del ciclo del agente y de la API (T4.3 · T4.6).

**Ni una llamada a OpenAI.** Todo corre contra `AdaptadorFalso`, que implementa la
interfaz de `app/llm/adapter.py` con un guion de respuestas programadas. Eso le da
a la suite dos propiedades que una prueba contra el proveedor real no tiene: es
determinista y no cuesta dinero.

El guion puede traer pasos **dinámicos** —una función que lee los mensajes y decide
qué pedir— porque un modelo real usa el resultado de una herramienta para armar la
llamada siguiente: así se prueba el paso `oc_construir_payload` → `oc_crear` con el
payload de verdad, sin que el test lo escriba a mano.

Lo que se verifica aquí es, una por una, cada regla del ciclo:

| Regla | Test |
|---|---|
| CA1 · tope de iteraciones | `test_el_tope_de_iteraciones_corta_el_turno` |
| CA2 · el ciclo no conoce al proveedor | `test_el_ciclo_del_agente_no_importa_openai` |
| CA3 · confirmación humana | `test_una_confirmacion_termina_el_turno` y los tres que le siguen |
| CA4 · historial visible | `test_el_historial_de_la_sesion_lista_las_llamadas` |
| CA5 · un error no mata la sesión | `test_un_error_del_proveedor_no_mata_la_sesion` |
| C6 · tope de tokens | `test_el_tope_de_tokens_detiene_la_sesion` |
| C7 · la clave no sale del backend | `test_health_no_filtra_la_clave` |
"""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import app, obtener_agente
from app.core.agente import Agente, ResultadoTurno, cargar_comportamiento
from app.core.config import Config, cargar_config
from app.llm.adapter import (
    ErrorProveedor,
    LlamadaHerramienta,
    Mensaje,
    Respuesta,
    esquema_de_herramienta,
)
from app.tools.oc import HERRAMIENTAS, Herramienta
from tests.conftest import FIXTURES, RAIZ

CASO = "sol-004"  # el caso con confirmación RC5 del oráculo
COMPORTAMIENTO = "# Prompt de prueba\nNo afirmas valores que no vengan de una herramienta."


# ── Andamiaje: el adaptador LLM falso ──────────────────────────────────────

# Un paso del guion: una respuesta fija, o una función que la calcula leyendo la
# conversación (como haría un modelo real con el resultado anterior).
Paso = Respuesta | Callable[[list[Mensaje]], Respuesta]


class AdaptadorFalso:
    """Implementa `AdaptadorLlm` con un guion. No toca la red."""

    def __init__(self, *guion: Paso, repetir: bool = False) -> None:
        # `repetir=True` deja el último paso en bucle: es lo que hace falta para
        # probar el tope de iteraciones (CA1).
        self.guion = list(guion)
        self.repetir = repetir
        self.veces = 0
        self.conversaciones: list[list[Mensaje]] = []

    async def enviar(
        self, mensajes: list[Mensaje], herramientas: list[Herramienta]
    ) -> Respuesta:
        self.veces += 1
        self.conversaciones.append(list(mensajes))
        if not self.guion:
            return responder("Se acabó el guion de prueba.")
        paso = self.guion[0] if self.repetir and len(self.guion) == 1 else self.guion.pop(0)
        return paso(mensajes) if callable(paso) else paso


class AdaptadorQueFalla:
    """Falla las primeras `fallos` veces y después responde (CA5)."""

    def __init__(self, fallos: int, final: str) -> None:
        self.fallos = fallos
        self.final = final
        self.veces = 0

    async def enviar(
        self, mensajes: list[Mensaje], herramientas: list[Herramienta]
    ) -> Respuesta:
        self.veces += 1
        if self.veces <= self.fallos:
            raise ErrorProveedor("El modelo no respondió dentro del tiempo de espera.")
        return responder(self.final)


def responder(texto: str, tokens: int = 10) -> Respuesta:
    """Un paso del guion en el que el modelo contesta y termina el turno."""
    return Respuesta(texto=texto, tool_calls=[], tokens_usados=tokens)


def pedir(nombre: str, argumentos: dict[str, Any], tokens: int = 10) -> Respuesta:
    """Un paso del guion en el que el modelo pide una herramienta."""
    return Respuesta(
        texto="",
        tool_calls=[LlamadaHerramienta(id=f"call-{nombre}", nombre=nombre, argumentos=argumentos)],
        tokens_usados=tokens,
    )


def _ultimo_payload(mensajes: list[Mensaje]) -> dict[str, Any]:
    """El payload que devolvió `oc_construir_payload`, buscando hacia atrás."""
    for mensaje in reversed(mensajes):
        if mensaje.rol != "tool" or mensaje.nombre_herramienta != "oc_construir_payload":
            continue
        cuerpo = json.loads(mensaje.contenido)
        if cuerpo.get("ok") is True:
            payload = cuerpo["data"]["payload"]
            assert isinstance(payload, dict)
            return payload
    raise AssertionError("el guion llegó a oc_crear sin un payload construido")


def crear_tras_payload(confirmado: bool) -> Callable[[list[Mensaje]], Respuesta]:
    """Paso dinámico: pide `oc_crear` con el payload que acaba de construirse."""

    def paso(mensajes: list[Mensaje]) -> Respuesta:
        return pedir(
            "oc_crear",
            {"caso": CASO, "payload": _ultimo_payload(mensajes), "confirmado": confirmado},
        )

    return paso


# Los cuatro pasos previos a la creación, en el orden que exige el proceso.
def guion_hasta_el_payload() -> list[Paso]:
    return [
        pedir("oc_leer_paquete", {"caso": CASO}),
        pedir("oc_validar", {"caso": CASO}),
        pedir("oc_generar_evidencia", {"caso": CASO}),
        pedir("oc_construir_payload", {"caso": CASO}),
    ]


# ── Andamiaje: raíz y configuración ────────────────────────────────────────


@pytest.fixture
def raiz(tmp_path: Path) -> Path:
    """Raíz aislada: `fixtures/` enlazado de solo lectura y `out/` propio."""
    (tmp_path / "fixtures").symlink_to(FIXTURES.parent)
    return tmp_path


def configuracion(raiz: Path, iteraciones: int = 25, tokens: int = 100_000) -> Config:
    """Config de prueba. No lee el entorno: los topes son el objeto del test."""
    return Config(
        openai_api_key=SecretStr("sk-de-prueba"),
        openai_model="gpt-4o-mini",
        max_iteraciones=iteraciones,
        max_tokens_sesion=tokens,
        llm_timeout_segundos=60,
        raiz=raiz,
    )


def turno(agente: Agente, mensaje: str, sesion: str = "s-1") -> ResultadoTurno:
    """Ejecuta un turno. `asyncio.run` en vez de pytest-asyncio (T0.1)."""
    return asyncio.run(agente.responder(sesion, mensaje))


def nombres(resultado: ResultadoTurno) -> list[str]:
    return [registro.nombre for registro in resultado.llamadas]


# ── CA3 · confirmación humana ──────────────────────────────────────────────


def test_una_confirmacion_termina_el_turno(raiz: Path) -> None:
    """El turno cierra con la pregunta y `needsConfirmation` queda en True (CA3)."""
    adaptador = AdaptadorFalso(
        *guion_hasta_el_payload(),
        responder("La solicitud dice 25.000.000 y la cotización 26.500.000. ¿Confirmas?"),
    )
    agente = Agente(configuracion(raiz), adaptador, COMPORTAMIENTO)

    resultado = turno(agente, f"Procesa {CASO} y no la crees hasta que yo lo confirme.")

    assert resultado.needs_confirmation is True
    assert "¿Confirmas?" in resultado.reply
    # El turno termina en el payload: `oc_crear` no se llamó.
    assert nombres(resultado) == [
        "oc_leer_paquete",
        "oc_validar",
        "oc_generar_evidencia",
        "oc_construir_payload",
    ]
    assert agente.sesion("s-1").espera_confirmacion is True


def test_el_modelo_no_puede_confirmarse_a_si_mismo(raiz: Path) -> None:
    """`confirmado=true` sin confirmación del usuario se rechaza (CA3 · C5).

    Es la puerta que hace que CA3 sea una regla del servidor y no una
    recomendación del prompt: un modelo complaciente no puede crear la OC en el
    mismo turno en el que prometió preguntar.
    """
    adaptador = AdaptadorFalso(
        *guion_hasta_el_payload(),
        crear_tras_payload(confirmado=True),
        responder("Tienes razón: necesito que lo confirmes. ¿Procedo?"),
    )
    agente = Agente(configuracion(raiz), adaptador, COMPORTAMIENTO)

    resultado = turno(agente, f"Procesa {CASO}.")

    creacion = [registro for registro in resultado.llamadas if registro.nombre == "oc_crear"]
    assert len(creacion) == 1
    assert creacion[0].ok is False
    assert "confirmado=true" in creacion[0].resumen
    assert resultado.needs_confirmation is True
    # Y no hay OC: el mock de SAP no escribió nada.
    assert not (raiz / "out" / "sap" / "ordenes.jsonl").exists()


def test_el_siguiente_mensaje_confirmando_crea_la_oc(raiz: Path) -> None:
    """Tras "confirmo", el ciclo sí deja pasar `confirmado=true` (CA3 · PRD §11)."""
    adaptador = AdaptadorFalso(
        *guion_hasta_el_payload(),
        responder("La solicitud dice un monto y la cotización otro. ¿Confirmas?"),
        # Segundo turno: el modelo reusa el payload que ya está en el historial.
        crear_tras_payload(confirmado=True),
        responder("Listo, la orden quedó creada."),
    )
    agente = Agente(configuracion(raiz), adaptador, COMPORTAMIENTO)

    primero = turno(agente, f"Procesa {CASO} y no la crees hasta que yo lo confirme.")
    assert primero.needs_confirmation is True

    segundo = turno(agente, "Confirmo, crea la orden.")

    creacion = [registro for registro in segundo.llamadas if registro.nombre == "oc_crear"]
    assert len(creacion) == 1
    assert creacion[0].ok is True
    assert "4500000001" in creacion[0].resumen
    # Ya no queda nada por confirmar: la OC existe.
    assert segundo.needs_confirmation is False
    assert agente.sesion("s-1").espera_confirmacion is False


def test_un_no_del_usuario_no_confirma(raiz: Path) -> None:
    """"No confirmo" contiene "confirmo": la negación se revisa primero (CA3)."""
    adaptador = AdaptadorFalso(
        *guion_hasta_el_payload(),
        responder("¿Confirmas?"),
        crear_tras_payload(confirmado=True),
        responder("Entendido, no creo nada."),
    )
    agente = Agente(configuracion(raiz), adaptador, COMPORTAMIENTO)

    turno(agente, f"Procesa {CASO}.")
    segundo = turno(agente, "No confirmo, revisa el monto con el proveedor.")

    creacion = [registro for registro in segundo.llamadas if registro.nombre == "oc_crear"]
    assert creacion[0].ok is False
    assert not (raiz / "out" / "sap" / "ordenes.jsonl").exists()


def test_un_caso_bloqueado_no_queda_pendiente_de_confirmacion(raiz: Path) -> None:
    """Un bloqueo no se confirma: `needsConfirmation` sigue en False (HU-2)."""
    adaptador = AdaptadorFalso(
        pedir("oc_leer_paquete", {"caso": "sol-002"}),
        pedir("oc_validar", {"caso": "sol-002"}),
        responder("El proveedor no existe en el maestro: hay que crearlo en SAP."),
    )
    agente = Agente(configuracion(raiz), adaptador, COMPORTAMIENTO)

    resultado = turno(agente, "Procesa sol-002.")

    assert resultado.needs_confirmation is False
    assert "oc_crear" not in nombres(resultado)


# ── CA1 · tope de iteraciones ──────────────────────────────────────────────


def test_el_tope_de_iteraciones_corta_el_turno(raiz: Path) -> None:
    """CA1: con el tope en 3, el ciclo consulta al modelo 3 veces y se detiene."""
    adaptador = AdaptadorFalso(pedir("oc_leer_paquete", {"caso": "sol-001"}), repetir=True)
    agente = Agente(configuracion(raiz, iteraciones=3), adaptador, COMPORTAMIENTO)

    resultado = turno(agente, "Procesa sol-001.")

    assert adaptador.veces == 3
    assert len(resultado.llamadas) == 3
    # Responde con lo que tiene y lo que falta, no con silencio.
    assert "tope de 3 iteraciones" in resultado.reply
    assert "oc_leer_paquete" in resultado.reply
    assert "oc_crear" in resultado.reply


# ── C6 · tope de tokens por sesión ─────────────────────────────────────────


def test_el_tope_de_tokens_detiene_la_sesion(raiz: Path) -> None:
    """C6: agotado el presupuesto, el agente deja de consultar al modelo."""
    adaptador = AdaptadorFalso(responder("Primera respuesta", tokens=60), repetir=True)
    agente = Agente(configuracion(raiz, tokens=50), adaptador, COMPORTAMIENTO)

    primero = turno(agente, "Hola.")
    assert primero.reply == "Primera respuesta"
    assert agente.sesion("s-1").tokens_usados == 60

    segundo = turno(agente, "Sigue.")

    assert adaptador.veces == 1, "no debe volver a consultar al modelo tras el tope"
    assert "tope de 50 tokens" in segundo.reply


# ── CA5 · un error del proveedor no mata la sesión ─────────────────────────


def test_un_error_del_proveedor_no_mata_la_sesion(raiz: Path) -> None:
    """CA5: el fallo se cuenta en lenguaje claro y el turno siguiente funciona."""
    adaptador = AdaptadorQueFalla(fallos=1, final="Ya puedo responder.")
    agente = Agente(configuracion(raiz), adaptador, COMPORTAMIENTO)

    primero = turno(agente, "Procesa sol-001.")

    assert "No pude consultar al modelo" in primero.reply
    assert "tiempo de espera" in primero.reply
    assert primero.needs_confirmation is False

    segundo = turno(agente, "Intenta otra vez.")

    assert segundo.reply == "Ya puedo responder."
    # La sesión conserva todo el historial: no se reinició.
    historial = agente.sesion("s-1").mensajes
    assert [mensaje.rol for mensaje in historial] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_una_herramienta_inexistente_no_rompe_el_turno(raiz: Path) -> None:
    """Un nombre que el modelo inventó vuelve como error, no como excepción (C3 · CA5)."""
    adaptador = AdaptadorFalso(
        pedir("oc_borrar_todo", {}),
        responder("Esa herramienta no existe; sigo con el proceso normal."),
    )
    agente = Agente(configuracion(raiz), adaptador, COMPORTAMIENTO)

    resultado = turno(agente, "Borra todo.")

    assert resultado.llamadas[0].ok is False
    assert "No existe la herramienta" in resultado.llamadas[0].resumen


# ── CA2 / CA4 · el ciclo, el historial y el proveedor ──────────────────────


def test_el_ciclo_del_agente_no_importa_openai() -> None:
    """PRD §6.1: cambiar de proveedor no debe tocar el ciclo del agente.

    Ni el ciclo ni la interfaz importan el SDK: el único archivo que lo conoce es
    `app/llm/openai_adapter.py`, y el único que lo instancia, `app/api.py`.
    """
    for modulo in ("app/core/agente.py", "app/llm/adapter.py"):
        lineas = (RAIZ / modulo).read_text(encoding="utf-8").splitlines()
        importa = [
            linea for linea in lineas if linea.strip().startswith(("import openai", "from openai"))
        ]
        assert importa == [], f"{modulo} importa el SDK del proveedor"


def test_el_esquema_de_la_herramienta_sale_de_pydantic() -> None:
    """El JSON Schema del tool-calling sale de `args.model_json_schema()` (ADR-001)."""
    esquemas = {
        herramienta.nombre: esquema_de_herramienta(herramienta) for herramienta in HERRAMIENTAS
    }
    assert set(esquemas) == {
        "oc_leer_paquete",
        "oc_validar",
        "oc_generar_evidencia",
        "oc_construir_payload",
        "oc_crear",
    }
    parametros = esquemas["oc_crear"]["parameters"]
    assert parametros["properties"]["confirmado"]["type"] == "boolean"
    assert "caso" in parametros["required"]
    assert esquemas["oc_crear"]["description"]


def test_el_historial_de_la_sesion_lista_las_llamadas(raiz: Path) -> None:
    """CA4: cada llamada queda en el historial visible, con su resultado resumido."""
    adaptador = AdaptadorFalso(
        pedir("oc_leer_paquete", {"caso": "sol-001"}),
        responder("Leí el paquete de sol-001."),
    )
    agente = Agente(configuracion(raiz), adaptador, COMPORTAMIENTO)

    turno(agente, "Procesa sol-001.")
    sesion = agente.sesion("s-1")

    assert [registro.nombre for registro in sesion.llamadas] == ["oc_leer_paquete"]
    assert sesion.llamadas[0].argumentos == {"caso": "sol-001"}
    assert sesion.llamadas[0].ok is True
    # Y también en out/log.jsonl, que escriben las herramientas.
    assert (raiz / "out" / "log.jsonl").exists()


def test_el_system_prompt_no_entra_al_historial(raiz: Path) -> None:
    """El comportamiento se manda al modelo pero no es parte de la conversación."""
    adaptador = AdaptadorFalso(responder("Hola."))
    agente = Agente(configuracion(raiz), adaptador, COMPORTAMIENTO)

    turno(agente, "Hola.")

    assert adaptador.conversaciones[0][0].rol == "system"
    assert all(mensaje.rol != "system" for mensaje in agente.sesion("s-1").mensajes)


# ── C4 · comportamiento y conocimiento en Markdown ─────────────────────────


def test_el_comportamiento_une_prompt_y_conocimiento() -> None:
    """C4: `agent/prompt.md` + `app/knowledge/ordenes-compra.md`, unidos en ejecución."""
    comportamiento = cargar_comportamiento(RAIZ)

    assert "Asistente de Órdenes de Compra" in comportamiento
    # Del prompt: el orden del proceso y la prohibición de afirmar valores (CA2).
    assert "oc_generar_evidencia" in comportamiento
    assert "No puedes afirmar" in comportamiento
    # Del conocimiento: los controles explicados en lenguaje de negocio.
    assert "RC10" in comportamiento
    assert "retroactiva" in comportamiento


def test_sin_prompt_el_agente_no_arranca(tmp_path: Path) -> None:
    """Arrancar sin comportamiento no es una degradación aceptable."""
    with pytest.raises(ValueError, match="prompt.md"):
        cargar_comportamiento(tmp_path)


# ── La API (PRD §6.4) ──────────────────────────────────────────────────────


@pytest.fixture
def cliente(raiz: Path) -> Any:
    """`TestClient` con el agente sustituido por uno de guion (sin red, sin clave)."""
    adaptador = AdaptadorFalso(
        *guion_hasta_el_payload(),
        responder("La solicitud y la cotización no coinciden. ¿Confirmas?"),
    )
    agente = Agente(configuracion(raiz), adaptador, COMPORTAMIENTO)
    app.dependency_overrides[obtener_agente] = lambda: agente
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_chat_devuelve_reply_toolcalls_y_needsconfirmation(cliente: TestClient) -> None:
    """PRD §6.4: `{sessionId, message}` → `{reply, toolCalls[], needsConfirmation}`."""
    respuesta = cliente.post(
        "/api/chat",
        json={"sessionId": "api-1", "message": f"Procesa {CASO}, no la crees aún."},
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert set(cuerpo) == {"reply", "toolCalls", "needsConfirmation"}
    assert cuerpo["needsConfirmation"] is True
    assert "¿Confirmas?" in cuerpo["reply"]
    assert [llamada["name"] for llamada in cuerpo["toolCalls"]] == [
        "oc_leer_paquete",
        "oc_validar",
        "oc_generar_evidencia",
        "oc_construir_payload",
    ]
    assert cuerpo["toolCalls"][0]["arguments"] == {"caso": CASO}


def test_la_sesion_devuelve_el_historial_completo(cliente: TestClient) -> None:
    """PRD §6.4: `GET /api/sessions/{id}` con mensajes y llamadas (CA4)."""
    cliente.post("/api/chat", json={"sessionId": "api-1", "message": f"Procesa {CASO}."})

    cuerpo = cliente.get("/api/sessions/api-1").json()

    assert cuerpo["sessionId"] == "api-1"
    assert cuerpo["needsConfirmation"] is True
    assert [llamada["name"] for llamada in cuerpo["toolCalls"]][0] == "oc_leer_paquete"
    roles = [mensaje["role"] for mensaje in cuerpo["messages"]]
    assert roles[0] == "user"
    assert "tool" in roles
    assert roles[-1] == "assistant"
    assert cuerpo["tokensUsados"] > 0


def test_una_sesion_inexistente_devuelve_404(cliente: TestClient) -> None:
    assert cliente.get("/api/sessions/no-existe").status_code == 404


def test_health_no_filtra_la_clave() -> None:
    """C7: `/api/health` reporta `{ok, provider, model}` y jamás la credencial."""
    cliente = TestClient(app)

    respuesta = cliente.get("/api/health")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["provider"] == "openai"
    assert cuerpo["model"]
    assert "ok" in cuerpo

    # Ni la clave real del entorno, ni nada que se parezca a una, salen en el JSON.
    texto = respuesta.text
    clave = cargar_config(requiere_llm=False).openai_api_key.get_secret_value()
    if clave:
        assert clave not in texto
    assert "sk-" not in texto
    assert "api_key" not in texto
    assert "apiKey" not in texto
