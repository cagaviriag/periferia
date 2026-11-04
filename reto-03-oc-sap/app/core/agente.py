"""El agente: `create_agent` más las reglas que el framework no trae (PRD §6.3 · T4.3).

El bucle modelo → herramientas → modelo ya no se escribe aquí: lo arma
`create_agent`. Lo que queda es el **ensamblaje** y la frontera con la API:

| Regla | Dónde |
|---|---|
| CA1 · tope de iteraciones | `ModelCallLimitMiddleware(run_limit=...)` + `AvisoDeTope` |
| CA2 · el modelo no afirma valores | el prompt y las herramientas; el agente no calcula |
| CA3 · confirmación humana | `confirmado` en `responder` + `PuertaDeConfirmacion` |
| CA4 · bitácora | `historial.llamadas`, que la API devuelve; `out/log.jsonl` lo escriben las herramientas |
| CA5 · un error no mata la sesión | `ErrorDelProveedor` y `ToolErrorMiddleware` |
| C6 · tope de tokens por sesión | `TopeDeTokens` |

**No importa ningún SDK de proveedor.** Recibe un `BaseChatModel` ya construido;
quién lo construye se decide en `app/api.py` (PRD §6.1).

Las sesiones tampoco se guardan aquí: el historial vive en el `InMemorySaver` de
LangGraph, con el `session_id` como `thread_id`, y todo lo que antes era un campo
de `Sesion` se deriva de los mensajes (`app/core/historial.py`).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolErrorMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import Config
from app.core.guardas import (
    AvisoDeTope,
    ContextoDelTurno,
    ErrorDelProveedor,
    PuertaDeConfirmacion,
    TopeDeTokens,
    es_confirmacion,
    fallo_de_herramienta,
)
from app.core.historial import LlamadaRegistrada, del_turno, llamadas, pendiente
from app.core.io import leer_texto
from app.tools.oc import HERRAMIENTAS, Contexto

ARCHIVO_PROMPT = ("agent", "prompt.md")
ARCHIVO_CONOCIMIENTO = ("app", "knowledge", "ordenes-compra.md")

TITULO_CONOCIMIENTO = "# Conocimiento del proceso de órdenes de compra"

RESPUESTA_SIN_TEXTO = "No tengo nada más que agregar. Dime qué solicitud quieres procesar."


@dataclass(frozen=True)
class ResultadoTurno:
    """Lo que la API devuelve de un turno: `reply`, `toolCalls[]`, `needsConfirmation`."""

    reply: str
    llamadas: list[LlamadaRegistrada]
    needs_confirmation: bool


def cargar_comportamiento(raiz: Path) -> str:
    """System prompt = `agent/prompt.md` + `app/knowledge/ordenes-compra.md`.

    C4 · PRD §6.5: el **comportamiento** vive en `agent/prompt.md` y el
    **conocimiento** del proceso en `app/knowledge/`. Se unen en ejecución, no en
    el código: cambiar una regla de negocio es editar un Markdown.

    Lanza ValueError si falta el prompt: arrancar sin comportamiento no es una
    degradación aceptable. Corre al construir el agente, no dentro de un turno.
    """
    prompt = leer_texto(raiz.joinpath(*ARCHIVO_PROMPT))
    if prompt is None:
        raise ValueError(f"Falta {'/'.join(ARCHIVO_PROMPT)}: es el system prompt del agente.")

    conocimiento = leer_texto(raiz.joinpath(*ARCHIVO_CONOCIMIENTO))
    if conocimiento is None:
        return prompt
    return f"{prompt}\n\n{TITULO_CONOCIMIENTO}\n\n{conocimiento}"


class Agente:
    """El agente de LangChain con las cinco reglas puestas, y una sesión por `thread_id`."""

    def __init__(self, config: Config, modelo: BaseChatModel, comportamiento: str) -> None:
        self.config = config
        # El orden importa: el primero es la capa más externa. `ErrorDelProveedor`
        # envuelve a todos para que ningún fallo del modelo escape del turno, y
        # `PuertaDeConfirmacion` queda dentro de `ToolErrorMiddleware` para que un
        # error suyo también vuelva como resultado de herramienta.
        #
        # La lista se anota con `Any` porque cada middleware es genérico en su
        # propio esquema de estado —`ModelCallLimitMiddleware` añade el contador de
        # llamadas— y no existe un tipo común más estrecho.
        reglas: list[AgentMiddleware[Any, Any, Any]] = [
            ErrorDelProveedor(),
            TopeDeTokens(config.max_tokens_sesion),
            AvisoDeTope(config.max_iteraciones),
            # CA1: el tope es por turno, no por conversación. Con `thread_limit` el
            # contador persiste en el checkpointer y el segundo turno de la sesión
            # arrancaría ya agotado.
            ModelCallLimitMiddleware(run_limit=config.max_iteraciones),
            ToolErrorMiddleware(fallo_de_herramienta),
            PuertaDeConfirmacion(),
        ]
        self._agente = create_agent(
            model=modelo,
            tools=HERRAMIENTAS,
            system_prompt=comportamiento,
            middleware=reglas,
            context_schema=ContextoDelTurno,
            checkpointer=InMemorySaver(),
        )

    def historial(self, session_id: str) -> list[AnyMessage]:
        """Los mensajes de la sesión. Vacío si nunca existió: lo usa `GET /api/sessions`."""
        valores: dict[str, Any] = self._agente.get_state(_hilo(session_id)).values
        return list(valores.get("messages", []))

    async def responder(self, session_id: str, texto_usuario: str) -> ResultadoTurno:
        """Procesa un mensaje del usuario y devuelve el turno completo.

        El turno termina por una de cuatro razones, y todas dejan la sesión viva:
        el modelo respondió sin pedir herramientas, falló el proveedor (CA5), se
        agotó el tope de tokens (C6) o se agotó el de iteraciones (CA1).
        """
        previo = (await self._agente.aget_state(_hilo(session_id))).values.get("messages", [])

        # CA3 · C5: la confirmación se decide **antes** de hablar con el modelo y
        # solo con dos datos: que el turno anterior terminó preguntando y que el
        # usuario dijo que sí. El modelo no participa en esta decisión.
        contexto = ContextoDelTurno(
            ctx=Contexto(raiz=self.config.raiz, session_id=session_id),
            confirmado=pendiente(previo) and es_confirmacion(texto_usuario),
        )

        salida = await self._agente.ainvoke(
            {"messages": [HumanMessage(content=texto_usuario)]},
            config=_hilo(session_id),
            context=contexto,
        )
        mensajes: list[AnyMessage] = list(salida["messages"])
        nuevos = del_turno(mensajes)
        return ResultadoTurno(
            reply=(nuevos[-1].text if nuevos else "") or RESPUESTA_SIN_TEXTO,
            llamadas=llamadas(nuevos),
            needs_confirmation=pendiente(mensajes),
        )


def _hilo(session_id: str) -> RunnableConfig:
    """La sesión del PRD es el `thread_id` del checkpointer: un hilo por conversación."""
    return {"configurable": {"thread_id": session_id}}
