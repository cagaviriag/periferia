"""Tests de T1.5 · RC8 (OC retroactiva) y RC9 (fecha de aprobación).

Las fechas esperadas se leen de los **fixtures reales**. Del `specs/01-oracle.md`
viene solo el tipo de veredicto de cada caso, que es lo que se verifica.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.controles.rc08_rc09 import rc8_retroactiva, rc9_fecha_aprobacion, solo_fecha
from app.core.modelos import Aprobacion, Correo, Cotizacion, Factura, Paquete, Solicitud

RAIZ = Path(__file__).resolve().parent.parent
SOLICITUDES = RAIZ / "fixtures" / "reto-03" / "solicitudes"

CASOS = ["sol-001", "sol-002", "sol-003", "sol-004", "sol-005", "sol-006"]

# Oráculo §matriz: solo sol-005 trae factura y por eso es la única retroactiva.
RC8_ESPERADO = {
    "sol-001": "ok",
    "sol-002": "ok",
    "sol-003": "ok",
    "sol-004": "ok",
    "sol-005": "confirmacion",
    "sol-006": "ok",
}

# Los tres casos cuya aprobación es del mismo día de la solicitud. El PRD dice
# "≥", así que deben dar `ok`: un ">" mal escrito crearía tres confirmaciones
# falsas y rompería la matriz del oráculo.
CASOS_MISMO_DIA = ["sol-002", "sol-004", "sol-006"]


# ── Armado del paquete desde los fixtures ──────────────────────────────────
#
# Andamiaje de test, no el parser de producción (`factura.txt` lo parsea T2.3).
# T1.5 declara que estos controles reciben el `Paquete` ya armado.


def _leer_json(ruta: Path) -> dict[str, Any]:
    crudo = json.loads(ruta.read_text(encoding="utf-8"))
    assert isinstance(crudo, dict)
    return crudo


def _construir_factura(carpeta: Path) -> Factura | None:
    """Lee `factura.txt` si existe. Cinco de los seis casos no la traen."""
    archivo = carpeta / "factura.txt"
    if not archivo.is_file():
        return None

    texto = archivo.read_text(encoding="utf-8")
    numero = re.search(r"No\. (\S+)", texto)
    fecha = re.search(r"Fecha de emisión: (\S+)", texto)
    total = re.search(r"TOTAL: COP ([\d.]+)", texto)
    assert numero is not None and fecha is not None and total is not None

    return Factura(
        numero=numero.group(1),
        fecha=fecha.group(1),
        total=int(total.group(1).replace(".", "")),
    )


def construir_paquete(caso: str) -> Paquete:
    """Arma el `Paquete` de un caso leyendo sus fixtures. Solo lectura (C1)."""
    carpeta = SOLICITUDES / caso
    aprobacion_cruda = _leer_json(carpeta / "aprobacion.json")
    texto_cotizacion = (carpeta / "cotizacion.txt").read_text(encoding="utf-8")
    solicitud_cruda = _leer_json(carpeta / "solicitud.json")

    return Paquete(
        caso=caso,
        correo=Correo.model_validate(_leer_json(carpeta / "correo.json")),
        solicitud=Solicitud.model_validate(solicitud_cruda),
        cotizacion=Cotizacion(
            referencia=None,
            proveedor=solicitud_cruda["proveedor_nombre"],
            nit=None,
            total=solicitud_cruda["valor_total"],
            moneda="COP",
            validez_hasta=None,
            item=None,
            texto=texto_cotizacion,
        ),
        aprobacion=Aprobacion(
            de=aprobacion_cruda["de"],
            fecha=aprobacion_cruda["fecha"],
            aprobado="aprobado" in aprobacion_cruda["cuerpo"].lower(),
            texto=aprobacion_cruda["cuerpo"],
        ),
        factura=_construir_factura(carpeta),
    )


def con_solicitud(caso: str, **cambios: object) -> Paquete:
    """Variante de un caso real con algunos campos de la solicitud alterados."""
    paquete = construir_paquete(caso)
    return paquete.model_copy(update={"solicitud": paquete.solicitud.model_copy(update=cambios)})


def con_aprobacion_del(caso: str, fecha: str) -> Paquete:
    """Variante del caso con otra fecha de aprobación."""
    paquete = construir_paquete(caso)
    assert paquete.aprobacion is not None
    return paquete.model_copy(update={"aprobacion": paquete.aprobacion.model_copy(
        update={"fecha": fecha}
    )})


# ── solo_fecha ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("2026-08-21T10:02:00-05:00", "2026-08-21"),   # formato de aprobacion.json
        ("2026-08-20", "2026-08-20"),                  # formato de solicitud.json
        ("2026-08-10", "2026-08-10"),                  # formato de factura.txt
        ("basura", ""),
        ("", ""),
        ("20-08-2026", ""),                            # día primero: no es ISO
        ("2026/08/20", ""),
        ("2026-8-2", ""),                              # sin ceros a la izquierda
    ],
)
def test_solo_fecha(entrada: str, esperado: str) -> None:
    assert solo_fecha(entrada) == esperado


@pytest.mark.parametrize("caso", CASOS)
def test_solo_fecha_sobre_los_fixtures_reales(caso: str) -> None:
    paquete = construir_paquete(caso)
    assert paquete.aprobacion is not None

    # Las dos fuentes traen formatos distintos y ambas se recortan a YYYY-MM-DD.
    assert solo_fecha(paquete.solicitud.fecha_solicitud).startswith("2026-08-")
    assert len(solo_fecha(paquete.aprobacion.fecha)) == 10
    assert "T" not in solo_fecha(paquete.aprobacion.fecha)


# ── Los seis casos contra el oráculo ───────────────────────────────────────


@pytest.mark.parametrize("caso", CASOS)
def test_rc8_coincide_con_el_oraculo(caso: str) -> None:
    veredicto = rc8_retroactiva(construir_paquete(caso))

    assert veredicto.codigo == "RC8"
    assert veredicto.tipo == RC8_ESPERADO[caso]
    assert veredicto.datos["retroactiva"] is (caso == "sol-005")


@pytest.mark.parametrize("caso", CASOS)
def test_rc9_ok_en_los_seis_casos(caso: str) -> None:
    veredicto = rc9_fecha_aprobacion(construir_paquete(caso))

    assert veredicto.codigo == "RC9"
    assert veredicto.tipo == "ok"
    assert veredicto.datos["dias_anticipacion"] == 0


@pytest.mark.parametrize("caso", CASOS_MISMO_DIA)
def test_rc9_aprobacion_el_mismo_dia_es_ok(caso: str) -> None:
    """El PRD dice `≥`: el mismo día pasa. Con `>` serían tres falsas confirmaciones."""
    paquete = construir_paquete(caso)
    assert paquete.aprobacion is not None
    # Se verifica primero que el fixture realmente es del mismo día.
    assert solo_fecha(paquete.aprobacion.fecha) == solo_fecha(paquete.solicitud.fecha_solicitud)

    veredicto = rc9_fecha_aprobacion(paquete)

    assert veredicto.tipo == "ok"
    assert veredicto.datos["fecha_aprobacion"] == veredicto.datos["fecha_solicitud"]


# ── sol-005 · el caso central ──────────────────────────────────────────────


def test_sol_005_es_retroactiva_con_17_dias_de_desvio() -> None:
    """Caso central de T1.5 (oráculo §sol-005 · O4)."""
    paquete = construir_paquete("sol-005")
    # Las dos fechas salen del fixture, no de la memoria.
    assert paquete.factura is not None
    assert paquete.factura.fecha == "2026-08-10"
    assert paquete.solicitud.fecha_solicitud == "2026-08-27"

    rc8 = rc8_retroactiva(paquete)

    assert rc8.tipo == "confirmacion"
    assert rc8.datos["retroactiva"] is True
    assert rc8.datos["dias_desvio"] == 17
    assert rc8.datos["numero_factura"] == "FC-88231"
    assert rc8.datos["fecha_factura"] == "2026-08-10"
    assert rc8.datos["fecha_solicitud"] == "2026-08-27"
    # El detalle menciona ambas fechas y explica qué significa.
    assert "2026-08-10" in rc8.detalle
    assert "2026-08-27" in rc8.detalle
    assert "retroactiva" in rc8.detalle


def test_sol_005_el_proceso_corrio_al_reves() -> None:
    """Evidencia para SOLUCION.md §9.1.7: se cotizó, se facturó y después se solicitó."""
    paquete = construir_paquete("sol-005")
    assert paquete.cotizacion is not None
    assert paquete.factura is not None

    fecha_cotizacion = re.search(r"Fecha: (\S+)", paquete.cotizacion.texto)
    assert fecha_cotizacion is not None
    cotizacion = solo_fecha(fecha_cotizacion.group(1))
    factura = solo_fecha(paquete.factura.fecha)
    solicitud = solo_fecha(paquete.solicitud.fecha_solicitud)

    # Orden cronológico real del fixture: cotización → factura → solicitud formal.
    assert cotizacion == "2026-08-05"
    assert factura == "2026-08-10"
    assert solicitud == "2026-08-27"
    assert cotizacion < factura < solicitud
    # 22 días entre la cotización y la solicitud; 17 entre la factura y la solicitud.
    assert rc8_retroactiva(paquete).datos["dias_desvio"] == 17


# ── RC8 · bordes ───────────────────────────────────────────────────────────


def test_rc8_sin_factura_es_ok() -> None:
    paquete = construir_paquete("sol-001")
    assert paquete.factura is None

    veredicto = rc8_retroactiva(paquete)

    assert veredicto.tipo == "ok"
    assert veredicto.datos["retroactiva"] is False
    assert veredicto.datos["dias_desvio"] == 0
    assert veredicto.datos["numero_factura"] is None


def test_rc8_con_factura_posterior_es_ok() -> None:
    paquete = construir_paquete("sol-005")
    assert paquete.factura is not None
    posterior = paquete.model_copy(update={"factura": paquete.factura.model_copy(
        update={"fecha": "2026-09-01"}
    )})

    veredicto = rc8_retroactiva(posterior)

    assert veredicto.tipo == "ok"
    assert veredicto.datos["retroactiva"] is False
    assert veredicto.datos["dias_desvio"] == 0


def test_rc8_con_factura_del_mismo_dia_es_ok() -> None:
    paquete = construir_paquete("sol-005")
    assert paquete.factura is not None
    mismo_dia = paquete.model_copy(update={"factura": paquete.factura.model_copy(
        update={"fecha": paquete.solicitud.fecha_solicitud}
    )})

    assert rc8_retroactiva(mismo_dia).tipo == "ok"


def test_rc8_con_fecha_de_solicitud_ilegible_confirma_sin_lanzar() -> None:
    paquete = con_solicitud("sol-005", fecha_solicitud="no-es-fecha")

    veredicto = rc8_retroactiva(paquete)

    assert veredicto.tipo == "confirmacion"
    assert veredicto.datos["fecha_solicitud"] == ""
    assert veredicto.datos["retroactiva"] is False
    assert "no-es-fecha" in veredicto.detalle


def test_rc8_con_fecha_de_factura_ilegible_confirma_sin_lanzar() -> None:
    paquete = construir_paquete("sol-005")
    assert paquete.factura is not None
    ilegible = paquete.model_copy(update={"factura": paquete.factura.model_copy(
        update={"fecha": "sin fecha"}
    )})

    veredicto = rc8_retroactiva(ilegible)

    assert veredicto.tipo == "confirmacion"
    assert veredicto.datos["retroactiva"] is False


def test_rc8_con_fecha_imposible_no_lanza() -> None:
    # '2026-09-31' pasa la validación de forma pero no existe en el calendario.
    # La comparación de cadenas sigue funcionando (la factura del 08-10 es anterior),
    # y `dias_desvio` cae a 0 porque `date.fromisoformat` falla y se atrapa.
    paquete = con_solicitud("sol-005", fecha_solicitud="2026-09-31")

    veredicto = rc8_retroactiva(paquete)

    assert veredicto.tipo == "confirmacion"
    assert veredicto.datos["retroactiva"] is True
    assert veredicto.datos["dias_desvio"] == 0


# ── RC9 · bordes ───────────────────────────────────────────────────────────


def test_rc9_con_aprobacion_anterior_confirma() -> None:
    # sol-001 se solicitó el 2026-08-20; se antedata la aprobación cinco días.
    paquete = con_aprobacion_del("sol-001", "2026-08-15")
    assert paquete.solicitud.fecha_solicitud == "2026-08-20"

    veredicto = rc9_fecha_aprobacion(paquete)

    assert veredicto.tipo == "confirmacion"
    assert veredicto.datos["dias_anticipacion"] == 5
    assert "2026-08-15" in veredicto.detalle
    assert "2026-08-20" in veredicto.detalle


def test_rc9_sin_aprobacion_es_no_evaluable() -> None:
    # RC2 ya bloquea por la ausencia; RC9 no la duplica.
    paquete = construir_paquete("sol-001").model_copy(update={"aprobacion": None})

    veredicto = rc9_fecha_aprobacion(paquete)

    assert veredicto.tipo == "no_evaluable"
    assert veredicto.datos["fecha_aprobacion"] is None


def test_rc9_con_fecha_ilegible_confirma_sin_lanzar() -> None:
    paquete = con_aprobacion_del("sol-001", "pendiente")

    veredicto = rc9_fecha_aprobacion(paquete)

    assert veredicto.tipo == "confirmacion"
    assert veredicto.datos["fecha_aprobacion"] == ""
    assert "pendiente" in veredicto.detalle


def test_rc9_ignora_la_hora_de_la_aprobacion() -> None:
    # sol-004 aprueba a las 18:45 del mismo día: la regla es de calendario, no de
    # instante, así que la hora y el offset no cambian el veredicto.
    tarde = con_aprobacion_del("sol-004", "2026-08-26T23:59:59-05:00")
    temprano = con_aprobacion_del("sol-004", "2026-08-26T00:00:01+14:00")

    assert rc9_fecha_aprobacion(tarde).tipo == "ok"
    assert rc9_fecha_aprobacion(temprano).tipo == "ok"


# ── Pureza: los controles no lanzan y no mutan el paquete ──────────────────


@pytest.mark.parametrize("caso", CASOS)
def test_los_controles_no_mutan_el_paquete(caso: str) -> None:
    paquete = construir_paquete(caso)
    antes = paquete.model_dump_json()

    rc8_retroactiva(paquete)
    rc9_fecha_aprobacion(paquete)

    assert paquete.model_dump_json() == antes
