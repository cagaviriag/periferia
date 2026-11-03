"""Tests de RC6 y RC7 (T1.4).

El test central es sol-006: RC6 devuelve `confirmacion` y RC7 devuelve
`derivado`. Ambos derivan del mismo proveedor, pero solo el IVA exige confirmar
por su impacto fiscal (PRD §7.3).

Los valores esperados (`C1`, `Z030`) se leen del maestro de proveedores, no se
escriben a mano: si el fixture cambia, el test se entera.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.controles.rc06_rc07 import rc6_indicador_iva, rc7_condiciones_pago
from app.core.modelos import Aprobacion, Correo, Maestros, Paquete, Solicitud

RAIZ = Path(__file__).resolve().parent.parent
FIXTURES = RAIZ / "fixtures" / "reto-03"
SOLICITUDES = FIXTURES / "solicitudes"
MAESTROS = FIXTURES / "maestros"

CASOS = ["sol-001", "sol-002", "sol-003", "sol-004", "sol-005", "sol-006"]


def leer_json(ruta: Path) -> Any:
    """Lee un fixture JSON. Solo lectura: C1 declara `fixtures/` inmutable."""
    return json.loads(ruta.read_text(encoding="utf-8"))


def cargar_maestros() -> Maestros:
    """Arma `Maestros` con los 4 JSON reales."""
    return Maestros.model_validate(
        {
            "proveedores": leer_json(MAESTROS / "proveedores.json"),
            "centros_costo": leer_json(MAESTROS / "centros-costo.json"),
            "indicadores_iva": leer_json(MAESTROS / "indicadores-iva.json"),
            "condiciones_pago": leer_json(MAESTROS / "condiciones-pago.json"),
        }
    )


def cargar_paquete(caso: str) -> Paquete:
    """Arma el `Paquete` de un caso desde sus fixtures.

    `cotizacion` y `factura` quedan en None: RC6 y RC7 no los leen.
    """
    aprobacion_cruda = leer_json(SOLICITUDES / caso / "aprobacion.json")
    cuerpo: str = aprobacion_cruda["cuerpo"]
    return Paquete(
        caso=caso,
        correo=Correo.model_validate(leer_json(SOLICITUDES / caso / "correo.json")),
        solicitud=Solicitud.model_validate(leer_json(SOLICITUDES / caso / "solicitud.json")),
        cotizacion=None,
        aprobacion=Aprobacion(
            de=aprobacion_cruda["de"],
            fecha=aprobacion_cruda["fecha"],
            aprobado="aprobado" in cuerpo.lower(),
            texto=cuerpo,
        ),
        factura=None,
    )


def con_solicitud(paquete: Paquete, **cambios: Any) -> Paquete:
    """Copia del paquete con campos de la solicitud modificados."""
    solicitud = paquete.solicitud.model_copy(update=cambios)
    return paquete.model_copy(update={"solicitud": solicitud})


def proveedor_del_maestro(codigo_sap: str) -> dict[str, Any]:
    """Busca un proveedor en `proveedores.json` por su código SAP."""
    for crudo in leer_json(MAESTROS / "proveedores.json"):
        if crudo["codigo_sap"] == codigo_sap:
            resultado: dict[str, Any] = crudo
            return resultado
    raise AssertionError(f"El fixture dejó de traer el proveedor {codigo_sap}")


# ── Los 6 casos reales (oráculo §matriz) ───────────────────────────────────

ESPERADO_POR_CASO = {
    "sol-001": ("ok", "ok"),                    # C1 / Z030
    "sol-002": ("ok", "ok"),                    # C1 / Z030
    "sol-003": ("ok", "ok"),                    # C1 / Z030
    "sol-004": ("ok", "ok"),                    # C1 / Z000
    "sol-005": ("ok", "ok"),                    # C1 / Z015
    "sol-006": ("confirmacion", "derivado"),    # no trae ninguno de los dos
}


@pytest.mark.parametrize("caso", CASOS)
def test_rc6_y_rc7_en_los_seis_casos(caso: str) -> None:
    maestros = cargar_maestros()
    paquete = cargar_paquete(caso)
    tipo_rc6, tipo_rc7 = ESPERADO_POR_CASO[caso]

    assert rc6_indicador_iva(paquete, maestros).tipo == tipo_rc6
    assert rc7_condiciones_pago(paquete, maestros).tipo == tipo_rc7


# ── sol-006: el test central de la tarea ───────────────────────────────────


def test_sol_006_deriva_ambos_valores_del_maestro_del_proveedor() -> None:
    maestros = cargar_maestros()
    paquete = cargar_paquete("sol-006")
    assert paquete.solicitud.indicador_iva is None
    assert paquete.solicitud.condiciones_pago is None

    # Los valores esperados salen del fixture, no están escritos a mano.
    proveedor = proveedor_del_maestro("100234")

    rc6 = rc6_indicador_iva(paquete, maestros)
    rc7 = rc7_condiciones_pago(paquete, maestros)

    assert rc6.tipo == "confirmacion"
    assert rc6.datos["indicador_iva"] == proveedor["indicador_iva_default"]
    assert rc6.datos["origen"] == "derivado"

    # RC7 solo informa: `derivado`, NO `confirmacion`. Es la diferencia que se
    # califica (PRD §7.3) y la razón de que estos dos controles no sean uno.
    assert rc7.tipo == "derivado"
    assert rc7.datos["condiciones_pago"] == proveedor["condiciones_pago_default"]
    assert rc7.datos["origen"] == "derivado"


def test_sol_006_el_detalle_dice_de_donde_salio_cada_valor() -> None:
    maestros = cargar_maestros()
    paquete = cargar_paquete("sol-006")

    rc6 = rc6_indicador_iva(paquete, maestros)
    rc7 = rc7_condiciones_pago(paquete, maestros)

    # El usuario debe poder confirmar sin investigar: el código, la descripción y
    # el proveedor del que se tomó.
    assert "C1" in rc6.detalle
    assert "IVA 19%" in rc6.detalle
    assert "TecnoSuministros" in rc6.detalle
    assert "Z030" in rc7.detalle
    assert "TecnoSuministros" in rc7.detalle


# ── Códigos inexistentes: bloqueo ──────────────────────────────────────────


def test_indicador_de_iva_inexistente_bloquea_y_lista_los_validos() -> None:
    paquete = con_solicitud(cargar_paquete("sol-001"), indicador_iva="C9")

    veredicto = rc6_indicador_iva(paquete, cargar_maestros())

    assert veredicto.tipo == "bloqueo"
    for valido in ("C0", "C1", "C2"):
        assert valido in veredicto.detalle
    assert veredicto.datos["origen"] == "solicitud"
    assert veredicto.datos["tasa"] is None


def test_condiciones_de_pago_inexistentes_bloquean() -> None:
    paquete = con_solicitud(cargar_paquete("sol-001"), condiciones_pago="Z999")

    veredicto = rc7_condiciones_pago(paquete, cargar_maestros())

    assert veredicto.tipo == "bloqueo"
    assert "Z999" in veredicto.detalle
    for valido in ("Z000", "Z015", "Z030", "Z060"):
        assert valido in veredicto.detalle


# ── Proveedor no resoluble: no_evaluable, no bloqueo duplicado ─────────────


def test_sin_proveedor_resoluble_y_sin_iva_es_no_evaluable() -> None:
    # RC1 ya bloquea por el proveedor; RC6 no repite la causa (ni lanza).
    paquete = con_solicitud(
        cargar_paquete("sol-001"),
        proveedor_nit="999999999",
        indicador_iva=None,
        condiciones_pago=None,
    )
    maestros = cargar_maestros()

    rc6 = rc6_indicador_iva(paquete, maestros)
    rc7 = rc7_condiciones_pago(paquete, maestros)

    assert rc6.tipo == "no_evaluable"
    assert rc6.datos["indicador_iva"] is None
    assert rc7.tipo == "no_evaluable"
    assert rc7.datos["condiciones_pago"] is None


# ── La tasa de C0 (plan §H6) ───────────────────────────────────────────────


def test_c0_pasa_y_su_tasa_se_lee_como_float_cero() -> None:
    # En indicadores-iva.json la tasa de C0 es el entero 0; Pydantic la coacciona
    # a 0.0. El campo es float: no se compara con `is` ni se asume int.
    paquete = con_solicitud(cargar_paquete("sol-001"), indicador_iva="C0")

    veredicto = rc6_indicador_iva(paquete, cargar_maestros())

    assert veredicto.tipo == "ok"
    assert veredicto.datos["tasa"] == 0.0
    assert isinstance(veredicto.datos["tasa"], float)


def test_c1_reporta_su_tasa_del_maestro() -> None:
    veredicto = rc6_indicador_iva(cargar_paquete("sol-001"), cargar_maestros())

    assert veredicto.tipo == "ok"
    assert veredicto.datos["indicador_iva"] == "C1"
    assert veredicto.datos["origen"] == "solicitud"
    assert veredicto.datos["tasa"] == 0.19


def test_rc7_informada_reporta_los_dias_del_maestro() -> None:
    # sol-004 trae Z000 (pago inmediato, 0 días).
    veredicto = rc7_condiciones_pago(cargar_paquete("sol-004"), cargar_maestros())

    assert veredicto.tipo == "ok"
    assert veredicto.datos["condiciones_pago"] == "Z000"
    assert veredicto.datos["dias"] == 0
