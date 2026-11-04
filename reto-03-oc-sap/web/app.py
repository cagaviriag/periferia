"""Front de chat del agente de Órdenes de Compra — PRD §6.1, HU-3 y §11.

Los cinco comportamientos que el PRD §6.1 califica uno por uno:

1. Historial de la conversación      → `_dibujar_historial`
2. Campo de entrada                  → `st.chat_input` al final del script
3. Indicador de "pensando"           → `st.spinner` en `_procesar_turno`
4. Cada llamada a herramienta visible (nombre, argumentos, resultado resumido)
                                     → `_dibujar_tool_call`, un `st.expander` por llamada
5. Confirmación resaltada            → `_dibujar_confirmacion`

Arquitectura (decidida fuera de este archivo): el front llama al ciclo del
agente **en proceso**, y conmuta a HTTP solo si existe la variable `API_URL`.
Razón: Streamlit Community Cloud despliega un único servicio desde el repo, así
que dos procesos serían dos despliegues y el doble de puntos de falla.

Toda la dependencia del ciclo del agente está aislada en `_responder`: si el
contrato real de `app/core/agente.py` difiere en un detalle, se ajusta ahí y en
ningún otro sitio.
"""

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import streamlit as st

# web/app.py → parents[0]=web, parents[1]=raíz del proyecto.
RAIZ = Path(__file__).resolve().parents[1]

# La raíz del proyecto tiene que ir **primera** en el path, y esta línea es
# load-bearing por dos razones:
#
# 1. Streamlit arranca el script con su propia carpeta (`web/`) al frente del
#    `sys.path`, y la raíz no está: `import app.core.agente` no la encontraría.
# 2. Peor: este archivo se llama `app.py` y el backend es el paquete `app/`.
#    Con `web/` delante, `import app` importaría ESTE archivo como el paquete
#    `app`, volvería a ejecutar la interfaz entera dentro de un import y
#    reventaría con claves de widget duplicadas.
#
# Se comprueba la posición 0 y no la pertenencia porque Streamlit reejecuta el
# script en cada interacción: insertar sin mirar haría crecer el path sin fin.
if sys.path[:1] != [str(RAIZ)]:
    sys.path.insert(0, str(RAIZ))

# Variables que el backend lee del entorno (app/core/config.py). En Streamlit
# Community Cloud se pegan en Settings → Secrets; aquí se copian al entorno del
# proceso para que `cargar_config` las vea. C7: la clave vive solo en el
# entorno, nunca se imprime ni se devuelve a la vista.
VARIABLES_DEL_BACKEND = [
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "MAX_ITERACIONES",
    "MAX_TOKENS_SESION",
    "LLM_TIMEOUT_SEGUNDOS",
    "API_URL",
]

# Atajos de la defensa: un botón por caso, con el texto exacto que conviene
# escribir. El de sol-004 es literalmente el prompt del PRD §11.
PROMPT_PRD_11 = (
    'Procesa la solicitud "sol-004". Muéstrame la OC como quedaría en SAP, qué '
    "validaciones pasó y cuáles no, y no la crees hasta que yo lo confirme."
)

ATAJOS: list[tuple[str, str, str]] = [
    (
        "sol-001",
        "sol-001 · camino feliz",
        'Procesa la solicitud "sol-001". Muéstrame la OC como quedaría en SAP y qué '
        "validaciones pasó. Si todo está en orden, créala.",
    ),
    (
        "sol-002",
        "sol-002 · proveedor inexistente",
        'Procesa la solicitud "sol-002". Dime si es apta y qué tendría que pedirle al '
        "solicitante si no lo es.",
    ),
    (
        "sol-003",
        "sol-003 · aprobador sin autoridad",
        'Procesa la solicitud "sol-003". Dime si es apta y por qué, con detalle del control '
        "que falló.",
    ),
    ("sol-004", "sol-004 · cotización ≠ solicitud (§11)", PROMPT_PRD_11),
    (
        "sol-005",
        "sol-005 · OC retroactiva",
        'Procesa la solicitud "sol-005". Muéstrame la OC como quedaría en SAP y no la crees '
        "hasta que yo lo confirme.",
    ),
    (
        "sol-006",
        "sol-006 · datos incompletos",
        'Procesa la solicitud "sol-006". Dime qué datos tuviste que derivar de los maestros '
        "y no crees la OC hasta que yo lo confirme.",
    ),
]

TEXTO_CONFIRMACION = "confirmo"
TEXTO_RECHAZO = "No confirmo. No crees la orden de compra."


# ── Frontera con el ciclo del agente ───────────────────────────────────────
#
# Todo lo que este front sabe del backend está entre esta línea y la siguiente
# marca de sección. Es deliberado: el ciclo del agente (`app/core/agente.py`) se
# construye en paralelo y el contrato puede moverse en un detalle.


@dataclass(frozen=True)
class Turno:
    """La respuesta de un turno, en la forma que consume la vista.

    Es el mismo contrato que `app.core.agente.Turno` y que la respuesta de
    `POST /api/chat` (`reply`, `toolCalls`, `needsConfirmation`), normalizado a
    un solo tipo para que la vista no sepa por dónde llegó.
    """

    reply: str
    tool_calls: list[Any] = field(default_factory=list)
    needs_confirmation: bool = False


def _url_de_la_api() -> str:
    """La API HTTP si está configurada, o cadena vacía si corremos en proceso."""
    return os.environ.get("API_URL", "").strip()


def _bucle_de_eventos() -> asyncio.AbstractEventLoop:
    """Un único bucle de eventos por sesión de Streamlit.

    `asyncio.run` crearía y cerraría un bucle por turno. Si el adaptador LLM
    mantiene un cliente HTTP asíncrono vivo entre turnos, ese cliente queda
    atado al primer bucle y el segundo turno fallaría. Con un bucle persistente
    el problema no existe.
    """
    if "bucle" not in st.session_state:
        st.session_state.bucle = asyncio.new_event_loop()
    bucle: asyncio.AbstractEventLoop = st.session_state.bucle
    return bucle


def _agente_en_proceso() -> Any:
    """Construye, una vez por sesión, el `Agente` de `app/core/agente.py`.

    Es el mismo armado de `app/api.py`: configuración → modelo del proveedor
    → comportamiento desde `agent/prompt.md`. Si falta la clave, `cargar_config`
    lanza con un mensaje accionable y `_responder` lo convierte en texto del
    chat (CA5). La clave no se toca aquí: viaja dentro de la configuración (C7).
    """
    if "agente" in st.session_state:
        return st.session_state.agente

    from app.core.agente import Agente, cargar_comportamiento
    from app.core.config import cargar_config
    from app.llm.modelo import crear_modelo

    config = cargar_config()
    agente = Agente(
        config=config,
        modelo=crear_modelo(config),
        comportamiento=cargar_comportamiento(config.raiz),
    )
    st.session_state.agente = agente
    return agente


def _responder(mensaje: str) -> Turno:
    """Manda un mensaje de usuario al ciclo del agente y devuelve su turno.

    ÚNICO punto de acoplamiento del front con el backend. Nunca lanza: un error
    del proveedor o de una herramienta vuelve como texto del turno y la sesión
    sigue viva (CA5).
    """
    url = _url_de_la_api()
    if url:
        return _responder_por_http(mensaje, url)
    return _responder_en_proceso(mensaje)


def _responder_en_proceso(mensaje: str) -> Turno:
    """Llama a `Agente.responder(session_id, mensaje)` en este mismo proceso."""
    try:
        agente = _agente_en_proceso()
    except Exception as excepcion:  # noqa: BLE001
        # Falta la clave, el prompt o el adaptador: se dice y la sesión sigue.
        return Turno(reply=f"⚠️ No pude inicializar el agente: {excepcion}")

    try:
        crudo = _bucle_de_eventos().run_until_complete(
            agente.responder(st.session_state.session_id, mensaje)
        )
    except Exception as excepcion:  # noqa: BLE001
        # CA5 · el error se muestra en lenguaje claro y la sesión no muere.
        return Turno(reply=f"⚠️ No pude completar el turno: {excepcion}")

    return _normalizar_turno(crudo)


def _responder_por_http(mensaje: str, url: str) -> Turno:
    """Llama a `POST /api/chat` del backend FastAPI (PRD §6.4)."""
    import httpx

    try:
        respuesta = httpx.post(
            f"{url.rstrip('/')}/api/chat",
            json={"sessionId": st.session_state.session_id, "message": mensaje},
            timeout=180.0,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
    except Exception as excepcion:  # noqa: BLE001
        return Turno(reply=f"⚠️ No pude hablar con la API ({url}): {excepcion}")

    return _normalizar_turno(cuerpo)


def _normalizar_turno(crudo: Any) -> Turno:
    """Convierte el turno del backend —objeto o dict JSON— en un `Turno`."""
    reply = _campo(crudo, "reply", "respuesta")
    llamadas = _campo(crudo, "tool_calls", "toolCalls", "llamadas")
    confirmacion = _campo(crudo, "needs_confirmation", "needsConfirmation", "confirmacion")
    return Turno(
        reply=str(reply) if reply is not None else str(crudo),
        tool_calls=list(llamadas) if isinstance(llamadas, list) else [],
        needs_confirmation=bool(confirmacion),
    )


def _refrescar_resultados() -> None:
    """Relee los resultados **completos** de las herramientas de la sesión.

    El historial visible del chat trae el resultado *resumido* que pide el PRD
    §6.1, y el agente lo recorta a 600 caracteres. El panel de la derecha
    necesita el JSON íntegro —el payload de la OC mide más que eso—, y la fuente
    autorizada es el historial de la sesión: cada llamada deja un mensaje `tool`
    con el resultado exacto que leyó el modelo. Así el panel muestra lo mismo
    que vio el agente, sin recalcular nada (C2).
    """
    try:
        st.session_state.resultados = _leer_resultados()
    except Exception:  # noqa: BLE001
        # El panel se queda con lo que ya tenía: el turno del chat ya se mostró
        # y un fallo leyendo el historial no puede tumbar la pantalla (CA5).
        pass


def _leer_resultados() -> list[dict[str, Any]]:
    """`[{"nombre": "oc_validar", "resultado": "<json completo>"}, …]`."""
    url = _url_de_la_api()
    if url:
        import httpx

        respuesta = httpx.get(
            f"{url.rstrip('/')}/api/sessions/{st.session_state.session_id}", timeout=30.0
        )
        respuesta.raise_for_status()
        mensajes = respuesta.json().get("messages") or []
        return [
            {"nombre": str(mensaje.get("toolName") or ""), "resultado": mensaje.get("content") or ""}
            for mensaje in mensajes
            if isinstance(mensaje, dict) and mensaje.get("role") == "tool"
        ]

    # El historial de la sesión es ahora la lista de mensajes de LangChain que
    # guarda el checkpointer del agente; `rol` la traduce al vocabulario del PRD.
    from app.core.historial import rol

    mensajes = _agente_en_proceso().historial(st.session_state.session_id)
    return [
        {"nombre": str(getattr(mensaje, "name", "") or ""), "resultado": mensaje.text}
        for mensaje in mensajes
        if rol(mensaje) == "tool"
    ]


# ── Lectura de las llamadas a herramienta ──────────────────────────────────


def _campo(origen: Any, *nombres: str) -> Any:
    """Primer campo presente en `origen`, sea un dict (API HTTP) o un objeto.

    Los dos caminos del front entregan la misma información con nombres
    distintos (`tool_calls` en Python, `toolCalls` en JSON). Esta función es el
    traductor, y vive junto al resto de la frontera por la misma razón.
    """
    for nombre in nombres:
        if isinstance(origen, dict):
            if nombre in origen:
                return origen[nombre]
        elif hasattr(origen, nombre):
            return getattr(origen, nombre)
    return None


def _nombre_de(llamada: Any) -> str:
    valor = _campo(llamada, "nombre", "name", "herramienta", "tool")
    return str(valor) if valor else "herramienta"


def _argumentos_de(llamada: Any) -> Any:
    return _campo(llamada, "argumentos", "arguments", "args", "input")


def _resultado_de(llamada: Any) -> Any:
    return _campo(llamada, "resultado", "result", "respuesta", "output", "content")


def _cuerpo(resultado: Any) -> dict[str, Any] | None:
    """Deserializa el string JSON que devuelve toda herramienta (C3).

    Las herramientas entregan `{"ok": true, "data": …}` o
    `{"ok": false, "error": …}` ya serializado. Devuelve `None` cuando el texto
    no es JSON legible —caso típico: el resumen recortado del historial—, para
    que quien llame pueda distinguirlo de un error real de la herramienta.
    """
    if isinstance(resultado, dict):
        return resultado
    if not isinstance(resultado, str):
        return None
    try:
        cuerpo = json.loads(resultado)
    except ValueError:
        return None
    return cuerpo if isinstance(cuerpo, dict) else None


def _datos_de(resultado: Any) -> dict[str, Any]:
    """El `data` de un resultado exitoso, o `{}` si falló o es ilegible."""
    cuerpo = _cuerpo(resultado)
    if cuerpo is None or cuerpo.get("ok") is not True:
        return {}
    datos = cuerpo.get("data")
    return datos if isinstance(datos, dict) else {}


def _escalares(datos: dict[str, Any]) -> str:
    """Los campos simples de `data`, en una línea legible."""
    campos = [
        f"`{clave}` = {valor}"
        for clave, valor in datos.items()
        if isinstance(valor, bool | int | str) and len(str(valor)) <= 70
    ]
    return " · ".join(campos) if campos else "ok"


def _ultimos_datos(nombre: str) -> dict[str, Any]:
    """El `data` de la última llamada exitosa a `nombre`, o `{}`.

    Lee los resultados completos de la sesión (`_refrescar_resultados`), no el
    resumen del chat.
    """
    for registro in reversed(st.session_state.resultados):
        if registro.get("nombre") != nombre:
            continue
        datos = _datos_de(registro.get("resultado"))
        if datos:
            return datos
    return {}


# ── Formato ────────────────────────────────────────────────────────────────


def _pesos(valor: Any) -> str:
    """Monto con separador de miles colombiano: 11400000 → `11.400.000`."""
    if not isinstance(valor, int) or isinstance(valor, bool):
        return "—" if valor is None else str(valor)
    return f"{valor:,}".replace(",", ".")


def _porcentaje(valor: Any) -> str:
    if isinstance(valor, int | float) and not isinstance(valor, bool):
        return f"{valor:.2f} %"
    return "—"


def _como_json(valor: Any) -> str:
    """Argumentos de la llamada, legibles, recortados si son enormes."""
    try:
        texto = json.dumps(valor, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        texto = str(valor)
    return texto if len(texto) <= 2000 else texto[:2000] + "\n… (recortado)"


# ── Vista · chat ───────────────────────────────────────────────────────────


def _dibujar_tool_call(llamada: Any) -> None:
    """Comportamiento 4 del PRD §6.1: nombre, argumentos y resultado resumido.

    Un `st.expander` por llamada, cerrado: la conversación se lee sin ruido y
    el detalle está a un clic. CA4 exige que quede en el historial visible.
    """
    resultado = _resultado_de(llamada)
    cuerpo = _cuerpo(resultado)
    # `ok` viene como campo propio de la llamada; el JSON es el respaldo.
    exito = _campo(llamada, "ok")
    if exito is None:
        exito = cuerpo is not None and cuerpo.get("ok") is True

    with st.expander(f"{'✅' if exito else '❌'} 🔧 `{_nombre_de(llamada)}`"):
        st.caption("Argumentos")
        st.code(_como_json(_argumentos_de(llamada)), language="json")
        st.caption("Resultado resumido")
        if cuerpo is None:
            # El agente recorta el resumen a 600 caracteres, así que un payload
            # grande llega como JSON incompleto: se muestra tal cual y el detalle
            # íntegro está en el panel de inspección.
            st.code(str(resultado)[:600], language="json")
        elif cuerpo.get("ok") is True:
            datos = cuerpo.get("data")
            st.markdown(_escalares(datos) if isinstance(datos, dict) else "ok")
        else:
            st.markdown(str(cuerpo.get("error", "error sin detalle")))


def _dibujar_historial() -> None:
    """Comportamiento 1 del PRD §6.1: el historial de la conversación."""
    for mensaje in st.session_state.historial:
        with st.chat_message(mensaje["rol"]):
            st.markdown(mensaje["texto"])
            for llamada in mensaje["tool_calls"]:
                _dibujar_tool_call(llamada)


def _dibujar_confirmacion() -> None:
    """Comportamiento 5 del PRD §6.1 + CA3: el estado de confirmación, resaltado."""
    with st.container(border=True):
        st.warning("### ⚠️ El agente pide tu confirmación para continuar")
        _dibujar_montos_de_rc5()
        st.caption(
            "Confirmar envía un mensaje de usuario normal. Es el ciclo del agente el que "
            "decide si crea la OC (CA3)."
        )
        izquierda, derecha = st.columns(2)
        # ─────────────────────────────────────────────────────────────────
        # C5 · CA3 — REGLA QUE NO SE PUEDE VIOLAR:
        # este botón NO llama a `oc_crear` ni a ninguna otra herramienta.
        # Encola el texto "confirmo" como un mensaje de usuario cualquiera y
        # el turno siguiente del agente decide qué hacer con él. Si el botón
        # invocara la herramienta, nos saltaríamos el turno real de
        # confirmación, que es exactamente lo que el PRD califica en CA3, y el
        # criterio quedaría invalidado aunque la OC se creara igual.
        # ─────────────────────────────────────────────────────────────────
        if izquierda.button("✅ Confirmar", type="primary", use_container_width=True):
            _encolar(TEXTO_CONFIRMACION)
        if derecha.button("✋ No confirmar", use_container_width=True):
            _encolar(TEXTO_RECHAZO)


def _dibujar_montos_de_rc5() -> None:
    """Los dos montos de RC5 con su desviación (HU-3 · PRD §11).

    El PRD §11 exige que la confirmación muestre el valor de la solicitud y el
    de la cotización, no solo el aviso. Los dos salen de `oc_validar`: el front
    no calcula ninguno (C2).
    """
    for veredicto in _confirmaciones():
        if veredicto.get("codigo") != "RC5":
            continue
        datos = veredicto.get("datos") or {}
        solicitud, cotizacion, desviacion = st.columns(3)
        solicitud.metric("Valor de la solicitud", _pesos(datos.get("valor_solicitud")))
        cotizacion.metric("Valor de la cotización", _pesos(datos.get("valor_cotizacion")))
        desviacion.metric(
            "Desviación",
            _porcentaje(datos.get("desviacion_pct")),
            delta=f"umbral {_porcentaje(datos.get('umbral_pct'))}",
            delta_color="off",
        )


# ── Vista · panel de inspección ────────────────────────────────────────────


def _payload_actual() -> dict[str, Any]:
    """El payload de la OC que devolvió `oc_construir_payload`, o `{}`."""
    payload = _ultimos_datos("oc_construir_payload").get("payload")
    return payload if isinstance(payload, dict) else {}


def _validacion_actual() -> dict[str, Any]:
    """El resultado de `oc_validar`, o `{}` si todavía no se llamó."""
    return _ultimos_datos("oc_validar")


def _confirmaciones() -> list[dict[str, Any]]:
    confirmaciones = _validacion_actual().get("confirmaciones")
    return confirmaciones if isinstance(confirmaciones, list) else []


def _dibujar_oc_en_sap() -> None:
    """HU-3 · §11: la OC como quedaría en SAP, en tabla legible, no JSON crudo."""
    payload = _payload_actual()
    if not payload:
        st.info("Cuando el agente construya el payload, la OC aparecerá aquí en tabla.")
        return

    referencia = payload.get("referencia") or {}
    proveedor = payload.get("proveedor") or {}
    aprobador = payload.get("aprobador") or {}
    sha = str(aprobador.get("evidencia_sha256") or "")

    st.caption("Cabecera")
    st.dataframe(
        [
            {"Campo": "Solicitud", "Valor": referencia.get("solicitud_id", "—")},
            {"Campo": "Correo", "Valor": referencia.get("correo_id", "—")},
            {"Campo": "Cotización", "Valor": referencia.get("cotizacion_ref") or "—"},
            {"Campo": "Sociedad", "Valor": payload.get("sociedad", "—")},
            {"Campo": "Org. de compras", "Valor": payload.get("organizacion_compras", "—")},
            {
                "Campo": "Proveedor",
                "Valor": f"{proveedor.get('codigo_sap', '—')} · {proveedor.get('nombre', '—')}",
            },
            {"Campo": "NIT", "Valor": proveedor.get("nit", "—")},
            {"Campo": "Moneda", "Valor": payload.get("moneda", "—")},
            {"Campo": "Condiciones de pago", "Valor": payload.get("condiciones_pago", "—")},
            {"Campo": "Aprobador", "Valor": aprobador.get("email", "—")},
            {"Campo": "Fecha de aprobación", "Valor": aprobador.get("fecha_aprobacion", "—")},
            {"Campo": "Evidencia (sha256)", "Valor": f"{sha[:16]}…" if sha else "—"},
        ],
        hide_index=True,
    )

    posiciones = payload.get("posiciones") or []
    st.caption(f"Posiciones ({len(posiciones)})")
    st.dataframe(
        [
            {
                "Pos.": posicion.get("numero"),
                "Descripción (≤40)": posicion.get("descripcion"),
                "Cant.": posicion.get("cantidad"),
                "UM": posicion.get("unidad"),
                "Precio unitario": _pesos(posicion.get("precio_unitario")),
                "Centro de costo": posicion.get("centro_costo"),
                "Subárea": posicion.get("subarea"),
                "IVA": posicion.get("indicador_iva"),
            }
            for posicion in posiciones
            if isinstance(posicion, dict)
        ],
        hide_index=True,
    )

    excepciones = payload.get("excepciones") or []
    if excepciones:
        st.caption(f"Excepciones ({len(excepciones)})")
        st.dataframe(
            [
                {
                    "Código": excepcion.get("codigo"),
                    "Detalle": excepcion.get("detalle"),
                    "Confirmado por": excepcion.get("confirmado_por") or "— sin confirmar",
                }
                for excepcion in excepciones
                if isinstance(excepcion, dict)
            ],
            hide_index=True,
        )

    with st.expander("Payload crudo (lo que recibiría SAP)"):
        st.json(payload)


def _dibujar_validaciones() -> None:
    """Los diez controles del caso, tal como los devolvió `oc_validar`."""
    validacion = _validacion_actual()
    if not validacion:
        st.info("Cuando el agente valide el caso, los diez controles aparecerán aquí.")
        return

    apta = bool(validacion.get("apta"))
    st.markdown(
        f"**Caso** `{validacion.get('caso', '—')}` · "
        f"**Solicitud** `{validacion.get('solicitud_id', '—')}` · "
        f"**Apta:** {'✅ sí' if apta else '❌ no'}"
        + (" · **retroactiva** ⏱️" if validacion.get("retroactiva") else "")
    )

    _dibujar_montos_de_rc5()

    for titulo, clave, icono in (
        ("Bloqueos", "bloqueos", "⛔"),
        ("Confirmaciones", "confirmaciones", "⚠️"),
    ):
        veredictos = validacion.get(clave) or []
        if veredictos:
            st.caption(f"{icono} {titulo}")
            st.dataframe(
                [
                    {"Código": v.get("codigo"), "Detalle": v.get("detalle")}
                    for v in veredictos
                    if isinstance(v, dict)
                ],
                hide_index=True,
            )

    derivados = validacion.get("derivados") or {}
    if derivados:
        st.caption("🧩 Derivados de los maestros")
        st.dataframe(
            [{"Campo": clave, "Valor": valor} for clave, valor in derivados.items()],
            hide_index=True,
        )

    veredictos = validacion.get("veredictos") or []
    if veredictos:
        with st.expander("Matriz completa de los diez controles"):
            st.dataframe(
                [
                    {
                        "Control": v.get("codigo"),
                        "Tipo": v.get("tipo"),
                        "Detalle": v.get("detalle"),
                    }
                    for v in veredictos
                    if isinstance(v, dict)
                ],
                hide_index=True,
            )


def _dibujar_oc_creada() -> None:
    """El número de OC, si el agente ya la creó (HU-5)."""
    datos = _ultimos_datos("oc_crear")
    if not datos:
        return
    nota = " (idempotente: ya existía)" if datos.get("idempotente") else ""
    st.success(
        f"**OC `{datos.get('numero_oc', '—')}`** creada el {datos.get('fecha', '—')}{nota}"
    )


def _dibujar_llamadas() -> None:
    """Todas las llamadas de la sesión, en orden (CA4)."""
    llamadas = st.session_state.tool_calls
    if not llamadas:
        st.info("Todavía no hay llamadas a herramientas en esta sesión.")
        return
    st.caption(f"{len(llamadas)} llamadas en la sesión")
    for llamada in llamadas:
        _dibujar_tool_call(llamada)


def _dibujar_panel() -> None:
    st.subheader("Inspección")
    _dibujar_oc_creada()
    tabla, controles, llamadas = st.tabs(["🧾 OC en SAP", "✅ Controles", "🔧 Llamadas"])
    with tabla:
        _dibujar_oc_en_sap()
    with controles:
        _dibujar_validaciones()
    with llamadas:
        _dibujar_llamadas()


# ── Vista · barra lateral ──────────────────────────────────────────────────


def _dibujar_lateral() -> None:
    with st.sidebar:
        st.header("Agente de OC · SAP")
        st.caption(f"Sesión `{st.session_state.session_id[:8]}`")

        st.subheader("Casos")
        st.caption("Atajos de la demo: cargan el texto y lo envían.")
        for caso, etiqueta, prompt in ATAJOS:
            if st.button(etiqueta, key=f"atajo-{caso}", use_container_width=True):
                _encolar(prompt)

        st.divider()
        if st.button("🧹 Nueva sesión", use_container_width=True):
            _reiniciar_sesion()

        st.divider()
        _dibujar_estado()


def _dibujar_estado() -> None:
    """Proveedor y modelo, nunca la clave (C7 · PRD §6.4 `/api/health`)."""
    st.subheader("Estado")
    url = _url_de_la_api()
    if url:
        st.caption(f"Modo HTTP → `{url}`")
        _dibujar_salud_http(url)
        return

    st.caption("Modo en proceso (sin API HTTP)")
    try:
        from app.core.config import cargar_config, resumen_publico

        config = cargar_config(requiere_llm=False)
        resumen = resumen_publico(config)
        st.write(f"Proveedor: `{resumen['provider']}`")
        st.write(f"Modelo: `{resumen['model']}`")
        st.write(f"Tope de iteraciones: `{resumen['max_iteraciones']}`")
    except Exception as excepcion:  # noqa: BLE001
        st.caption(f"No pude leer la configuración: {excepcion}")
        return

    # C7 · solo se informa si la clave está puesta: se mide su longitud, nunca se
    # muestra. `cargar_config` la busca en el entorno y en el `.env`, así que
    # preguntarle a él es lo único que no miente.
    if config.openai_api_key.get_secret_value().strip():
        st.success("Clave del modelo configurada")
    else:
        st.warning("Falta `OPENAI_API_KEY`: el chat no podrá responder.")


def _dibujar_salud_http(url: str) -> None:
    import httpx

    try:
        respuesta = httpx.get(f"{url.rstrip('/')}/api/health", timeout=10.0)
        respuesta.raise_for_status()
        salud = respuesta.json()
    except Exception as excepcion:  # noqa: BLE001
        st.error(f"API no disponible: {excepcion}")
        return
    st.write(f"Proveedor: `{salud.get('provider', '—')}`")
    st.write(f"Modelo: `{salud.get('model', '—')}`")


# ── Estado de la sesión de Streamlit ───────────────────────────────────────


def _inicializar_estado() -> None:
    st.session_state.setdefault("session_id", str(uuid.uuid4()))
    # Un mensaje: {"rol": "user"|"assistant", "texto": str, "tool_calls": list}
    st.session_state.setdefault("historial", [])
    st.session_state.setdefault("tool_calls", [])
    # Resultados completos de las herramientas, para el panel: ver
    # `_refrescar_resultados`.
    st.session_state.setdefault("resultados", [])
    st.session_state.setdefault("necesita_confirmacion", False)
    st.session_state.setdefault("pendiente", None)


def _reiniciar_sesion() -> None:
    """Sesión nueva: el PRD §11 arranca la demo con una (`en tu chat, sesión nueva`)."""
    # El `Agente` no se borra: las sesiones viven dentro de él y un `session_id`
    # nuevo ya es una conversación nueva. Reconstruirlo volvería a leer el prompt
    # y a crear el cliente del proveedor sin ninguna necesidad.
    # No hace falta relanzar el script: la barra lateral se dibuja antes que el
    # chat, así que el resto de esta misma pasada ya pinta la sesión vacía.
    for clave in ("session_id", "historial", "tool_calls", "resultados", "necesita_confirmacion"):
        st.session_state.pop(clave, None)
    _inicializar_estado()


def _encolar(mensaje: str) -> None:
    """Deja un mensaje de usuario listo para el siguiente punto de proceso.

    Streamlit reejecuta el script completo en cada interacción. Un botón de la
    barra lateral se dibuja antes que el chat, así que su mensaje se atiende en
    esta misma pasada; uno de la parte baja (el de confirmar) llega tarde y lo
    recoge el `st.rerun()` del final de `main`. Encolar en vez de procesar en el
    sitio es lo que permite que los dos caminos compartan código.
    """
    st.session_state.pendiente = mensaje


def _propagar_secretos() -> None:
    """Copia los secretos de Streamlit al entorno del proceso.

    En Streamlit Community Cloud la clave se pega en Settings → Secrets, y
    `app/core/config.py` la busca en el entorno. Esta función es el puente.
    Nunca imprime ni devuelve el valor (C7).
    """
    try:
        secretos = st.secrets
    except Exception:  # noqa: BLE001
        return  # sin archivo de secretos: todo viene del entorno o del .env
    for nombre in VARIABLES_DEL_BACKEND:
        try:
            valor = secretos.get(nombre)
        except Exception:  # noqa: BLE001
            valor = None
        if valor and not os.environ.get(nombre, "").strip():
            os.environ[nombre] = str(valor)


# ── Turno ──────────────────────────────────────────────────────────────────


def _procesar_turno(mensaje: str) -> None:
    """Un turno completo: mensaje de usuario → agente → respuesta en pantalla."""
    with st.chat_message("assistant"):
        # Comportamiento 3 del PRD §6.1: el indicador de "pensando".
        with st.spinner("Pensando…"):
            turno = _responder(mensaje)

        st.markdown(turno.reply)
        for llamada in turno.tool_calls:
            _dibujar_tool_call(llamada)

    st.session_state.historial.append(
        {"rol": "assistant", "texto": turno.reply, "tool_calls": list(turno.tool_calls)}
    )
    st.session_state.tool_calls.extend(turno.tool_calls)
    st.session_state.necesita_confirmacion = turno.needs_confirmation
    # El panel de la derecha se dibuja después de este punto, así que recoge los
    # resultados completos del turno que acaba de pasar.
    _refrescar_resultados()


def main() -> None:
    st.set_page_config(page_title="Agente de OC · SAP", page_icon="🧾", layout="wide")
    _propagar_secretos()
    _inicializar_estado()
    _dibujar_lateral()

    st.title("🧾 Órdenes de compra · agente conversacional")
    st.caption(
        "Lee el paquete de compra, corre los diez controles, construye la OC y la crea en "
        "el SAP simulado. Cada valor que ves salió de una herramienta (CA2)."
    )

    # El mensaje a procesar sale de dos sitios: el campo de entrada —cuyo envío
    # ya provocó esta pasada, y cuyo texto está en `st.session_state.entrada`— o
    # un botón que encoló algo. Se resuelve antes de dibujar para que el
    # historial ya lo incluya cuando se pinte la conversación.
    pendiente = st.session_state.pendiente or st.session_state.get("entrada")
    st.session_state.pendiente = None
    if pendiente:
        st.session_state.historial.append({"rol": "user", "texto": pendiente, "tool_calls": []})

    conversacion, panel = st.columns([3, 2], gap="large")
    with conversacion:
        _dibujar_historial()
        if pendiente:
            _procesar_turno(pendiente)
        if st.session_state.necesita_confirmacion:
            _dibujar_confirmacion()

    # El panel se dibuja después de procesar el turno, así muestra el estado
    # recién llegado. Streamlit respeta la posición de cada columna aunque el
    # código de la derecha se ejecute al final.
    with panel:
        _dibujar_panel()

    # Comportamiento 2 del PRD §6.1: el campo de entrada. Va en el nivel
    # superior del script para que Streamlit lo ancle al pie de la página. Su
    # texto se lee arriba desde `st.session_state.entrada`, no aquí: enviarlo ya
    # relanzó el script, y procesarlo en este punto obligaría a una pasada más.
    st.chat_input("Escribe tu mensaje… por ejemplo: Procesa la solicitud sol-004", key="entrada")

    # Único `st.rerun()` del front: recoge lo que encoló un botón dibujado
    # después del punto de proceso —el de confirmar— y lo atiende en la pasada
    # siguiente. Va al final del script, fuera de toda columna.
    if st.session_state.pendiente:
        st.rerun()


main()
