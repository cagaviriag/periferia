"""API del chat (PRD §6.4 · T4.6).

Tres endpoints, los del PRD y ninguno más:

| Método | Ruta | Respuesta |
|---|---|---|
| `POST` | `/api/chat` | `{reply, toolCalls[], needsConfirmation}` |
| `GET`  | `/api/sessions/{id}` | historial completo de la sesión |
| `GET`  | `/api/health` | `{ok, provider, model}` — **nunca** la clave (C7) |

Dos decisiones que conviene poder defender:

1. **La configuración se resuelve en la primera petición, no al importar el
   módulo.** `cargar_config()` lee el `.env` de la raíz (ADR-010) y lanza si falta
   la clave; si eso ocurriera al importar, el proceso no arrancaría y el operador
   vería un fallo de importación en lugar de un `503` con el mensaje que le dice
   qué copiar en el `.env`. Además permite que los tests inyecten un agente con un
   adaptador LLM falso y corran sin tocar la red.

2. **El nombre de los campos es el del PRD** (`sessionId`, `toolCalls`,
   `needsConfirmation`). El dominio está en español, pero el contrato HTTP lo fija
   el cliente, así que los campos se declaran en español y el nombre del PRD va en
   `validation_alias` (lo que entra) o `serialization_alias` (lo que sale); FastAPI
   serializa por alias. Se usan los dos alias específicos y no el `alias` genérico
   a propósito: `alias` también renombra el parámetro del constructor, y entonces
   el propio código tendría que escribir `RespuestaChat(toolCalls=...)` en medio de
   una capa en español.

Esta capa no decide nada del negocio: traduce HTTP a `Agente.responder` y vuelve.
El único módulo que menciona OpenAI es `app/llm/modelo.py`, y quien lo llama,
`obtener_agente`.

3. **El historial de la sesión se deriva, no se guarda dos veces.** `GET
   /api/sessions/{id}` arma mensajes, `toolCalls[]`, `tokensUsados` y
   `needsConfirmation` desde la lista de mensajes que devuelve el checkpointer del
   agente, con las funciones de `app/core/historial.py`. Antes esos cuatro datos
   eran campos de un objeto `Sesion`; ahora ninguno puede desincronizarse del
   historial real.
"""

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from langchain_core.messages import AIMessage, AnyMessage
from pydantic import BaseModel, ConfigDict, Field

from app.core.agente import Agente, cargar_comportamiento
from app.core.config import cargar_config, resumen_publico
from app.core.historial import LlamadaRegistrada, llamadas, pendiente, rol, tokens
from app.llm.modelo import crear_modelo

app = FastAPI(
    title="Agente de Órdenes de Compra SAP",
    description="Reto 03 · Periferia IT Group",
    version="1.0.0",
)

# Sesiones en memoria (PRD §6.1: "memoria o archivo bastan"). Viven en el
# checkpointer del agente, y el agente vive aquí: un proceso, un único estado.
_agente: Agente | None = None


def obtener_agente() -> Agente:
    """El agente del proceso, construido en la primera petición que lo necesita.

    Es la dependencia que los tests sustituyen con `app.dependency_overrides`
    para correr sin clave y sin red.
    """
    global _agente  # noqa: PLW0603
    if _agente is not None:
        return _agente

    try:
        config = cargar_config()
    except ValueError as fallo:
        # 503 y no 500: el servicio está sano, lo que falta es configuración, y el
        # mensaje de `cargar_config` dice exactamente qué copiar en el `.env`.
        raise HTTPException(status_code=503, detail=str(fallo)) from fallo

    # `crear_modelo` es el único punto que elige proveedor de LLM: ni el agente ni
    # la API saben que existe OpenAI.
    _agente = Agente(
        config=config,
        modelo=crear_modelo(config),
        comportamiento=cargar_comportamiento(config.raiz),
    )
    return _agente


# ── Modelos de la API ──────────────────────────────────────────────────────


class PeticionChat(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: str = Field(validation_alias="sessionId", min_length=1)
    mensaje: str = Field(validation_alias="message", min_length=1)


class LlamadaVisible(BaseModel):
    """Una llamada a herramienta para el front (CA4 · PRD §6.1).

    `result` es el resultado **resumido**: el front muestra nombre, argumentos y
    resultado de cada llamada, y el payload completo queda en `out/`.
    """

    name: str
    arguments: dict[str, Any]
    ok: bool
    result: str


class RespuestaChat(BaseModel):
    reply: str
    tool_calls: list[LlamadaVisible] = Field(serialization_alias="toolCalls")
    # CA3: el front resalta este estado y el botón "Confirmar" manda un mensaje
    # de usuario normal, nunca una llamada directa a `oc_crear` (C5).
    needs_confirmation: bool = Field(serialization_alias="needsConfirmation")


class MensajeHistorial(BaseModel):
    role: str
    content: str
    tool_name: str | None = Field(default=None, serialization_alias="toolName")
    tool_calls: list[str] = Field(default_factory=list, serialization_alias="toolCalls")


class RespuestaSesion(BaseModel):
    session_id: str = Field(serialization_alias="sessionId")
    messages: list[MensajeHistorial]
    tool_calls: list[LlamadaVisible] = Field(serialization_alias="toolCalls")
    tokens_usados: int = Field(serialization_alias="tokensUsados")
    needs_confirmation: bool = Field(serialization_alias="needsConfirmation")


class RespuestaHealth(BaseModel):
    """C7 · PRD §8: proveedor y modelo, jamás la credencial.

    No hay un campo donde la clave pudiera entrar: `resumen_publico` es la única
    fuente de estos datos y no la expone.
    """

    ok: bool
    provider: str
    model: str
    max_iteraciones: int = Field(serialization_alias="maxIteraciones")
    detalle: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.post("/api/chat", response_model=RespuestaChat)
async def chat(
    peticion: PeticionChat, agente: Annotated[Agente, Depends(obtener_agente)]
) -> RespuestaChat:
    """Un turno completo del ciclo del agente.

    No captura excepciones del agente: `Agente.responder` ya devuelve un turno
    válido cuando falla el proveedor o se agota un tope (CA5 · CA1 · C6). Si algo
    escapara de ahí, es un defecto y debe verse como 500, no esconderse.
    """
    turno = await agente.responder(peticion.session_id, peticion.mensaje)
    return RespuestaChat(
        reply=turno.reply,
        tool_calls=[_visible(registro) for registro in turno.llamadas],
        needs_confirmation=turno.needs_confirmation,
    )


@app.get("/api/sessions/{session_id}", response_model=RespuestaSesion)
def obtener_sesion(
    session_id: str, agente: Annotated[Agente, Depends(obtener_agente)]
) -> RespuestaSesion:
    """Historial completo de la sesión: mensajes y llamadas a herramientas (CA4)."""
    mensajes = agente.historial(session_id)
    if not mensajes:
        raise HTTPException(status_code=404, detail=f"No existe la sesión {session_id!r}.")
    return _respuesta_sesion(session_id, mensajes)


@app.get("/api/health", response_model=RespuestaHealth)
def health() -> RespuestaHealth:
    """Estado del backend. `ok` es False si falta configuración, nunca por la clave.

    La credencial no se lee ni se menciona: se intenta cargar la configuración
    completa y, si falla, se recarga sin exigir clave para poder reportar el
    modelo configurado. Lo que viaja al cliente sale de `resumen_publico` (C7).
    """
    try:
        config = cargar_config()
        listo, detalle = True, None
    except ValueError as fallo:
        config = cargar_config(requiere_llm=False)
        listo, detalle = False, str(fallo)

    publico = resumen_publico(config)
    return RespuestaHealth(
        ok=listo,
        provider=str(publico["provider"]),
        model=str(publico["model"]),
        max_iteraciones=int(publico["max_iteraciones"]),
        detalle=detalle,
    )


# ── Traducción de dominio a HTTP ───────────────────────────────────────────


def _visible(registro: LlamadaRegistrada) -> LlamadaVisible:
    return LlamadaVisible(
        name=registro.nombre,
        arguments=registro.argumentos,
        ok=registro.ok,
        result=registro.resumen,
    )


def _mensaje_historial(mensaje: AnyMessage) -> MensajeHistorial:
    """El mensaje de LangChain, sin los identificadores internos del tool-calling."""
    pedidas = (
        [llamada["name"] for llamada in mensaje.tool_calls]
        if isinstance(mensaje, AIMessage)
        else []
    )
    return MensajeHistorial(
        role=rol(mensaje),
        content=mensaje.text,
        tool_name=getattr(mensaje, "name", None) if rol(mensaje) == "tool" else None,
        tool_calls=pedidas,
    )


def _respuesta_sesion(session_id: str, mensajes: list[AnyMessage]) -> RespuestaSesion:
    """Todo sale del historial de mensajes: no hay un segundo estado que mantener."""
    return RespuestaSesion(
        session_id=session_id,
        messages=[_mensaje_historial(mensaje) for mensaje in mensajes],
        tool_calls=[_visible(registro) for registro in llamadas(mensajes)],
        tokens_usados=tokens(mensajes),
        needs_confirmation=pendiente(mensajes),
    )
