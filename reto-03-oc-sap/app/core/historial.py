"""Lectura del historial de mensajes de LangChain (CA4 · CA3 · C6).

Con `create_agent` el estado de la conversación ya no es una lista de `Mensaje`
propios: es la lista de mensajes de LangChain que vive en el checkpointer. Todo
lo que antes era un campo de `Sesion` —las llamadas visibles, los tokens
gastados, si queda algo por confirmar— se **deriva** de esa lista.

Son funciones puras sobre `list[AnyMessage]`: no conocen el agente, no conocen la
API y no guardan nada. Las usan los middleware de `app/core/guardas.py`, el
agente y `app/api.py`.

El `Any` de `argumentos` es la frontera de deserialización de siempre: son los
argumentos que escribió el modelo (ADR-009).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

# Largo del resultado que viaja al historial visible del chat. El PRD §6.1 pide
# "resultado resumido": el payload completo queda en out/<caso>/trazabilidad.json.
LARGO_RESUMEN = 600

# La herramienta que crea la OC: es la única sobre la que actúa la puerta de CA3.
NOMBRE_CREAR = "oc_crear"

# Marca textual con la que `oc_crear` reporta que falta la confirmación del
# usuario. Se detecta aquí para marcar el turno como pendiente **sin** interpretar
# la respuesta del modelo: la señal viene de la herramienta, no del texto que el
# modelo redacte.
MARCA_CONFIRMACION = "requiere confirmación"


@dataclass(frozen=True)
class LlamadaRegistrada:
    """Una llamada a herramienta tal como la ve el usuario (CA4 · PRD §6.1)."""

    nombre: str
    argumentos: dict[str, Any]
    ok: bool
    resumen: str


def rol(mensaje: AnyMessage) -> str:
    """El rol del mensaje en el vocabulario del PRD: user, assistant, tool, system."""
    if isinstance(mensaje, HumanMessage):
        return "user"
    if isinstance(mensaje, AIMessage):
        return "assistant"
    if isinstance(mensaje, ToolMessage):
        return "tool"
    return mensaje.type


def del_turno(mensajes: list[AnyMessage]) -> list[AnyMessage]:
    """Los mensajes posteriores al último del usuario: el turno que acaba de correr.

    El historial del checkpointer es acumulativo, y `reply`, `toolCalls[]` y
    `needsConfirmation` son del turno, no de la sesión.
    """
    for indice in range(len(mensajes) - 1, -1, -1):
        if isinstance(mensajes[indice], HumanMessage):
            return mensajes[indice + 1 :]
    return list(mensajes)


def tokens(mensajes: list[AnyMessage]) -> int:
    """Los tokens que el proveedor reportó en la conversación (C6 · PRD §8 Costo).

    Sale de `usage_metadata`, que LangChain rellena por respuesta. Un proveedor
    que no lo reporte deja el total en 0, igual que el adaptador propio.
    """
    total = 0
    for mensaje in mensajes:
        if isinstance(mensaje, AIMessage) and mensaje.usage_metadata is not None:
            total += mensaje.usage_metadata["total_tokens"]
    return total


def llamadas(mensajes: list[AnyMessage]) -> list[LlamadaRegistrada]:
    """El historial visible: nombre, argumentos y resultado resumido de cada llamada.

    Los argumentos salen del `AIMessage` que pidió la herramienta y el resultado
    del `ToolMessage` que la respondió; los une el `tool_call_id`.
    """
    argumentos: dict[str, dict[str, Any]] = {}
    for mensaje in mensajes:
        if isinstance(mensaje, AIMessage):
            for llamada in mensaje.tool_calls:
                argumentos[llamada["id"] or ""] = dict(llamada["args"])

    registradas: list[LlamadaRegistrada] = []
    for mensaje in mensajes:
        if not isinstance(mensaje, ToolMessage):
            continue
        resultado = mensaje.text
        registradas.append(
            LlamadaRegistrada(
                nombre=mensaje.name or "",
                argumentos=argumentos.get(str(mensaje.tool_call_id), {}),
                ok=_cuerpo(resultado).get("ok") is True,
                resumen=resultado
                if len(resultado) <= LARGO_RESUMEN
                else resultado[:LARGO_RESUMEN] + "…",
            )
        )
    return registradas


def pendiente(mensajes: list[AnyMessage]) -> bool:
    """¿Queda algo por confirmar al final del último turno? (CA3)

    Sube a True cuando `oc_validar` devuelve confirmaciones o cuando `oc_crear`
    responde que falta la confirmación. Baja a False cuando `oc_crear` tiene
    éxito: la OC ya existe y no hay nada que preguntar.
    """
    espera = False
    for mensaje in del_turno(mensajes):
        if isinstance(mensaje, ToolMessage):
            # Se lee el resultado completo y no el resumen de `llamadas`: las
            # confirmaciones viven en `data` y el recorte a 600 caracteres rompería
            # el JSON justo donde está la señal.
            espera = _pendiente_tras(mensaje.name or "", mensaje.text, espera)
    return espera


def _pendiente_tras(nombre: str, resultado: str, espera: bool) -> bool:
    """El estado de CA3 después de una llamada."""
    cuerpo = _cuerpo(resultado)
    exitosa = cuerpo.get("ok") is True

    if nombre == NOMBRE_CREAR and exitosa:
        return False

    if exitosa:
        datos = cuerpo.get("data")
        if isinstance(datos, dict) and datos.get("confirmaciones"):
            return True
        return espera

    return espera or MARCA_CONFIRMACION in str(cuerpo.get("error", "")).lower()


def _cuerpo(resultado: str) -> dict[str, Any]:
    """El JSON de una respuesta de herramienta. {} si no se pudo leer."""
    try:
        valor = json.loads(resultado)
    except ValueError:
        return {}
    return valor if isinstance(valor, dict) else {}
