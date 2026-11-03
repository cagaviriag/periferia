"""Gate de la Fase 1: la matriz del oráculo, celda por celda.

Reproduce `specs/01-oracle.md` §matriz para los 6 casos reales. Si una celda
falla, el control correspondiente está mal — nunca se ajusta este test para
que pase.

Se afirman las listas de códigos **exactas**, no sus longitudes: un bloqueo
extra pasaría desapercibido en una aserción por longitud.
"""

import pytest

from app.controles.evaluar import evaluar_controles
from tests.conftest import CASOS, cargar_maestros, construir_paquete

# specs/01-oracle.md §matriz · (apta, bloqueos, confirmaciones, derivados, retroactiva)
ORACULO: dict[str, tuple[bool, list[str], list[str], set[str], bool]] = {
    "sol-001": (True, [], [], set(), False),
    "sol-002": (False, ["RC1"], [], set(), False),
    "sol-003": (False, ["RC2"], [], set(), False),
    "sol-004": (True, [], ["RC5"], set(), False),
    "sol-005": (True, [], ["RC8"], set(), True),
    "sol-006": (True, [], ["RC6"], {"indicador_iva", "condiciones_pago"}, False),
}


@pytest.mark.parametrize("caso", CASOS)
def test_matriz_del_oraculo(caso: str) -> None:
    apta, bloqueos, confirmaciones, derivados, retroactiva = ORACULO[caso]

    resultado = evaluar_controles(construir_paquete(caso), cargar_maestros())

    assert resultado.apta is apta, f"{caso}: apta"
    assert [v.codigo for v in resultado.bloqueos] == bloqueos, f"{caso}: bloqueos"
    assert [v.codigo for v in resultado.confirmaciones] == confirmaciones, (
        f"{caso}: confirmaciones"
    )
    assert set(resultado.derivados) == derivados, f"{caso}: derivados"
    assert resultado.retroactiva is retroactiva, f"{caso}: retroactiva"


@pytest.mark.parametrize("caso", CASOS)
def test_siempre_se_evaluan_los_diez_controles(caso: str) -> None:
    # Sin corto-circuito: la analista debe ver todo lo que está mal en una
    # sola pasada. sol-002 y sol-003 bloquean temprano y aun así van los 10.
    resultado = evaluar_controles(construir_paquete(caso), cargar_maestros())

    assert len(resultado.veredictos) == 10
    assert [v.codigo for v in resultado.veredictos] == [
        "RC1", "RC4", "RC2", "RC3", "RC10", "RC5", "RC6", "RC7", "RC8", "RC9",
    ]


def test_sol_003_rc3_no_evaluable_no_entra_en_bloqueos() -> None:
    # ADR-003: sin aprobador válido para el centro no hay tope aplicable.
    # RC3 queda en la traza pero no duplica la causa raíz que ya reportó RC2.
    resultado = evaluar_controles(construir_paquete("sol-003"), cargar_maestros())

    rc3 = next(v for v in resultado.veredictos if v.codigo == "RC3")
    assert rc3.tipo == "no_evaluable"
    assert "RC3" not in [v.codigo for v in resultado.bloqueos]
    assert "RC3" not in [v.codigo for v in resultado.confirmaciones]


def test_sol_006_derivados_se_recogen_por_origen_no_por_tipo() -> None:
    # RC6 derivado es tipo `confirmacion` (impacto fiscal) y RC7 es tipo
    # `derivado`. Filtrar por tipo perdería el IVA. Ver evaluar.py.
    resultado = evaluar_controles(construir_paquete("sol-006"), cargar_maestros())

    rc6 = next(v for v in resultado.veredictos if v.codigo == "RC6")
    rc7 = next(v for v in resultado.veredictos if v.codigo == "RC7")
    assert rc6.tipo == "confirmacion"
    assert rc7.tipo == "derivado"
    assert resultado.derivados == {"indicador_iva": "C1", "condiciones_pago": "Z030"}


def test_un_bloqueo_no_suprime_las_confirmaciones() -> None:
    # sol-002 bloquea por RC1 pero su cotización cuadra; si le rompemos el
    # monto, deben reportarse ambas cosas a la vez.
    paquete = construir_paquete("sol-002")
    paquete.solicitud.valor_total = paquete.solicitud.cantidad * paquete.solicitud.valor_unitario

    resultado = evaluar_controles(paquete, cargar_maestros())

    assert resultado.apta is False
    assert "RC1" in [v.codigo for v in resultado.bloqueos]


def test_apta_ignora_las_confirmaciones() -> None:
    # sol-004 tiene una confirmación (RC5, desviación del 6 %) y sigue apta:
    # las confirmaciones permiten crear con `confirmado=True`, no impiden.
    resultado = evaluar_controles(construir_paquete("sol-004"), cargar_maestros())

    assert resultado.apta is True
    assert len(resultado.confirmaciones) == 1


@pytest.mark.parametrize("caso", CASOS)
def test_evaluar_controles_es_determinista(caso: str) -> None:
    maestros = cargar_maestros()

    primera = evaluar_controles(construir_paquete(caso), maestros)
    segunda = evaluar_controles(construir_paquete(caso), maestros)

    assert primera.model_dump() == segunda.model_dump()
