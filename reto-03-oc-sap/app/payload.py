"""El payload de la OC: construirlo, trazarlo y crear la orden en SAP con él.

HU-3 · HU-5 · I6 · I7 · ADR-004. Aquí vive la regla que hace cumplible CA2:
**ningún valor del payload sale de lo que el modelo mandó**. La validación se
vuelve a correr sobre los fixtures —al construir y otra vez al crear—, los
derivados se comparan contra los que calcularon RC6 y RC7, y cada campo queda en
`out/<caso>/trazabilidad.json` con la fuente de la que salió.

`construir_payload` requiere que la evidencia exista: el payload consume su
sha256. No se genera a escondidas desde aquí, porque el orden de llamada es parte
del proceso que el agente debe seguir y que el chat debe mostrar.

Separado de `app/tools/oc.py` en la rama `refactor/langchain`. Ambas funciones
devuelven el string JSON del contrato: sus desenlaces de error son mensajes de
negocio con el código del control que los produjo (RC1, RC6, RC7) o con el
bloqueo que impide crear, no excepciones técnicas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.controles.resolutores import resolver_proveedor
from app.core.io import escribir_json, leer_texto, ruta_salida
from app.core.log import ResultadoIntento, registrar_control
from app.core.modelos import OrdenCompra, Paquete, ResultadoValidacion, error, ok
from app.core.normalizar import inferir_unidad, truncar_descripcion
from app.evidencia import ruta_evidencia, sha256
from app.paquete import ruta_legible, validar_caso
from app.respuesta import codigos, describir, detallar
from app.sap.adapter import SapAdapter
from app.sap.mock import SapMock

ARCHIVO_TRAZABILIDAD = "trazabilidad.json"

# Invariante I5: las posiciones van 10, 20, 30… El modelo `PosicionOC` no lo
# valida (es un `int` cualquiera), así que es responsabilidad de esta capa
# (hallazgo H2 del plan).
PASO_POSICION = 10


def construir_payload(raiz: Path, caso: str, derivados_recibidos: dict[str, str] | None) -> str:
    """Construye el payload, escribe la trazabilidad y devuelve el string JSON."""
    paquete, maestros, resultado = validar_caso(raiz, caso)
    solicitud = paquete.solicitud

    # El payload consume el sha256 de la evidencia, así que la evidencia tiene
    # que existir primero.
    evidencia = leer_texto(ruta_evidencia(raiz, caso))
    if evidencia is None:
        return error("genera primero la evidencia: llama a oc_generar_evidencia con este caso.")

    if paquete.aprobacion is None:
        return error(
            f"El caso {caso} no tiene aprobación, y la OC exige registrar quién aprobó. "
            "Pide al solicitante el correo de aprobación (RC2)."
        )

    proveedor = resolver_proveedor(paquete, maestros)
    if proveedor is None:
        return error(
            f"El proveedor {solicitud.proveedor_nombre!r} "
            f"(NIT {solicitud.proveedor_nit!r}) no existe en el maestro, así que no hay "
            "código SAP con el que construir la OC. Es el bloqueo RC1."
        )

    derivados = _derivados_confiables(resultado, derivados_recibidos)
    if isinstance(derivados, str):
        return derivados

    condiciones_pago = solicitud.condiciones_pago or derivados.get("condiciones_pago")
    if not condiciones_pago:
        return error("Falta condiciones_pago y no se pudo derivar del proveedor (RC7).")

    indicador_iva = solicitud.indicador_iva or derivados.get("indicador_iva")
    if not indicador_iva:
        return error("Falta indicador_iva y no se pudo derivar del proveedor (RC6).")

    item_cotizacion = paquete.cotizacion.item if paquete.cotizacion is not None else None

    crudo: dict[str, Any] = {
        "referencia": {
            "solicitud_id": solicitud.solicitud_id,
            "correo_id": paquete.correo.id,
            "cotizacion_ref": (
                paquete.cotizacion.referencia if paquete.cotizacion is not None else None
            ),
        },
        # sociedad y organizacion_compras son constantes del modelo (I4).
        "proveedor": {
            "codigo_sap": proveedor.codigo_sap,
            "nit": proveedor.nit,
            "nombre": proveedor.nombre,
        },
        "moneda": solicitud.moneda,
        "condiciones_pago": condiciones_pago,
        "aprobador": {
            "email": paquete.aprobacion.de,
            "fecha_aprobacion": paquete.aprobacion.fecha,
            "evidencia_sha256": sha256(evidencia),
        },
        "posiciones": [
            {
                # I5 · H2: la numeración en pasos de 10 la pone esta capa, porque
                # `PosicionOC.numero` es un int cualquiera para el modelo.
                "numero": PASO_POSICION,
                # I1 · ADR-004: SAP limita el texto breve a 40 caracteres. La
                # descripción completa queda en trazabilidad.json.
                "descripcion": truncar_descripcion(solicitud.descripcion),
                "cantidad": solicitud.cantidad,
                # ADR-005: la unidad se infiere del ítem de la cotización, que es
                # el texto inequívoco; la descripción es el respaldo.
                "unidad": inferir_unidad(item_cotizacion, solicitud.descripcion),
                # ADR-007: el precio va con IVA incluido, igual que el monto que
                # aprobó el líder y contra el que corrieron RC3, RC5 y RC10.
                "precio_unitario": solicitud.valor_unitario,
                "centro_costo": solicitud.centro_costo,
                "subarea": solicitud.subarea,
                "indicador_iva": indicador_iva,
            }
        ],
        # Una excepción por cada confirmación pendiente. Nacen sin confirmar:
        # `oc_crear` escribe el `confirmado_por` cuando alguien confirma (ADR-006).
        "excepciones": [
            {"codigo": veredicto.codigo, "detalle": veredicto.detalle, "confirmado_por": None}
            for veredicto in resultado.confirmaciones
        ],
    }

    # El payload se valida antes de devolverlo: si una pieza no cumple el esquema
    # de SAP, el error aparece aquí y no al intentar crear la orden.
    orden = OrdenCompra.model_validate(crudo)

    destino = escribir_json(
        ruta_salida(raiz, caso, ARCHIVO_TRAZABILIDAD),
        _trazabilidad(paquete, orden, derivados),
    )
    return ok(
        {
            "payload": orden.model_dump(mode="json"),
            "ruta_trazabilidad": ruta_legible(raiz, destino),
            "descripcion_truncada": orden.posiciones[0].descripcion,
        }
    )


def _derivados_confiables(
    resultado: ResultadoValidacion, recibidos: dict[str, str] | None
) -> dict[str, str] | str:
    """Los derivados que calcularon RC6 y RC7, o un error si el modelo los contradice.

    Los valores que se usan son **siempre** los que salieron de los maestros en
    `evaluar_controles`. Los `derivados` del argumento solo se comparan: si el
    modelo manda un `indicador_iva` que ningún control derivó, es un valor
    inventado y se rechaza en lugar de escribirlo en la OC (CA2).
    """
    calculados = dict(resultado.derivados)
    for campo, valor in (recibidos or {}).items():
        if calculados.get(campo) != valor:
            return error(
                f"El derivado {campo}={valor!r} no coincide con el que calculó oc_validar "
                f"({calculados.get(campo)!r}). Usa los derivados que devuelve oc_validar."
            )
    return calculados


def _trazabilidad(
    paquete: Paquete, orden: OrdenCompra, derivados: dict[str, str]
) -> dict[str, Any]:
    """Fuente de cada campo del payload (I7 · ADR-004).

    Responde la pregunta de la auditoría: *¿de dónde salió este valor?* Las
    fuentes posibles son `solicitud`, `correo`, `cotizacion`, `aprobacion`,
    `maestro.<nombre>`, `derivado`, `constante`, `evidencia` y `validacion`.
    """
    posicion = orden.posiciones[0]
    fuente_iva = "derivado" if "indicador_iva" in derivados else "solicitud"
    fuente_pago = "derivado" if "condiciones_pago" in derivados else "solicitud"

    campos: dict[str, dict[str, Any]] = {
        "referencia.solicitud_id": _traza(orden.referencia.solicitud_id, "solicitud"),
        "referencia.correo_id": _traza(orden.referencia.correo_id, "correo"),
        "referencia.cotizacion_ref": _traza(orden.referencia.cotizacion_ref, "cotizacion"),
        "sociedad": _traza(orden.sociedad, "constante"),
        "organizacion_compras": _traza(orden.organizacion_compras, "constante"),
        "proveedor.codigo_sap": _traza(orden.proveedor.codigo_sap, "maestro.proveedores"),
        "proveedor.nit": _traza(orden.proveedor.nit, "maestro.proveedores"),
        "proveedor.nombre": _traza(orden.proveedor.nombre, "maestro.proveedores"),
        "moneda": _traza(orden.moneda, "solicitud"),
        "condiciones_pago": _traza(orden.condiciones_pago, fuente_pago),
        "aprobador.email": _traza(orden.aprobador.email, "aprobacion"),
        "aprobador.fecha_aprobacion": _traza(orden.aprobador.fecha_aprobacion, "aprobacion"),
        "aprobador.evidencia_sha256": _traza(orden.aprobador.evidencia_sha256, "evidencia"),
        "posiciones[0].numero": _traza(posicion.numero, "constante"),
        "posiciones[0].descripcion": _traza(posicion.descripcion, "solicitud"),
        "posiciones[0].cantidad": _traza(posicion.cantidad, "solicitud"),
        "posiciones[0].unidad": _traza(posicion.unidad, "cotizacion"),
        "posiciones[0].precio_unitario": _traza(posicion.precio_unitario, "solicitud"),
        "posiciones[0].centro_costo": _traza(posicion.centro_costo, "solicitud"),
        "posiciones[0].subarea": _traza(posicion.subarea, "solicitud"),
        "posiciones[0].indicador_iva": _traza(posicion.indicador_iva, fuente_iva),
        "excepciones": _traza([e.codigo for e in orden.excepciones], "validacion"),
    }

    # ADR-004: la descripción completa se preserva aquí, porque el payload solo
    # lleva los 40 caracteres que admite el texto breve de SAP.
    campos["posiciones[0].descripcion"]["valor_completo"] = paquete.solicitud.descripcion

    return {
        "caso": paquete.caso,
        "solicitud_id": paquete.solicitud.solicitud_id,
        "descripcion_completa": paquete.solicitud.descripcion,
        "derivados": derivados,
        "campos": campos,
    }


def _traza(valor: object, fuente: str) -> dict[str, Any]:
    """Una entrada de `trazabilidad.json`: el valor y de dónde salió."""
    return {"valor": valor, "fuente": fuente}


# ── Creación en SAP (HU-5) ─────────────────────────────────────────────────


async def crear_orden(
    raiz: Path, session_id: str, caso: str, payload: dict[str, Any], confirmado: bool
) -> str:
    """Los cinco desenlaces, cada uno con su fila en `control.csv` (I6).

    La validación se **vuelve a correr aquí** sobre los fixtures. No se confía en
    el payload recibido ni en que el modelo diga que ya validó: es la defensa de
    CA2 contra un modelo que afirme un resultado que nunca calculó.
    """
    paquete, maestros, resultado = validar_caso(raiz, caso)
    solicitud_id = paquete.solicitud.solicitud_id
    bloqueos = codigos(resultado.bloqueos)
    confirmaciones = codigos(resultado.confirmaciones)

    def registrar(desenlace: ResultadoIntento, numero_oc: str | None) -> None:
        """Una fila por intento (I6), con el desenlace y los códigos en juego."""
        registrar_control(
            raiz, solicitud_id, desenlace, numero_oc, resultado.retroactiva, bloqueos, confirmaciones
        )

    try:
        orden = OrdenCompra.model_validate(payload)
    except Exception as excepcion:  # noqa: BLE001
        registrar("error", None)
        return error(
            f"El payload no cumple el esquema de la OC. {describir(excepcion)} "
            "Reconstrúyelo con oc_construir_payload."
        )

    # El payload debe ser del caso que se dice crear. Sin esta comprobación un
    # payload de otro caso crearía una OC con los controles de este.
    if orden.referencia.solicitud_id != solicitud_id:
        registrar("error", None)
        return error(
            f"El payload es de la solicitud {orden.referencia.solicitud_id!r} y el caso "
            f"{caso} corresponde a {solicitud_id!r}. Reconstruye el payload."
        )

    # Un bloqueo NO se levanta con `confirmado=True`: esa es justamente la
    # diferencia entre bloqueo y confirmación (HU-2). sol-002 y sol-003 no crean
    # OC ni con confirmación.
    if resultado.bloqueos:
        registrar("bloqueada", None)
        return error(
            f"No se puede crear la OC de {solicitud_id}: hay bloqueos que requieren "
            f"corrección, no confirmación. {detallar(resultado.bloqueos)}"
        )

    if resultado.confirmaciones and not confirmado:
        registrar("pendiente_confirmacion", None)
        return error(
            f"requiere confirmación del usuario antes de crear la OC de {solicitud_id}: "
            f"{detallar(resultado.confirmaciones)}"
        )

    if confirmado:
        # ADR-006: la única identidad que existe en el sistema es la sesión del
        # chat. Se escribe antes de crear para que quede dentro de la OC guardada.
        for excepcion_oc in orden.excepciones:
            excepcion_oc.confirmado_por = session_id

    sap: SapAdapter = SapMock(raiz, maestros)
    # La búsqueda previa es lo que distingue "creada" de "idempotente": el mock
    # devuelve el mismo número en ambos casos (C10), pero el usuario necesita
    # saber que no se creó una segunda OC.
    existente = await sap.buscar_orden_por_referencia(solicitud_id)
    creada = await sap.crear_orden(orden)
    idempotente = existente is not None

    registrar("idempotente" if idempotente else "creada", creada["numero_oc"])
    return ok(
        {
            "numero_oc": creada["numero_oc"],
            "fecha": creada["fecha"],
            "idempotente": idempotente,
            "retroactiva": resultado.retroactiva,
        }
    )
