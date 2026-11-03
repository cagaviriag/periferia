"""Tests de T0.4 · normalizadores.

Los casos salen de los fixtures reales. Donde el valor esperado depende del
maestro (cruce de `sol-006`) se **lee del fixture**, no se escribe a mano:
si el maestro cambia, el test sigue diciendo la verdad.

`truncar_descripcion` se verifica por propiedades, no por literales (ADR-004).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.normalizar import (
    inferir_unidad,
    normalizar_nit,
    normalizar_nombre,
    truncar_descripcion,
)

RAIZ = Path(__file__).resolve().parents[1]
FIXTURES = RAIZ / "fixtures" / "reto-03"
SOLICITUDES = ("sol-001", "sol-002", "sol-003", "sol-004", "sol-005", "sol-006")

LIMITE_SAP = 40


# --------------------------------------------------------------------------- #
# Lectores de fixtures (solo para el test; las funciones bajo prueba no leen)  #
# --------------------------------------------------------------------------- #


def _leer_proveedores() -> list[dict[str, object]]:
    texto = (FIXTURES / "maestros" / "proveedores.json").read_text(encoding="utf-8")
    proveedores: list[dict[str, object]] = json.loads(texto)
    return proveedores


def _nombre_maestro(codigo_sap: str) -> str:
    for proveedor in _leer_proveedores():
        if proveedor["codigo_sap"] == codigo_sap:
            return str(proveedor["nombre"])
    raise AssertionError(f"el maestro no tiene al proveedor {codigo_sap}")


def _leer_solicitud(caso: str) -> dict[str, object]:
    texto = (FIXTURES / "solicitudes" / caso / "solicitud.json").read_text(encoding="utf-8")
    solicitud: dict[str, object] = json.loads(texto)
    return solicitud


def _descripcion_solicitud(caso: str) -> str:
    return str(_leer_solicitud(caso)["descripcion"])


def _proveedor_nombre_solicitud(caso: str) -> str:
    return str(_leer_solicitud(caso)["proveedor_nombre"])


def _proveedor_nit_solicitud(caso: str) -> str | None:
    nit = _leer_solicitud(caso).get("proveedor_nit")
    if nit is None:
        return None
    return str(nit)


def _item_cotizacion(caso: str) -> str:
    """Nombre del primer ítem de la cotización: el texto antes del primer '|'."""
    texto = (FIXTURES / "solicitudes" / caso / "cotizacion.txt").read_text(encoding="utf-8")
    for linea in texto.splitlines():
        if linea.startswith("1."):
            return linea.split("|")[0].removeprefix("1.").strip()
    raise AssertionError(f"la cotización de {caso} no tiene un ítem numerado")


# --------------------------------------------------------------------------- #
# normalizar_nit                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("900.555.111-2", "900555111"),  # cotización sol-001 vs maestro
        ("900555111", "900555111"),  # solicitud sol-001
        ("830.111.222-8", "830111222"),  # cotización sol-003
        ("901.999.000-5", "901999000"),  # cotización sol-002 (no existe en maestro)
        (None, None),  # sol-006 no trae proveedor_nit
        ("", None),
        ("  900 555 111  ", "900555111"),
        ("-", None),
    ],
)
def test_normalizar_nit(entrada: str | None, esperado: str | None) -> None:
    assert normalizar_nit(entrada) == esperado


def test_nit_de_cotizacion_coincide_con_el_de_la_solicitud() -> None:
    """ADR-008: sin normalizar, RC1 no cruzaría el NIT con guion contra el maestro."""
    assert normalizar_nit("900.555.111-2") == normalizar_nit("900555111")


# --------------------------------------------------------------------------- #
# normalizar_nombre                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("TecnoSuministros S.A.S.", "tecnosuministros"),  # sol-006 cruza por nombre
        ("Mobiliario Andino S.A.", "mobiliario andino"),
        ("Papelería Central Ltda.", "papeleria central"),  # acentos + sufijo
        ("Cloud Andina S.A.S.", "cloud andina"),
        (None, None),
        ("", None),
        ("   S.A.S.   ", None),  # borde: solo sufijo, queda vacío
    ],
)
def test_normalizar_nombre(entrada: str | None, esperado: str | None) -> None:
    assert normalizar_nombre(entrada) == esperado


def test_nombre_de_sol_006_cruza_contra_el_maestro() -> None:
    """Aserción clave: sol-006 no trae NIT, así que RC1 cruza por nombre.

    El nombre esperado se lee de `proveedores.json` (codigo_sap=100234). Si
    estos dos valores difieren, RC1 falla en sol-006 en la Fase 1.
    """
    nombre_maestro = normalizar_nombre(_nombre_maestro("100234"))

    assert nombre_maestro is not None
    assert normalizar_nombre("TecnoSuministros S.A.S.") == nombre_maestro
    # Y con el nombre tal como viene en la solicitud sol-006, leído del fixture.
    assert normalizar_nombre(_proveedor_nombre_solicitud("sol-006")) == nombre_maestro
    assert _proveedor_nit_solicitud("sol-006") is None


# --------------------------------------------------------------------------- #
# truncar_descripcion — propiedades, no literales (ADR-004)                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("caso", SOLICITUDES)
def test_truncar_descripcion_cumple_las_propiedades(caso: str) -> None:
    original = _descripcion_solicitud(caso)
    resultado = truncar_descripcion(original)

    assert len(resultado) <= LIMITE_SAP
    assert not resultado.endswith(" ")

    if len(original) <= LIMITE_SAP:
        assert resultado == original
        return

    # No parte palabras: es prefijo del original y se cortó en una frontera.
    assert original.startswith(resultado)
    assert original[len(resultado)] in " ,;:.-"


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("", ""),
        ("A" * 50, "A" * 40),  # la primera palabra excede: corte en seco
    ],
)
def test_truncar_descripcion_bordes(entrada: str, esperado: str) -> None:
    assert truncar_descripcion(entrada) == esperado


@pytest.mark.parametrize(
    "entrada",
    [
        "Papelería y tóner  ",   # cabe en 40: antes salía con los dos espacios
        " ",                     # solo espacio: antes se devolvía tal cual
        "Papelería y tóner\t",
    ],
)
def test_truncar_descripcion_no_termina_en_espacio_en_el_camino_rapido(
    entrada: str,
) -> None:
    # D5: el invariante 6 de T0.4 ("nunca termina en espacio") también rige
    # cuando la descripción ya cabe en el límite. El camino rápido devolvía el
    # texto intacto y lo violaba.
    resultado = truncar_descripcion(entrada)

    assert len(entrada) <= LIMITE_SAP          # el test ejercita el camino rápido
    assert len(resultado) <= LIMITE_SAP
    assert not resultado.endswith(" ")
    assert resultado == entrada.rstrip()


def test_truncar_descripcion_quita_puntuacion_final() -> None:
    resultado = truncar_descripcion("Mobiliario para la sede nueva, 40 puestos de trabajo")
    assert not resultado.endswith(",")
    assert not resultado.endswith(" ")
    assert len(resultado) <= LIMITE_SAP


# --------------------------------------------------------------------------- #
# inferir_unidad                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("item", "descripcion", "esperado"),
    [
        # El ítem **es** una hora -> H (sol-004).
        ("Hora de arquitecto de nube senior", "Bolsa de 100 horas de arquitectura", "H"),
        # El ítem **es** una licencia: 120 licencias, no 120 meses (sol-001).
        (
            "Licencia antivirus corporativo 12 meses",
            "Renovación licencias antivirus corporativo 120 puestos, vigencia 12 meses",
            "UN",
        ),
        ("Puesto de trabajo 1.40 m con silla ergonómica", "Mobiliario para nueva sede", "UN"),
        ("Diadema USB con micrófono y cancelación de ruido", "Diademas con micrófono", "UN"),
        (None, "Bolsa de 100 horas de arquitectura", "H"),  # fallback a descripción
        (None, "Papelería y tóner", "UN"),  # default seguro
        ("", "Bolsa de 100 horas de arquitectura", "H"),  # ítem vacío = sin ítem
    ],
)
def test_inferir_unidad(item: str | None, descripcion: str, esperado: str) -> None:
    assert inferir_unidad(item, descripcion) == esperado


def test_inferir_unidad_sol_001_no_se_engana_con_meses() -> None:
    """El caso que decide ADR-005, con el ítem leído del fixture real.

    La descripción dice "vigencia 12 meses" pero la cantidad son 120 licencias:
    la unidad es UN. Una búsqueda ingenua de "meses" daría MES.
    """
    item = _item_cotizacion("sol-001")
    descripcion = _descripcion_solicitud("sol-001")

    assert "meses" in descripcion
    assert inferir_unidad(item, descripcion) == "UN"


def test_inferir_unidad_sol_004_es_hora() -> None:
    """El ítem real de sol-004 **es** una hora de servicio."""
    item = _item_cotizacion("sol-004")
    descripcion = _descripcion_solicitud("sol-004")

    assert inferir_unidad(item, descripcion) == "H"


@pytest.mark.parametrize("caso", SOLICITUDES)
def test_inferir_unidad_siempre_devuelve_un_valor_valido(caso: str) -> None:
    """El payload exige `unidad ∈ {UN, H, MES}` (PRD §7.4)."""
    unidad = inferir_unidad(_item_cotizacion(caso), _descripcion_solicitud(caso))
    assert unidad in {"UN", "H", "MES"}
