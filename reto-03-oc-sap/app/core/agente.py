"""El ciclo del agente y las sesiones en memoria (PRD §6.3 · T4.3 · T4.7).

Este módulo es donde se cumplen las cinco reglas del ciclo. Cada una tiene una
línea que se puede señalar con el dedo:

| Regla | Dónde |
|---|---|
| CA1 · tope de iteraciones | `for _ in range(self.config.max_iteraciones)` en `responder` |
| CA2 · el modelo no afirma valores | `_ejecutar`: todo valor sale de una herramienta; el prompt lo prohíbe |
| CA3 · confirmación humana | `_es_confirmacion` + el rechazo de `confirmado=true` en `_ejecutar` |
| CA4 · bitácora | `sesion.llamadas`, que la API devuelve; `out/log.jsonl` lo escriben las herramientas |
| CA5 · un error no mata la sesión | el `except ErrorProveedor` del ciclo |
| C6 · tope de tokens por sesión | `sesion.tokens_usados >= self.config.max_tokens_sesion` |

**No importa `openai`.** Habla con el modelo por la interfaz de
`app/llm/adapter.py`; quién la implementa se decide en `app/api.py`. Cambiar de
proveedor no toca este archivo, que es lo que el PRD §6.1 evalúa.

Tampoco hay reglas de negocio aquí: los diez controles son funciones puras en
`app/controles/` y el agente nunca los consulta por su cuenta. Si el agente
afirma un monto, existe un `tool_call` previo que lo produjo.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Config
from app.core.io import leer_texto
from app.core.modelos import error
from app.llm.adapter import AdaptadorLlm, ErrorProveedor, LlamadaHerramienta, Mensaje
from app.tools.oc import HERRAMIENTAS, Contexto, Herramienta

# ── Constantes del comportamiento ──────────────────────────────────────────

ARCHIVO_PROMPT = ("agent", "prompt.md")
ARCHIVO_CONOCIMIENTO = ("app", "knowledge", "ordenes-compra.md")

TITULO_CONOCIMIENTO = "# Conocimiento del proceso de órdenes de compra"

# La herramienta que crea la OC: es la única sobre la que actúa la puerta de CA3.
NOMBRE_CREAR = "oc_crear"

# Marca textual con la que `oc_crear` reporta que falta la confirmación del
# usuario (ver `_crear` en app/tools/oc.py: "requiere confirmación del usuario
# antes de crear la OC de ..."). El ciclo la detecta para marcar el turno como
# pendiente **sin** interpretar la respuesta del modelo: la señal viene de la
# herramienta, no del texto que el modelo redacte.
MARCA_CONFIRMACION = "requiere confirmación"

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

RESPUESTA_SIN_TEXTO = (
    "No tengo nada más que agregar. Dime qué solicitud quieres procesar."
)

AVISO_PROVEEDOR = "No pude consultar al modelo en este momento."

# Largo del resultado que viaja al historial visible del chat. El PRD §6.1 pide
# "resultado resumido": el payload completo queda en out/<caso>/trazabilidad.json.
LARGO_RESUMEN = 600


# ── Piezas de la sesión ────────────────────────────────────────────────────


@dataclass(frozen=True)
class LlamadaRegistrada:
    """Una llamada a herramienta tal como la ve el usuario (CA4 · PRD §6.1)."""

    nombre: str
    argumentos: dict[str, Any]
    ok: bool
    resumen: str


@dataclass
class Sesion:
    """Estado de una conversación. En memoria: el PRD §6.1 no pide persistencia.

    `espera_confirmacion` es el corazón de CA3: solo si el turno anterior terminó
    preguntando, el siguiente mensaje del usuario puede confirmar algo.
    """

    id: str
    mensajes: list[Mensaje] = field(default_factory=list)
    llamadas: list[LlamadaRegistrada] = field(default_factory=list)
    tokens_usados: int = 0
    espera_confirmacion: bool = False


@dataclass(frozen=True)
class ResultadoTurno:
    """Lo que la API devuelve de un turno: `reply`, `toolCalls[]`, `needsConfirmation`."""

    reply: str
    llamadas: list[LlamadaRegistrada]
    needs_confirmation: bool


# ── Comportamiento y conocimiento (C4) ─────────────────────────────────────


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


# ── El agente ──────────────────────────────────────────────────────────────


class Agente:
    """El ciclo modelo → herramientas → modelo, y las sesiones en memoria."""

    def __init__(self, config: Config, adaptador: AdaptadorLlm, comportamiento: str) -> None:
        self.config = config
        self.adaptador = adaptador
        # El mensaje de sistema se arma una vez y no entra al historial de la
        # sesión: no es parte de la conversación que el usuario ve.
        self._sistema = Mensaje(rol="system", contenido=comportamiento)
        self._sesiones: dict[str, Sesion] = {}

    def sesion(self, session_id: str) -> Sesion:
        """La sesión con ese id, creándola la primera vez."""
        if session_id not in self._sesiones:
            self._sesiones[session_id] = Sesion(id=session_id)
        return self._sesiones[session_id]

    def buscar_sesion(self, session_id: str) -> Sesion | None:
        """La sesión, o None si nunca existió. La usa `GET /api/sessions/{id}`."""
        return self._sesiones.get(session_id)

    async def responder(self, session_id: str, texto_usuario: str) -> ResultadoTurno:
        """Procesa un mensaje del usuario y devuelve el turno completo.

        El turno termina por una de cuatro razones, y todas dejan la sesión viva:
        el modelo respondió sin pedir herramientas, falló el proveedor (CA5), se
        agotó el tope de tokens (C6) o se agotó el de iteraciones (CA1).
        """
        sesion = self.sesion(session_id)

        # CA3 · C5: la confirmación se decide **antes** de hablar con el modelo y
        # solo con dos datos: que el turno anterior terminó preguntando y que el
        # usuario dijo que sí. El modelo no participa en esta decisión.
        confirmado = sesion.espera_confirmacion and _es_confirmacion(texto_usuario)

        sesion.mensajes.append(Mensaje(rol="user", contenido=texto_usuario))
        llamadas: list[LlamadaRegistrada] = []
        pendiente = False

        if self._sin_presupuesto(sesion):
            return self._terminar(sesion, self._aviso_de_tokens(), llamadas, pendiente)

        # CA1 · C6: el tope de iteraciones herramienta → modelo por turno. Esta
        # es la línea: el ciclo no puede dar más vueltas que `max_iteraciones`.
        for _ in range(self.config.max_iteraciones):
            try:
                respuesta = await self.adaptador.enviar(
                    [self._sistema, *sesion.mensajes], HERRAMIENTAS
                )
            except ErrorProveedor as fallo:
                # CA5: el error del proveedor se cuenta en lenguaje claro y la
                # sesión sigue viva: el historial queda intacto y el siguiente
                # mensaje del usuario continúa donde se quedó.
                return self._terminar(
                    sesion, f"{AVISO_PROVEEDOR} {fallo}", llamadas, pendiente
                )

            sesion.tokens_usados += respuesta.tokens_usados

            if not respuesta.tool_calls:
                return self._terminar(
                    sesion, respuesta.texto or RESPUESTA_SIN_TEXTO, llamadas, pendiente
                )

            sesion.mensajes.append(
                Mensaje(
                    rol="assistant",
                    contenido=respuesta.texto,
                    tool_calls=tuple(respuesta.tool_calls),
                )
            )

            for llamada in respuesta.tool_calls:
                resultado = await self._ejecutar(llamada, sesion, confirmado)
                registro = _registrar(llamada, resultado)
                llamadas.append(registro)
                sesion.llamadas.append(registro)  # CA4: historial visible
                sesion.mensajes.append(
                    Mensaje(
                        rol="tool",
                        contenido=resultado,
                        tool_call_id=llamada.id,
                        nombre_herramienta=llamada.nombre,
                    )
                )
                pendiente = _pendiente_tras(llamada.nombre, resultado, pendiente)

            if self._sin_presupuesto(sesion):
                return self._terminar(sesion, self._aviso_de_tokens(), llamadas, pendiente)

        # CA1: se agotaron las iteraciones. El agente responde con lo que tiene y
        # lo que falta, en lugar de seguir gastando o de cortar en silencio.
        aviso = _aviso_de_tope(self.config.max_iteraciones, llamadas)
        return self._terminar(sesion, aviso, llamadas, pendiente)

    # ── Ejecución de una llamada ───────────────────────────────────────────

    async def _ejecutar(
        self, llamada: LlamadaHerramienta, sesion: Sesion, confirmado: bool
    ) -> str:
        """Ejecuta la herramienta que pidió el modelo. Devuelve siempre un string JSON."""
        herramienta = _buscar(llamada.nombre)
        if herramienta is None:
            disponibles = ", ".join(h.nombre for h in HERRAMIENTAS)
            return error(
                f"No existe la herramienta {llamada.nombre!r}. Disponibles: {disponibles}."
            )

        # CA3 · C5: el modelo no puede confirmarse a sí mismo. `confirmado=true`
        # solo vale si el **último mensaje del usuario** confirmó. Sin esta
        # puerta, un modelo complaciente crearía la OC en el mismo turno en el
        # que prometió preguntar, y la regla que el PRD califica quedaría en una
        # recomendación del prompt.
        if llamada.nombre == NOMBRE_CREAR and llamada.argumentos.get("confirmado") and not confirmado:
            return error(
                "No puedes marcar confirmado=true: el usuario todavía no ha confirmado. "
                "Muéstrale los datos concretos de cada excepción, hazle la pregunta y "
                "termina el turno. Crea la OC cuando él responda que confirma."
            )

        contexto = Contexto(raiz=self.config.raiz, session_id=sesion.id)
        try:
            return await herramienta.execute(llamada.argumentos, contexto)
        # Las herramientas no lanzan (C3), pero el ciclo no depende de que eso
        # sea cierto: CA5 exige que la sesión sobreviva a cualquier error.
        except Exception as excepcion:  # noqa: BLE001
            return error(
                f"La herramienta {llamada.nombre} falló: {type(excepcion).__name__}. "
                "Explícaselo al usuario y no inventes el resultado."
            )

    # ── Cierre del turno ───────────────────────────────────────────────────

    def _terminar(
        self,
        sesion: Sesion,
        reply: str,
        llamadas: list[LlamadaRegistrada],
        pendiente: bool,
    ) -> ResultadoTurno:
        """Deja la respuesta final en el historial y fija el estado de CA3."""
        sesion.mensajes.append(Mensaje(rol="assistant", contenido=reply))
        sesion.espera_confirmacion = pendiente
        return ResultadoTurno(reply=reply, llamadas=llamadas, needs_confirmation=pendiente)

    def _sin_presupuesto(self, sesion: Sesion) -> bool:
        """C6 · PRD §8 Costo: tope de tokens por sesión, además del de iteraciones."""
        return sesion.tokens_usados >= self.config.max_tokens_sesion

    def _aviso_de_tokens(self) -> str:
        return (
            f"Esta conversación alcanzó el tope de {self.config.max_tokens_sesion} tokens "
            "configurado para controlar el costo. No puedo seguir consultando al modelo en "
            "esta sesión; las llamadas a herramientas que ya hice quedan en el historial. "
            "Abre una sesión nueva para continuar."
        )


# ── Funciones puras de apoyo ───────────────────────────────────────────────


def _buscar(nombre: str) -> Herramienta | None:
    """La herramienta del registro con ese nombre, o None."""
    for herramienta in HERRAMIENTAS:
        if herramienta.nombre == nombre:
            return herramienta
    return None


def _cuerpo(resultado: str) -> dict[str, Any]:
    """El JSON de una respuesta de herramienta. {} si no se pudo leer."""
    try:
        valor = json.loads(resultado)
    except ValueError:
        return {}
    return valor if isinstance(valor, dict) else {}


def _pendiente_tras(nombre: str, resultado: str, pendiente: bool) -> bool:
    """¿Queda algo por confirmar después de esta llamada? (CA3)

    Sube a True cuando `oc_validar` devuelve confirmaciones o cuando `oc_crear`
    responde que falta la confirmación. Baja a False cuando `oc_crear` tiene
    éxito: la OC ya existe y no hay nada que preguntar.
    """
    cuerpo = _cuerpo(resultado)
    exitosa = cuerpo.get("ok") is True

    if nombre == NOMBRE_CREAR and exitosa:
        return False

    if exitosa:
        datos = cuerpo.get("data")
        if isinstance(datos, dict) and datos.get("confirmaciones"):
            return True
        return pendiente

    return pendiente or MARCA_CONFIRMACION in str(cuerpo.get("error", "")).lower()


def _registrar(llamada: LlamadaHerramienta, resultado: str) -> LlamadaRegistrada:
    """Arma la entrada del historial visible: nombre, argumentos y resultado resumido."""
    cuerpo = _cuerpo(resultado)
    resumen = resultado if len(resultado) <= LARGO_RESUMEN else resultado[:LARGO_RESUMEN] + "…"
    return LlamadaRegistrada(
        nombre=llamada.nombre,
        argumentos=llamada.argumentos,
        ok=cuerpo.get("ok") is True,
        resumen=resumen,
    )


def _es_confirmacion(texto: str) -> bool:
    """¿El usuario confirmó en este mensaje? (CA3)

    Las negaciones se revisan primero porque "no confirmo" contiene "confirmo".
    Ante la duda, **no** es confirmación: el costo de no crear una OC es pedirla
    otra vez; el de crearla sin permiso es una orden de compra indebida.
    """
    limpio = texto.strip().lower()
    if any(negacion in limpio for negacion in NEGACIONES):
        return False
    return any(palabra in limpio for palabra in PALABRAS_CONFIRMACION)


def _aviso_de_tope(tope: int, llamadas: list[LlamadaRegistrada]) -> str:
    """CA1: lo que el agente tiene y lo que falta al agotar las iteraciones."""
    hechas = [registro.nombre for registro in llamadas if registro.ok]
    faltan = [
        herramienta.nombre for herramienta in HERRAMIENTAS if herramienta.nombre not in hechas
    ]
    return (
        f"Alcancé el tope de {tope} iteraciones de este turno y lo detuve para no seguir "
        "gastando. Lo que sí quedó hecho: "
        f"{', '.join(dict.fromkeys(hechas)) or 'ninguna llamada exitosa'}. "
        f"Lo que falta: {', '.join(faltan) or 'nada'}. "
        "Dime si quieres que continúe y sigo desde este punto."
    )
