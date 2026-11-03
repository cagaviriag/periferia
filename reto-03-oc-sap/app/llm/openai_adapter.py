"""La única implementación que conoce el SDK `openai` (PRD §6.1 · T4.2).

Todo lo específico del proveedor vive aquí y en ningún otro archivo:

- la clave, que se pide explícitamente con `get_secret_value()` (C7);
- la forma de los mensajes (`role`/`content`/`tool_calls`);
- la forma de las herramientas (`{"type": "function", "function": {...}}`);
- el mapeo de los errores del SDK a `ErrorProveedor` (CA5).

Cambiar de proveedor es escribir un hermano de este archivo y cambiar una línea
en `app/api.py`. El ciclo del agente no se toca.

Los `cast` a los tipos `...Param` del SDK son la frontera de serialización, la
misma de `app/core/io.py` (ADR-009): los diccionarios se arman a mano porque los
`TypedDict` del SDK cambian de forma entre versiones, y el `cast` documenta que
la responsabilidad del formato es de este módulo.
"""

import json
from typing import Any, cast

from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

from app.core.config import Config
from app.llm.adapter import (
    ErrorProveedor,
    LlamadaHerramienta,
    Mensaje,
    Respuesta,
    esquema_de_herramienta,
)
from app.tools.oc import Herramienta

# Temperatura 0: el reto se califica por determinismo (C9). El azar del modelo no
# cambia ningún valor —los producen las herramientas— pero sí cambiaría el orden
# de las llamadas, y con él la bitácora que se audita.
TEMPERATURA = 0.0

PROVEEDOR = "openai"


class AdaptadorOpenAI:
    """Implementa `AdaptadorLlm` sobre `chat.completions`.

    El timeout se configura en el cliente (`LLM_TIMEOUT_SEGUNDOS`): sin él, una
    petición colgada bloquearía el turno hasta que el usuario se rinda, y CA5
    exige un mensaje claro.
    """

    def __init__(self, config: Config) -> None:
        self._modelo = config.openai_model
        # C7: este es el único punto del sistema que desenvuelve la credencial.
        self._cliente = AsyncOpenAI(
            api_key=config.openai_api_key.get_secret_value(),
            timeout=float(config.llm_timeout_segundos),
            max_retries=0,  # el reintento lo decide el usuario: ver CA5 en agente.py
        )

    async def enviar(
        self, mensajes: list[Mensaje], herramientas: list[Herramienta]
    ) -> Respuesta:
        """Manda la conversación al modelo y traduce la respuesta al vocabulario propio."""
        try:
            completado = await self._cliente.chat.completions.create(
                model=self._modelo,
                messages=[_mensaje_openai(mensaje) for mensaje in mensajes],
                tools=[_herramienta_openai(herramienta) for herramienta in herramientas],
                temperature=TEMPERATURA,
            )
        except APITimeoutError as excepcion:
            raise ErrorProveedor(
                "El modelo no respondió dentro del tiempo de espera. "
                "Vuelve a enviar tu mensaje; la conversación sigue abierta."
            ) from excepcion
        except APIConnectionError as excepcion:
            raise ErrorProveedor(
                "No se pudo conectar con el proveedor del modelo. "
                "Revisa la conexión y vuelve a intentarlo."
            ) from excepcion
        except APIError as excepcion:
            raise ErrorProveedor(
                f"El proveedor del modelo rechazó la petición: {_detalle(excepcion)}"
            ) from excepcion
        # El `except Exception` final es deliberado: cualquier fallo del SDK tiene
        # que llegar al ciclo como `ErrorProveedor`, porque CA5 exige que la
        # sesión sobreviva a *cualquier* error del proveedor, no solo a los que
        # el SDK clasificó.
        except Exception as excepcion:  # noqa: BLE001
            raise ErrorProveedor(
                f"Falló la llamada al modelo: {type(excepcion).__name__}."
            ) from excepcion

        return _respuesta_propia(completado)


def _detalle(excepcion: APIError) -> str:
    """Primera línea del error del SDK, recortada.

    Se usa `excepcion.message` y no `str(excepcion)` para no arrastrar el volcado
    del cuerpo HTTP al chat. La credencial nunca viaja en estos mensajes, y aun
    así se recorta: lo que el usuario necesita es la causa, no la traza.
    """
    mensaje = " ".join(str(excepcion.message).split()) or "sin detalle"
    return mensaje[:200]


def _herramienta_openai(herramienta: Herramienta) -> ChatCompletionToolParam:
    """Envuelve el esquema neutral en el formato de tool-calling de OpenAI."""
    crudo: dict[str, Any] = {"type": "function", "function": esquema_de_herramienta(herramienta)}
    return cast(ChatCompletionToolParam, crudo)


def _mensaje_openai(mensaje: Mensaje) -> ChatCompletionMessageParam:
    """`Mensaje` propio → mensaje del SDK. Un caso por rol, sin anidar."""
    if mensaje.rol == "tool":
        crudo: dict[str, Any] = {
            "role": "tool",
            # El id enlaza el resultado con la llamada que lo pidió. Si faltara,
            # el proveedor rechaza la conversación completa.
            "tool_call_id": mensaje.tool_call_id or "",
            "content": mensaje.contenido,
        }
        return cast(ChatCompletionMessageParam, crudo)

    if mensaje.rol == "assistant" and mensaje.tool_calls:
        crudo = {
            "role": "assistant",
            # `content` vacío va como None: el modelo que solo pide herramientas
            # no escribió texto, y un string vacío no es lo mismo que ausencia.
            "content": mensaje.contenido or None,
            "tool_calls": [
                {
                    "id": llamada.id,
                    "type": "function",
                    "function": {
                        "name": llamada.nombre,
                        "arguments": json.dumps(llamada.argumentos, ensure_ascii=False),
                    },
                }
                for llamada in mensaje.tool_calls
            ],
        }
        return cast(ChatCompletionMessageParam, crudo)

    crudo = {"role": mensaje.rol, "content": mensaje.contenido}
    return cast(ChatCompletionMessageParam, crudo)


def _respuesta_propia(completado: ChatCompletion) -> Respuesta:
    """Respuesta del SDK → `Respuesta` propia.

    Un `arguments` que no sea JSON válido **no lanza**: se entrega como
    `{}` y la herramienta devolverá "Argumentos inválidos" al modelo, que es el
    camino que el PRD §6.2 prescribe para un argumento mal formado.
    """
    if not completado.choices:
        raise ErrorProveedor("El proveedor del modelo devolvió una respuesta vacía.")

    mensaje = completado.choices[0].message
    llamadas: list[LlamadaHerramienta] = []
    for llamada in mensaje.tool_calls or []:
        # Solo existen herramientas de tipo `function`: las `custom` nunca se
        # declaran, así que una que llegue se ignora en lugar de romper el turno.
        if llamada.type != "function":
            continue
        llamadas.append(
            LlamadaHerramienta(
                id=llamada.id,
                nombre=llamada.function.name,
                argumentos=_argumentos(llamada.function.arguments),
            )
        )

    tokens = completado.usage.total_tokens if completado.usage is not None else 0
    return Respuesta(texto=mensaje.content or "", tool_calls=llamadas, tokens_usados=tokens)


def _argumentos(crudo: str) -> dict[str, Any]:
    """Parsea los argumentos del modelo. Nunca lanza: devuelve {} si no son un objeto."""
    try:
        valor = json.loads(crudo or "{}")
    except ValueError:
        return {}
    return valor if isinstance(valor, dict) else {}
