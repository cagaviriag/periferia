"""Tests de T1.3 · RC10 (aritmética) y RC5 (cotización vs solicitud).

Los seis casos se arman desde los **fixtures reales**: ningún monto esperado se
escribe a mano, se lee del archivo. Lo único que viene del `specs/01-oracle.md` es
el *tipo* de veredicto de cada caso, que es justamente lo que se está verificando.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.controles.rc05_rc10 import rc5_cotizacion, rc10_aritmetica
from app.core.modelos import Correo, Cotizacion, Paquete, Solicitud

RAIZ = Path(__file__).resolve().parent.parent
SOLICITUDES = RAIZ / "fixtures" / "reto-03" / "solicitudes"

CASOS = ["sol-001", "sol-002", "sol-003", "sol-004", "sol-005", "sol-006"]

# Oráculo §matriz: solo sol-004 confirma por desviación (6 % > 2 %).
RC5_ESPERADO = {
    "sol-001": "ok",
    "sol-002": "ok",
    "sol-003": "ok",
    "sol-004": "confirmacion",
    "sol-005": "ok",
    "sol-006": "ok",
}


# ── Armado del paquete desde los fixtures ──────────────────────────────────
#
# Andamiaje de test, no el parser de producción: el parser real de `cotizacion.txt`
# es de Fase 2 (T2.3) y T1.3 declara explícitamente que estos controles reciben el
# `Paquete` ya armado. Aquí se extrae solo lo que RC5 y RC10 necesitan.


def _leer_json(ruta: Path) -> dict[str, Any]:
    crudo = json.loads(ruta.read_text(encoding="utf-8"))
    assert isinstance(crudo, dict)
    return crudo


def _monto(texto: str, etiqueta: str) -> int:
    """Lee un monto 'COP 11.400.000' que sigue a una etiqueta del fixture."""
    encontrado = re.search(rf"{etiqueta}.*?COP ([\d.]+)", texto)
    assert encontrado is not None, f"el fixture no trae la etiqueta {etiqueta!r}"
    return int(encontrado.group(1).replace(".", ""))


def construir_paquete(caso: str) -> Paquete:
    """Arma el `Paquete` de un caso leyendo sus fixtures. Solo lectura (C1)."""
    carpeta = SOLICITUDES / caso
    texto_cotizacion = (carpeta / "cotizacion.txt").read_text(encoding="utf-8")

    return Paquete(
        caso=caso,
        correo=Correo.model_validate(_leer_json(carpeta / "correo.json")),
        solicitud=Solicitud.model_validate(_leer_json(carpeta / "solicitud.json")),
        cotizacion=Cotizacion(
            referencia=None,
            proveedor=_leer_json(carpeta / "solicitud.json")["proveedor_nombre"],
            nit=None,
            total=_monto(texto_cotizacion, "TOTAL \\(IVA incluido\\):"),
            moneda="COP",
            validez_hasta=None,
            item=None,
            texto=texto_cotizacion,
        ),
        # RC5 y RC10 no miran la aprobación ni la factura (T1.3 §Firmas: estos dos
        # controles solo miran el paquete, y de él solo solicitud y cotización).
        aprobacion=None,
        factura=None,
    )


def con_solicitud(caso: str, **cambios: object) -> Paquete:
    """Variante de un caso real con algunos campos de la solicitud alterados."""
    paquete = construir_paquete(caso)
    return paquete.model_copy(update={"solicitud": paquete.solicitud.model_copy(update=cambios)})


def con_total_cotizado(paquete: Paquete, total: int) -> Paquete:
    """Variante del paquete con otro total en la cotización."""
    assert paquete.cotizacion is not None
    return paquete.model_copy(update={"cotizacion": paquete.cotizacion.model_copy(
        update={"total": total}
    )})


# ── RC10 · los seis casos cuadran ──────────────────────────────────────────


@pytest.mark.parametrize("caso", CASOS)
def test_rc10_ok_en_los_seis_casos(caso: str) -> None:
    paquete = construir_paquete(caso)

    veredicto = rc10_aritmetica(paquete)

    assert veredicto.codigo == "RC10"
    assert veredicto.tipo == "ok"
    assert veredicto.datos["esperado"] == veredicto.datos["valor_total"]
    assert veredicto.datos["diferencia"] == 0


def test_rc10_detalle_muestra_la_multiplicacion_de_sol_001() -> None:
    # El fixture dice 120 × 95.000 = 11.400.000; el detalle debe exhibirla.
    veredicto = rc10_aritmetica(construir_paquete("sol-001"))

    assert "120 × 95.000 = 11.400.000" in veredicto.detalle


# ── RC5 · los seis casos, y sol-004 como caso central ──────────────────────


@pytest.mark.parametrize("caso", CASOS)
def test_rc5_coincide_con_el_oraculo(caso: str) -> None:
    veredicto = rc5_cotizacion(construir_paquete(caso))

    assert veredicto.codigo == "RC5"
    assert veredicto.tipo == RC5_ESPERADO[caso]
    assert veredicto.datos["umbral_pct"] == 2.0


@pytest.mark.parametrize("caso", [c for c in CASOS if RC5_ESPERADO[c] == "ok"])
def test_rc5_los_casos_ok_tienen_desviacion_cero(caso: str) -> None:
    veredicto = rc5_cotizacion(construir_paquete(caso))

    assert veredicto.datos["desviacion_pct"] == 0.0


def test_sol_004_confirma_por_desviacion_del_6_por_ciento() -> None:
    """Caso central de T1.3 (oráculo §sol-004 · PRD §11)."""
    paquete = construir_paquete("sol-004")
    # Los dos montos salen del fixture, no de la memoria.
    assert paquete.solicitud.valor_total == 25_000_000
    assert paquete.cotizacion is not None
    assert paquete.cotizacion.total == 26_500_000

    rc5 = rc5_cotizacion(paquete)
    rc10 = rc10_aritmetica(paquete)

    assert rc5.tipo == "confirmacion"
    assert rc5.datos["desviacion_pct"] == 6.00
    assert rc5.datos["valor_solicitud"] == 25_000_000
    assert rc5.datos["valor_cotizacion"] == 26_500_000
    # PRD §11: la UI muestra los dos valores y cuál se usará si se confirma.
    assert "25.000.000" in rc5.detalle
    assert "26.500.000" in rc5.detalle
    assert "6.00 %" in rc5.detalle
    assert "valor de la solicitud" in rc5.detalle
    # La solicitud cuadra consigo misma: el desacuerdo es con el proveedor.
    assert rc10.tipo == "ok"


# ── RC5 · bordes ───────────────────────────────────────────────────────────


def test_rc5_sin_cotizacion_confirma_y_nunca_bloquea() -> None:
    paquete = construir_paquete("sol-001").model_copy(update={"cotizacion": None})

    veredicto = rc5_cotizacion(paquete)

    assert veredicto.tipo == "confirmacion"
    assert veredicto.datos["valor_cotizacion"] is None
    assert veredicto.datos["desviacion_pct"] is None
    assert "no hay cotización" in veredicto.detalle.lower()


def test_rc5_el_limite_exacto_del_2_por_ciento_es_ok() -> None:
    # El PRD dice "≤ 2 %": el límite exacto pasa.
    base = con_solicitud("sol-001", valor_total=100_000_000)

    veredicto = rc5_cotizacion(con_total_cotizado(base, 102_000_000))

    assert veredicto.tipo == "ok"
    assert veredicto.datos["desviacion_pct"] == 2.0


def test_rc5_un_peso_sobre_el_limite_confirma() -> None:
    base = con_solicitud("sol-001", valor_total=100_000_000)

    veredicto = rc5_cotizacion(con_total_cotizado(base, 102_000_001))

    assert veredicto.tipo == "confirmacion"


def test_rc5_con_valor_total_en_cero_confirma_sin_dividir_por_cero() -> None:
    # Si hubiera una división sin guarda, esta llamada lanzaría ZeroDivisionError
    # y el test fallaría con error, no con aserción.
    paquete = con_solicitud("sol-001", valor_total=0)

    veredicto = rc5_cotizacion(paquete)

    assert veredicto.tipo == "confirmacion"
    assert veredicto.datos["valor_solicitud"] == 0
    assert veredicto.datos["desviacion_pct"] is None
    assert "cero" in veredicto.detalle


def test_rc5_sin_cotizacion_y_con_valor_cero_tampoco_lanza() -> None:
    paquete = con_solicitud("sol-001", valor_total=0).model_copy(update={"cotizacion": None})

    assert rc5_cotizacion(paquete).tipo == "confirmacion"


# ── RC10 · bordes ──────────────────────────────────────────────────────────


def test_rc10_bloquea_con_desajuste_y_muestra_la_multiplicacion() -> None:
    paquete = con_solicitud("sol-001", cantidad=10, valor_unitario=100, valor_total=5_000)

    veredicto = rc10_aritmetica(paquete)

    assert veredicto.tipo == "bloqueo"
    assert "10 × 100 = 1.000" in veredicto.detalle
    assert veredicto.datos["esperado"] == 1_000
    assert veredicto.datos["diferencia"] == 4_000


def test_rc10_tolera_un_peso_de_redondeo() -> None:
    base = construir_paquete("sol-001").solicitud
    esperado = base.cantidad * base.valor_unitario

    veredicto = rc10_aritmetica(con_solicitud("sol-001", valor_total=esperado + 1))

    assert veredicto.tipo == "ok"
    assert veredicto.datos["diferencia"] == 1


def test_rc10_bloquea_con_dos_pesos_de_diferencia() -> None:
    base = construir_paquete("sol-001").solicitud
    esperado = base.cantidad * base.valor_unitario

    veredicto = rc10_aritmetica(con_solicitud("sol-001", valor_total=esperado + 2))

    assert veredicto.tipo == "bloqueo"
    assert veredicto.datos["diferencia"] == 2


def test_rc10_tolera_un_peso_por_debajo() -> None:
    base = construir_paquete("sol-001").solicitud
    esperado = base.cantidad * base.valor_unitario

    assert rc10_aritmetica(con_solicitud("sol-001", valor_total=esperado - 1)).tipo == "ok"


def test_rc10_bloquea_con_cantidad_en_cero() -> None:
    # Con cantidad 0 y total 0 la multiplicación "cuadra", pero no hay compra.
    paquete = con_solicitud("sol-001", cantidad=0, valor_total=0)

    veredicto = rc10_aritmetica(paquete)

    assert veredicto.tipo == "bloqueo"
    assert "cantidad en cero" in veredicto.detalle
    assert veredicto.datos["cantidad"] == 0


# ── Pureza: los controles no lanzan y no mutan el paquete ──────────────────


@pytest.mark.parametrize("caso", CASOS)
def test_los_controles_no_mutan_el_paquete(caso: str) -> None:
    paquete = construir_paquete(caso)
    antes = paquete.model_dump_json()

    rc10_aritmetica(paquete)
    rc5_cotizacion(paquete)

    assert paquete.model_dump_json() == antes
