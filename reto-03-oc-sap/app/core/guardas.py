"""Las reglas del ciclo, escritas como middleware de LangChain (CA1 · CA3 · CA5 · C6).

`create_agent` trae el bucle modelo → herramientas → modelo, pero no trae las
reglas que el PRD califica. Cada una queda en un gancho:

| Regla | Middleware | Gancho |
|---|---|---|
| CA1 · mensaje del tope de iteraciones | `AvisoDeTope` | `after_agent` |
| CA3 · el modelo no se autoautoriza | `PuertaDeConfirmacion` | `awrap_tool_call` |
| CA5 · un error del proveedor no mata la sesión | `ErrorDelProveedor` | `awrap_model_call` |
| C6 · tope de tokens por sesión | `TopeDeTokens` | `before_model` |

El tope de iteraciones en sí lo pone `ModelCallLimitMiddleware`, que se arma en
`app/core/agente.py`; aquí solo está el mensaje con el que termina el turno.

**No se usa `HumanInTheLoopMiddleware`** a propósito: interrumpiría también
`sol-001`, que el objetivo O1 del PRD exige crear sin preguntar nada. La
confirmación de CA3 no es una pausa del grafo, es una condición sobre el último
mensaje del usuario.

`ContextoDelTurno` es el `context_schema` del agente: lo que el llamador inyecta
en cada turno y el modelo no puede tocar.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
    hook_config,
)
from langchain_core.messages import AIMessage, AnyMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from app.core.historial import del_turno, llamadas, tokens
from app.core.modelos import error
from app.tools.oc import HERRAMIENTAS, Contexto

# Frases con las que el usuario confirma. Son las que la UI escribe cuando se
# pulsa "Confirmar" y las que el PRD §11 usa en la demo ("confirmo").
PALABRAS_CONFIRMACION = (
    "confirmo",
    "confirmado",
    "confirmada",
    "confirmar",
    "lo autorizo",
    "autorizo",
    "apruebo",
    "procede",
    "adelante",
    "créala",
    "crea la oc",
)

# Se revisan antes que las anteriores: "no confirmo" contiene "confirmo".
NEGACIONES = (
    "no confirmo",
    "no confirmes",
    "no lo confirmo",
    "no la confirmo",
    "no autorizo",
    "no la crees",
    "no lo crees",
    "cancela",
    "detente",
)

NOMBRE_CREAR = "oc_crear"

AVISO_PROVEEDOR = "No pude consultar al modelo en este momento."

# El texto con el que `ModelCallLimitMiddleware` cierra el turno cuando se agota
# el tope. Es inglés genérico y no dice qué se hizo ni qué falta, así que
# `AvisoDeTope` lo reemplaza por el mensaje que CA1 exige.
MARCA_TOPE = "Model call limits exceeded"

Estado = AgentState[Any]


def es_confirmacion(texto: str) -> bool:
    """¿El usuario confirmó en este mensaje? (CA3)

    Las negaciones se revisan primero porque "no confirmo" contiene "confirmo".
    Ante la duda, **no** es confirmación: el costo de no crear una OC es pedirla
    otra vez; el de crearla sin permiso es una orden de compra indebida.
    """
    limpio = texto.strip().lower()
    if any(negacion in limpio for negacion in NEGACIONES):
        return False
    return any(palabra in limpio for palabra in PALABRAS_CONFIRMACION)


@dataclass(frozen=True)
class ContextoDelTurno:
    """El `context_schema` del agente: un `Contexto` y el veredicto de CA3.

    `confirmado` se decide en `Agente.responder` **antes** de hablar con el
    modelo, con dos datos: que el turno anterior terminó preguntando y que el
    usuario dijo que sí. El modelo no participa en esa decisión y no puede
    alcanzar este objeto.
    """

    ctx: Contexto
    confirmado: bool


class PuertaDeConfirmacion(AgentMiddleware[Estado, ContextoDelTurno, Any]):
    """CA3 · C5 y la inyección del `Contexto`: ejecuta la herramienta del turno.

    Dos trabajos en un solo gancho, porque los dos necesitan los argumentos de la
    llamada antes de que la herramienta corra:

    1. **La puerta.** Si el modelo manda `confirmado=true` y el usuario no
       confirmó en su último mensaje, la llamada se rechaza y el modelo recibe la
       instrucción de preguntar. Sin esta puerta, un modelo complaciente crearía
       la OC en el mismo turno en el que prometió preguntar y CA3 quedaría en una
       recomendación del prompt.

    2. **El `Contexto`.** `ToolNode` **borra** cualquier argumento marcado con
       `InjectedToolArg` que venga en la llamada —es su defensa contra un modelo
       que quiera forjarlo— y solo repone los de `InjectedState`, `InjectedStore`
       y `ToolRuntime`. Para un `InjectedToolArg` propio no hay reposición, así
       que el `ctx` tiene que entrar invocando la herramienta aquí, en lugar de
       delegar en el `handler` del nodo.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Ejecuta la herramienta que pidió el modelo, con el contexto del turno."""
        # `ToolCallRequest` no es genérico en el contexto —a diferencia de
        # `ModelRequest`—, así que su `context` llega tipado como `None` y el tipo
        # real se recupera con un `cast`. Es parte del precio de la inyección.
        contexto = cast(ContextoDelTurno, request.runtime.context)
        nombre = request.tool_call["name"]
        argumentos = request.tool_call["args"]

        if request.tool is None:
            disponibles = ", ".join(herramienta.name for herramienta in HERRAMIENTAS)
            return self._fallo(
                request, f"No existe la herramienta {nombre!r}. Disponibles: {disponibles}."
            )

        if nombre == NOMBRE_CREAR and argumentos.get("confirmado") and not contexto.confirmado:
            return self._fallo(
                request,
                "No puedes marcar confirmado=true: el usuario todavía no ha confirmado. "
                "Muéstrale los datos concretos de cada excepción, hazle la pregunta y "
                "termina el turno. Crea la OC cuando él responda que confirma.",
            )

        resultado: str = await request.tool.ainvoke({**argumentos, "ctx": contexto.ctx})
        return ToolMessage(
            content=resultado, name=nombre, tool_call_id=str(request.tool_call["id"])
        )

    @staticmethod
    def _fallo(request: ToolCallRequest, detalle: str) -> ToolMessage:
        """Un rechazo en el mismo contrato JSON que devuelven las herramientas (C3)."""
        return ToolMessage(
            content=error(detalle),
            name=request.tool_call["name"],
            tool_call_id=str(request.tool_call["id"]),
            status="error",
        )


class ErrorDelProveedor(AgentMiddleware[Estado, ContextoDelTurno, Any]):
    """CA5: un fallo del proveedor se cuenta en lenguaje claro y la sesión sigue viva.

    Ni `ModelRetryMiddleware` ni `ModelFallbackMiddleware` sirven para esto: el
    primero reintenta y el segundo cambia de modelo, pero cuando se agotan, la
    excepción sigue subiendo y mata el turno. Convertirla en un `AIMessage` es lo
    que deja el historial consistente: el turno termina como cualquier otro y el
    siguiente mensaje del usuario continúa donde se quedó.

    El detalle se arma con el **nombre** del tipo de excepción y nunca con su
    texto: así este módulo no importa el SDK de ningún proveedor (PRD §6.1) y la
    credencial no puede viajar en el mensaje (C7).
    """

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextoDelTurno],
        handler: Callable[[ModelRequest[ContextoDelTurno]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any] | AIMessage:
        try:
            return await handler(request)
        except Exception as excepcion:  # noqa: BLE001
            return AIMessage(content=f"{AVISO_PROVEEDOR} {_detalle(excepcion)}")


def _detalle(excepcion: Exception) -> str:
    """Qué le pasó al proveedor, en lenguaje de usuario y sin traza."""
    nombre = type(excepcion).__name__
    if "Timeout" in nombre:
        return (
            "El modelo no respondió dentro del tiempo de espera. "
            "Vuelve a enviar tu mensaje; la conversación sigue abierta."
        )
    if "Connection" in nombre:
        return (
            "No se pudo conectar con el proveedor del modelo. "
            "Revisa la conexión y vuelve a intentarlo."
        )
    return f"Falló la llamada al modelo: {nombre}."


class TopeDeTokens(AgentMiddleware[Estado, ContextoDelTurno, Any]):
    """C6 · PRD §8 Costo: tope de tokens por sesión, además del de iteraciones."""

    def __init__(self, tope: int) -> None:
        super().__init__()
        self.tope = tope

    @hook_config(can_jump_to=["end"])
    def before_model(
        self, state: Estado, runtime: Runtime[ContextoDelTurno]
    ) -> dict[str, Any] | None:
        """Corta el turno antes de consultar al modelo si el presupuesto se agotó."""
        if tokens(state["messages"]) < self.tope:
            return None
        return {
            "jump_to": "end",
            "messages": [
                AIMessage(
                    content=f"Esta conversación alcanzó el tope de {self.tope} tokens "
                    "configurado para controlar el costo. No puedo seguir consultando al "
                    "modelo en esta sesión; las llamadas a herramientas que ya hice quedan "
                    "en el historial. Abre una sesión nueva para continuar."
                )
            ],
        }


class AvisoDeTope(AgentMiddleware[Estado, ContextoDelTurno, Any]):
    """CA1: al agotar las iteraciones, el agente dice lo que hizo y lo que falta.

    `ModelCallLimitMiddleware` cierra el turno con `Model call limits exceeded:
    run limit (3/3)`. Eso no es el mensaje que CA1 pide, así que se reemplaza por
    uno que enumera las llamadas exitosas y las que faltan. La información para
    armarlo no está en el middleware del tope: se vuelve a leer del historial.
    """

    def __init__(self, tope: int) -> None:
        super().__init__()
        self.tope = tope

    def after_agent(
        self, state: Estado, runtime: Runtime[ContextoDelTurno]
    ) -> dict[str, Any] | None:
        mensajes: list[AnyMessage] = state["messages"]
        ultimo = mensajes[-1] if mensajes else None
        if not isinstance(ultimo, AIMessage) or not ultimo.text.startswith(MARCA_TOPE):
            return None
        return {
            "messages": [
                RemoveMessage(id=str(ultimo.id)),
                AIMessage(content=_aviso_de_tope(self.tope, mensajes)),
            ]
        }


def _aviso_de_tope(tope: int, mensajes: list[AnyMessage]) -> str:
    """Lo que el agente tiene y lo que falta al agotar las iteraciones (CA1)."""
    hechas = [registro.nombre for registro in llamadas(del_turno(mensajes)) if registro.ok]
    faltan = [
        herramienta.name for herramienta in HERRAMIENTAS if herramienta.name not in hechas
    ]
    return (
        f"Alcancé el tope de {tope} iteraciones de este turno y lo detuve para no seguir "
        "gastando. Lo que sí quedó hecho: "
        f"{', '.join(dict.fromkeys(hechas)) or 'ninguna llamada exitosa'}. "
        f"Lo que falta: {', '.join(faltan) or 'nada'}. "
        "Dime si quieres que continúe y sigo desde este punto."
    )


def fallo_de_herramienta(excepcion: Exception, request: ToolCallRequest) -> str:
    """CA5 · C3: una herramienta que lanza vuelve como error, no mata la sesión.

    Es el `on_error` de `ToolErrorMiddleware`. Las herramientas no lanzan —cada
    una envuelve su cuerpo en un `except Exception`—, pero el ciclo no depende de
    que eso siga siendo cierto.
    """
    return error(
        f"La herramienta {request.tool_call['name']} falló: {type(excepcion).__name__}. "
        "Explícaselo al usuario y no inventes el resultado."
    )
