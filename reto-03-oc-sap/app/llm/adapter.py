"""Interfaz propia del proveedor LLM (PRD §6.1 · T4.1).

Este módulo **no importa ningún SDK**. Define la única forma en la que el ciclo
del agente habla con un modelo:

    enviar(mensajes, herramientas) -> Respuesta

El PRD lo pide textualmente: *"Una interfaz propia con una implementación para el
proveedor que elijas. Cambiar de proveedor no debe tocar el ciclo del agente."*
Por eso `app/core/agente.py` importa de aquí y nunca de `openai_adapter.py`: la
única línea del sistema que menciona OpenAI está en `app/api.py`, donde se
construye el adaptador concreto.

Las tres piezas del vocabulario son deliberadamente pobres:

- `Mensaje`   — un turno de la conversación, con los cuatro roles que existen.
- `Respuesta` — lo que devuelve el modelo: texto, llamadas a herramienta y tokens.
- `ErrorProveedor` — el único error que el ciclo tiene que capturar (CA5).

No hay streaming, ni imágenes, ni roles de sistema múltiples: lo que no se usa en
este reto no está en la interfaz.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.tools.oc import Herramienta

# Los cuatro roles del tool-calling. `tool` es el resultado de una herramienta;
# es un rol del protocolo, no un participante humano.
Rol = Literal["system", "user", "assistant", "tool"]


class ErrorProveedor(Exception):
    """Fallo al hablar con el proveedor LLM: timeout, HTTP, cuota o clave inválida.

    CA5 · C7: el mensaje lo redacta el adaptador en lenguaje claro y **nunca**
    incluye la credencial. El ciclo del agente lo captura, lo muestra en el chat
    y deja la sesión viva.
    """


@dataclass(frozen=True)
class LlamadaHerramienta:
    """Una llamada a herramienta que pidió el modelo.

    `argumentos` llega **crudo**: es lo que el modelo escribió, sin validar. Lo
    valida la propia herramienta contra su esquema Pydantic (PRD §6.2).
    """

    id: str
    nombre: str
    argumentos: dict[str, Any]


@dataclass(frozen=True)
class Mensaje:
    """Un turno de la conversación, en vocabulario propio.

    `tool_calls` solo lo llena un mensaje `assistant`; `tool_call_id` y
    `nombre_herramienta`, solo un mensaje `tool`. Es una tupla y no una lista
    porque el mensaje es inmutable: el historial de una sesión no se reescribe.
    """

    rol: Rol
    contenido: str
    tool_calls: tuple[LlamadaHerramienta, ...] = ()
    tool_call_id: str | None = None
    nombre_herramienta: str | None = None


@dataclass(frozen=True)
class Respuesta:
    """Lo que devuelve el modelo en una iteración.

    `texto` vacío es normal cuando el modelo solo pide herramientas.
    `tokens_usados` es el total del intercambio y alimenta el tope de tokens por
    sesión (C6): si el proveedor no lo reporta, llega en 0.
    """

    texto: str
    tool_calls: list[LlamadaHerramienta]
    tokens_usados: int


class AdaptadorLlm(Protocol):
    """El contrato que implementa cada proveedor.

    Es un `Protocol` y no una clase base: el adaptador falso de los tests cumple
    el contrato sin heredar nada, y así la suite corre sin tocar la red.
    """

    async def enviar(
        self, mensajes: list[Mensaje], herramientas: list[Herramienta]
    ) -> Respuesta:
        """Manda la conversación al modelo. Lanza `ErrorProveedor` si falla."""
        ...


def esquema_de_herramienta(herramienta: Herramienta) -> dict[str, Any]:
    """Nombre, descripción y JSON Schema de los argumentos de una herramienta.

    El esquema sale de `args.model_json_schema()`: Pydantic sustituye a `zod`
    (ADR-001) y es lo que el tool-calling necesita. Esta función devuelve la
    forma **neutral**; envolverla en el formato de un proveedor concreto es
    trabajo de su adaptador.
    """
    return {
        "name": herramienta.nombre,
        "description": herramienta.description,
        "parameters": herramienta.args.model_json_schema(),
    }
