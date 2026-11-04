"""Tests del agente y de la API (T4.3 · T4.6).

**Ni una llamada a OpenAI.** Todo corre contra `ModeloFalso`, un `BaseChatModel`
con un guion de respuestas programadas. Eso le da a la suite dos propiedades que
una prueba contra el proveedor real no tiene: es determinista y no cuesta dinero.

El guion puede traer pasos **dinámicos** —una función que lee los mensajes y decide
qué pedir— porque un modelo real usa el resultado de una herramienta para armar la
llamada siguiente: así se prueba el paso `oc_construir_payload` → `oc_crear` con el
payload de verdad, sin que el test lo escriba a mano.

Lo que se verifica aquí es, una por una, cada regla del ciclo:

| Regla | Test |
|---|---|
| CA1 · tope de iteraciones | `test_el_tope_de_iteraciones_corta_el_turno` |
| CA2 · el agente no conoce al proveedor | `test_el_ciclo_del_agente_no_importa_openai` |
| CA3 · confirmación humana | `test_una_confirmacion_termina_el_turno` y los tres que le siguen |
| CA4 · historial visible | `test_el_historial_de_la_sesion_lista_las_llamadas` |
| CA5 · un error no mata la sesión | `test_un_error_del_proveedor_no_mata_la_sesion` |
| C6 · tope de tokens | `test_el_tope_de_tokens_detiene_la_sesion` |
| C7 · la clave no sale del backend | `test_health_no_filtra_la_clave` |

Al migrar a `create_agent` cambió el **andamiaje**, no lo que se afirma: donde
antes había un `AdaptadorLlm` falso ahora hay un `BaseChatModel` falso, y donde
se leía `agente.sesion(...).mensajes` ahora se lee `agente.historial(...)`, que
sale del checkpointer.
"""

import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import SecretStr

from app.api import app, obtener_agente
from app.core.agente import Agente, ResultadoTurno, cargar_comportamiento
from app.core.config import Config, cargar_config
from app.core.historial import llamadas, tokens
from app.tools.oc import HERRAMIENTAS
from tests.conftest import FIXTURES, RAIZ

CASO = "sol-004"  # el caso con confirmación RC5 del oráculo
COMPORTAMIENTO = "# Prompt de prueba\nNo afirmas valores que no vengan de una herramienta."


# ── Andamiaje: el modelo falso ─────────────────────────────────────────────

# Un paso del guion: una respuesta fija, o una función que la calcula leyendo la
# conversación (como haría un modelo real con el resultado anterior).
Paso = AIMessage | Callable[[list[BaseMessage]], AIMessage]


class ErrorDeProveedorFalso(Exception):
    """Un fallo del proveedor. El nombre del tipo es lo que el agente traduce (CA5)."""


class APITimeoutError(ErrorDeProveedorFalso):
    """Imita el nombre del timeout del SDK sin importar el SDK."""


class ModeloFalso(BaseChatModel):
    """Un `BaseChatModel` con guion. No toca la red.

    `bind_tools` devuelve el mismo objeto: el guion ya dice qué herramienta pedir,
    así que no hace falta declararlas. Cada respuesta se copia sin `id` porque el
    reductor `add_messages` deduplica por id y reusar el mismo objeto sobrescribiría
    el mensaje anterior en lugar de añadirlo.
    """

    guion: list[Any] = []
    repetir: bool = False
    veces: int = 0
    conversaciones: list[list[BaseMessage]] = []
    fallos: int = 0

    @property
    def _llm_type(self) -> str:
        return "falso"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ModeloFalso":
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.veces += 1
        self.conversaciones.append(list(messages))
        if self.veces <= self.fallos:
            raise APITimeoutError("El modelo no respondió dentro del tiempo de espera.")
        if not self.guion:
            paso: Paso = responder("Se acabó el guion de prueba.")
        elif self.repetir:
            paso = self.guion[min(self.veces - self.fallos - 1, len(self.guion) - 1)]
        else:
            paso = self.guion.pop(0)
        mensaje = paso(messages) if callable(paso) else paso
        return ChatResult(generations=[ChatGeneration(message=mensaje.model_copy(deep=True))])


def _consumo(tokens: int) -> dict[str, int]:
    """El `usage_metadata` que el proveedor reporta y del que sale el tope C6."""
    return {
        "input_tokens": tokens // 2,
        "output_tokens": tokens - tokens // 2,
        "total_tokens": tokens,
    }


def responder(texto: str, tokens: int = 10) -> AIMessage:
    """Un paso del guion en el que el modelo contesta y termina el turno."""
    return AIMessage(content=texto, id=None, usage_metadata=_consumo(tokens))


def pedir(nombre: str, argumentos: dict[str, Any], tokens: int = 10) -> AIMessage:
    """Un paso del guion en el que el modelo pide una herramienta.

    El `"type": "tool_call"` no es decorativo: el nodo de herramientas de LangGraph
    distingue por ese campo una lista de llamadas de una lista de mensajes, y sin
    él el turno muere con "No AIMessage found in input".
    """
    return AIMessage(
        content="",
        id=None,
        tool_calls=[
            {"name": nombre, "args": argumentos, "id": f"call-{nombre}", "type": "tool_call"}
        ],
        usage_metadata=_consumo(tokens),
    )


def _ultimo_payload(mensajes: list[BaseMessage]) -> dict[str, Any]:
    """El payload que devolvió `oc_construir_payload`, buscando hacia atrás."""
    for mensaje in reversed(mensajes):
        if not isinstance(mensaje, ToolMessage) or mensaje.name != "oc_construir_payload":
            continue
        cuerpo = json.loads(mensaje.text)
        if cuerpo.get("ok") is True:
            payload = cuerpo["data"]["payload"]
            assert isinstance(payload, dict)
            return payload
    raise AssertionError("el guion llegó a oc_crear sin un payload construido")


def crear_tras_payload(confirmado: bool) -> Callable[[list[BaseMessage]], AIMessage]:
    """Paso dinámico: pide `oc_crear` con el payload que acaba de construirse."""

    def paso(mensajes: list[BaseMessage]) -> AIMessage:
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
    modelo = ModeloFalso(
        guion=[
            *guion_hasta_el_payload(),
            responder("La solicitud dice 25.000.000 y la cotización 26.500.000. ¿Confirmas?"),
        ]
    )
    agente = Agente(configuracion(raiz), modelo, COMPORTAMIENTO)

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


def test_el_modelo_no_puede_confirmarse_a_si_mismo(raiz: Path) -> None:
    """`confirmado=true` sin confirmación del usuario se rechaza (CA3 · C5).

    Es la puerta que hace que CA3 sea una regla del servidor y no una
    recomendación del prompt: un modelo complaciente no puede crear la OC en el
    mismo turno en el que prometió preguntar. Vive en `PuertaDeConfirmacion`.
    """
    modelo = ModeloFalso(
        guion=[
            *guion_hasta_el_payload(),
            crear_tras_payload(confirmado=True),
            responder("Tienes razón: necesito que lo confirmes. ¿Procedo?"),
        ]
    )
    agente = Agente(configuracion(raiz), modelo, COMPORTAMIENTO)

    resultado = turno(agente, f"Procesa {CASO}.")

    creacion = [registro for registro in resultado.llamadas if registro.nombre == "oc_crear"]
    assert len(creacion) == 1
    assert creacion[0].ok is False
    assert "confirmado=true" in creacion[0].resumen
    assert resultado.needs_confirmation is True
    # Y no hay OC: el mock de SAP no escribió nada.
    assert not (raiz / "out" / "sap" / "ordenes.jsonl").exists()


def test_el_siguiente_mensaje_confirmando_crea_la_oc(raiz: Path) -> None:
    """Tras "confirmo", el agente sí deja pasar `confirmado=true` (CA3 · PRD §11)."""
    modelo = ModeloFalso(
        guion=[
            *guion_hasta_el_payload(),
            responder("La solicitud dice un monto y la cotización otro. ¿Confirmas?"),
            # Segundo turno: el modelo reusa el payload que ya está en el historial.
            crear_tras_payload(confirmado=True),
            responder("Listo, la orden quedó creada."),
        ]
    )
    agente = Agente(configuracion(raiz), modelo, COMPORTAMIENTO)

    primero = turno(agente, f"Procesa {CASO} y no la crees hasta que yo lo confirme.")
    assert primero.needs_confirmation is True

    segundo = turno(agente, "Confirmo, crea la orden.")

    creacion = [registro for registro in segundo.llamadas if registro.nombre == "oc_crear"]
    assert len(creacion) == 1
    assert creacion[0].ok is True
    assert "4500000001" in creacion[0].resumen
    # Ya no queda nada por confirmar: la OC existe.
    assert segundo.needs_confirmation is False


def test_un_no_del_usuario_no_confirma(raiz: Path) -> None:
    """"No confirmo" contiene "confirmo": la negación se revisa primero (CA3)."""
    modelo = ModeloFalso(
        guion=[
            *guion_hasta_el_payload(),
            responder("¿Confirmas?"),
            crear_tras_payload(confirmado=True),
            responder("Entendido, no creo nada."),
        ]
    )
    agente = Agente(configuracion(raiz), modelo, COMPORTAMIENTO)

    turno(agente, f"Procesa {CASO}.")
    segundo = turno(agente, "No confirmo, revisa el monto con el proveedor.")

    creacion = [registro for registro in segundo.llamadas if registro.nombre == "oc_crear"]
    assert creacion[0].ok is False
    assert not (raiz / "out" / "sap" / "ordenes.jsonl").exists()


def test_un_caso_bloqueado_no_queda_pendiente_de_confirmacion(raiz: Path) -> None:
    """Un bloqueo no se confirma: `needsConfirmation` sigue en False (HU-2)."""
    modelo = ModeloFalso(
        guion=[
            pedir("oc_leer_paquete", {"caso": "sol-002"}),
            pedir("oc_validar", {"caso": "sol-002"}),
            responder("El proveedor no existe en el maestro: hay que crearlo en SAP."),
        ]
    )
    agente = Agente(configuracion(raiz), modelo, COMPORTAMIENTO)

    resultado = turno(agente, "Procesa sol-002.")

    assert resultado.needs_confirmation is False
    assert "oc_crear" not in nombres(resultado)


# ── CA1 · tope de iteraciones ──────────────────────────────────────────────


def test_el_tope_de_iteraciones_corta_el_turno(raiz: Path) -> None:
    """CA1: con el tope en 3, el agente consulta al modelo 3 veces y se detiene.

    El tope lo pone `ModelCallLimitMiddleware(run_limit=3)` y el mensaje final lo
    reescribe `AvisoDeTope`: el del middleware es inglés genérico.
    """
    modelo = ModeloFalso(guion=[pedir("oc_leer_paquete", {"caso": "sol-001"})], repetir=True)
    agente = Agente(configuracion(raiz, iteraciones=3), modelo, COMPORTAMIENTO)

    resultado = turno(agente, "Procesa sol-001.")

    assert modelo.veces == 3
    assert len(resultado.llamadas) == 3
    # Responde con lo que tiene y lo que falta, no con silencio.
    assert "tope de 3 iteraciones" in resultado.reply
    assert "oc_leer_paquete" in resultado.reply
    assert "oc_crear" in resultado.reply


def test_el_tope_es_por_turno_y_no_por_conversacion(raiz: Path) -> None:
    """CA1: el tope se reinicia en cada turno (`run_limit`, no `thread_limit`).

    Con `thread_limit` el contador vive en el checkpointer y el segundo turno de
    la sesión arrancaría agotado, lo que no es lo que el PRD pide.
    """
    modelo = ModeloFalso(guion=[responder("Primera."), responder("Segunda.")])
    agente = Agente(configuracion(raiz, iteraciones=1), modelo, COMPORTAMIENTO)

    assert turno(agente, "Hola.").reply == "Primera."
    assert turno(agente, "Sigue.").reply == "Segunda."


# ── C6 · tope de tokens por sesión ─────────────────────────────────────────


def test_el_tope_de_tokens_detiene_la_sesion(raiz: Path) -> None:
    """C6: agotado el presupuesto, el agente deja de consultar al modelo."""
    modelo = ModeloFalso(guion=[responder("Primera respuesta", tokens=60)], repetir=True)
    agente = Agente(configuracion(raiz, tokens=50), modelo, COMPORTAMIENTO)

    primero = turno(agente, "Hola.")
    assert primero.reply == "Primera respuesta"
    assert tokens(agente.historial("s-1")) == 60

    segundo = turno(agente, "Sigue.")

    assert modelo.veces == 1, "no debe volver a consultar al modelo tras el tope"
    assert "tope de 50 tokens" in segundo.reply


# ── CA5 · un error del proveedor no mata la sesión ─────────────────────────


def test_un_error_del_proveedor_no_mata_la_sesion(raiz: Path) -> None:
    """CA5: el fallo se cuenta en lenguaje claro y el turno siguiente funciona."""
    modelo = ModeloFalso(guion=[responder("Ya puedo responder.")], fallos=1)
    agente = Agente(configuracion(raiz), modelo, COMPORTAMIENTO)

    primero = turno(agente, "Procesa sol-001.")

    assert "No pude consultar al modelo" in primero.reply
    assert "tiempo de espera" in primero.reply
    assert primero.needs_confirmation is False

    segundo = turno(agente, "Intenta otra vez.")

    assert segundo.reply == "Ya puedo responder."
    # La sesión conserva todo el historial: no se reinició.
    from app.core.historial import rol

    assert [rol(mensaje) for mensaje in agente.historial("s-1")] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_una_herramienta_inexistente_no_rompe_el_turno(raiz: Path) -> None:
    """Un nombre que el modelo inventó vuelve como error, no como excepción (C3 · CA5)."""
    modelo = ModeloFalso(
        guion=[
            pedir("oc_borrar_todo", {}),
            responder("Esa herramienta no existe; sigo con el proceso normal."),
        ]
    )
    agente = Agente(configuracion(raiz), modelo, COMPORTAMIENTO)

    resultado = turno(agente, "Borra todo.")

    assert resultado.llamadas[0].ok is False
    assert "No existe la herramienta" in resultado.llamadas[0].resumen


# ── CA2 / CA4 · el agente, el historial y el proveedor ─────────────────────


def test_el_ciclo_del_agente_no_importa_openai() -> None:
    """PRD §6.1: cambiar de proveedor no debe tocar el agente.

    Ni el ensamblaje ni las reglas mencionan al proveedor: el único archivo que lo
    conoce es `app/llm/modelo.py`, y quien lo llama, `app/api.py`.
    """
    for modulo in ("app/core/agente.py", "app/core/guardas.py", "app/core/historial.py"):
        lineas = (RAIZ / modulo).read_text(encoding="utf-8").splitlines()
        importa = [
            linea
            for linea in lineas
            if linea.strip().startswith(("import openai", "from openai", "from langchain_openai"))
        ]
        assert importa == [], f"{modulo} importa el SDK del proveedor"


def test_el_esquema_de_la_herramienta_sale_de_pydantic() -> None:
    """El JSON Schema del tool-calling sale de Pydantic (ADR-001).

    Con `@tool` el esquema lo infiere LangChain de la firma, y `tool_call_schema`
    es exactamente lo que ve el modelo: sin `ctx`, que es `InjectedToolArg`.
    """
    esquemas = {
        herramienta.name: herramienta.tool_call_schema.model_json_schema()
        for herramienta in HERRAMIENTAS
    }
    assert set(esquemas) == {
        "oc_leer_paquete",
        "oc_validar",
        "oc_generar_evidencia",
        "oc_construir_payload",
        "oc_crear",
    }
    parametros = esquemas["oc_crear"]
    assert parametros["properties"]["confirmado"]["type"] == "boolean"
    assert "caso" in parametros["required"]
    assert "ctx" not in parametros["properties"]
    assert HERRAMIENTAS[-1].description


def test_el_historial_de_la_sesion_lista_las_llamadas(raiz: Path) -> None:
    """CA4: cada llamada queda en el historial visible, con su resultado resumido."""
    modelo = ModeloFalso(
        guion=[
            pedir("oc_leer_paquete", {"caso": "sol-001"}),
            responder("Leí el paquete de sol-001."),
        ]
    )
    agente = Agente(configuracion(raiz), modelo, COMPORTAMIENTO)

    turno(agente, "Procesa sol-001.")
    registradas = llamadas(agente.historial("s-1"))

    assert [registro.nombre for registro in registradas] == ["oc_leer_paquete"]
    assert registradas[0].argumentos == {"caso": "sol-001"}
    assert registradas[0].ok is True
    # Y también en out/log.jsonl, que escriben las herramientas.
    assert (raiz / "out" / "log.jsonl").exists()


def test_el_system_prompt_no_entra_al_historial(raiz: Path) -> None:
    """El comportamiento se manda al modelo pero no es parte de la conversación."""
    modelo = ModeloFalso(guion=[responder("Hola.")])
    agente = Agente(configuracion(raiz), modelo, COMPORTAMIENTO)

    turno(agente, "Hola.")

    assert isinstance(modelo.conversaciones[0][0], SystemMessage)
    assert all(
        not isinstance(mensaje, SystemMessage) for mensaje in agente.historial("s-1")
    )


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
    modelo = ModeloFalso(
        guion=[
            *guion_hasta_el_payload(),
            responder("La solicitud y la cotización no coinciden. ¿Confirmas?"),
        ]
    )
    agente = Agente(configuracion(raiz), modelo, COMPORTAMIENTO)
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
