"""Tests de RC2 y RC3 (T1.2).

El test central es sol-003: RC2 bloquea por falta de autoridad y RC3 **no**
bloquea, devuelve `no_evaluable` (ADR-003). Un bloqueo doble diría al usuario
que hay dos problemas donde hay uno.

Los topes y los emails se leen de `centros-costo.json`, no se escriben a mano.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.controles.rc02_rc03 import rc2_aprobacion, rc3_tope
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

    `cotizacion` y `factura` quedan en None: RC2 y RC3 no los leen.
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


def con_aprobacion(paquete: Paquete, **cambios: Any) -> Paquete:
    """Copia del paquete con campos de la aprobación modificados."""
    assert paquete.aprobacion is not None
    aprobacion = paquete.aprobacion.model_copy(update=cambios)
    return paquete.model_copy(update={"aprobacion": aprobacion})


def tope_del_maestro(centro_costo: str, email: str) -> int:
    """Lee el tope de un aprobador desde `centros-costo.json`."""
    for centro in leer_json(MAESTROS / "centros-costo.json"):
        if centro["centro_costo"] != centro_costo:
            continue
        for aprobador in centro["aprobadores"]:
            if aprobador["email"] == email:
                tope: int = aprobador["tope"]
                return tope
    raise AssertionError(f"El fixture dejó de traer a {email} en {centro_costo}")


# ── Los 6 casos reales (oráculo §matriz) ───────────────────────────────────

ESPERADO_POR_CASO = {
    "sol-001": ("ok", "ok"),
    "sol-002": ("ok", "ok"),                 # RC1 bloquea, pero la autoridad está bien
    "sol-003": ("bloqueo", "no_evaluable"),  # ADR-003
    "sol-004": ("ok", "ok"),
    "sol-005": ("ok", "ok"),
    "sol-006": ("ok", "ok"),
}


@pytest.mark.parametrize("caso", CASOS)
def test_rc2_y_rc3_en_los_seis_casos(caso: str) -> None:
    maestros = cargar_maestros()
    paquete = cargar_paquete(caso)
    tipo_rc2, tipo_rc3 = ESPERADO_POR_CASO[caso]

    rc2 = rc2_aprobacion(paquete, maestros)
    rc3 = rc3_tope(paquete, maestros, rc2)

    assert rc2.tipo == tipo_rc2
    assert rc3.tipo == tipo_rc3


# ── sol-003: el test central de la tarea ───────────────────────────────────


def test_sol_003_bloquea_por_autoridad_y_rc3_no_duplica_el_bloqueo() -> None:
    maestros = cargar_maestros()
    paquete = cargar_paquete("sol-003")

    rc2 = rc2_aprobacion(paquete, maestros)
    rc3 = rc3_tope(paquete, maestros, rc2)

    assert rc2.tipo == "bloqueo"
    assert rc3.tipo == "no_evaluable"   # NO "bloqueo": ADR-003
    # El mensaje debe permitir corregirlo sin investigar: nombra a quien sí puede
    # aprobar en CC-2020.
    assert "rtorres@periferia-ficticia.com" in rc2.detalle
    assert "fvargas@periferia-ficticia.com" in rc2.detalle
    assert "CC-2020" in rc2.detalle
    assert rc2.datos["aprobadores_validos"] == "rtorres@periferia-ficticia.com"


def test_sol_003_con_el_centro_correcto_habria_pasado_el_tope() -> None:
    """El defecto de sol-003 es de autoridad, no de monto.

    Si el gasto se hubiera imputado a CC-3030, donde fvargas@ sí aprueba con
    tope de 80.000.000, entonces 74.000.000 ≤ 80.000.000 y pasaba. Este dato va
    al análisis de desvío de proceso de SOLUCION.md.
    """
    maestros = cargar_maestros()
    paquete = con_solicitud(
        cargar_paquete("sol-003"), centro_costo="CC-3030", subarea="Preventa"
    )
    tope = tope_del_maestro("CC-3030", "fvargas@periferia-ficticia.com")
    assert paquete.solicitud.valor_total <= tope

    rc2 = rc2_aprobacion(paquete, maestros)
    rc3 = rc3_tope(paquete, maestros, rc2)

    assert rc2.tipo == "ok"
    assert rc3.tipo == "ok"
    assert rc3.datos["tope"] == tope
    assert rc3.datos["exceso"] == 0


# ── Las otras causas de bloqueo de RC2 ─────────────────────────────────────


def test_sin_aprobacion_bloquea_diciendo_que_no_llego() -> None:
    paquete = cargar_paquete("sol-001").model_copy(update={"aprobacion": None})

    veredicto = rc2_aprobacion(paquete, cargar_maestros())

    assert veredicto.tipo == "bloqueo"
    assert "No llegó" in veredicto.detalle
    # Se distingue de "no dice Aprobado": son acciones distintas del usuario.
    assert "Aprobado" not in veredicto.detalle
    assert veredicto.datos["aprobador_email"] is None
    assert veredicto.datos["aprobado"] is False


def test_aprobacion_sin_la_palabra_aprobado_bloquea() -> None:
    paquete = con_aprobacion(
        cargar_paquete("sol-001"),
        aprobado=False,
        texto="Recibido, lo reviso con el equipo esta semana.",
    )

    veredicto = rc2_aprobacion(paquete, cargar_maestros())

    assert veredicto.tipo == "bloqueo"
    assert "no contiene una aprobación explícita" in veredicto.detalle
    assert veredicto.datos["aprobado"] is False


def test_centro_de_costo_inexistente_bloquea_rc2() -> None:
    paquete = con_solicitud(cargar_paquete("sol-001"), centro_costo="CC-9999")

    veredicto = rc2_aprobacion(paquete, cargar_maestros())

    assert veredicto.tipo == "bloqueo"
    assert "CC-9999" in veredicto.detalle
    assert veredicto.datos["aprobadores_validos"] == ""


@pytest.mark.parametrize(
    "descripcion",
    ["sin_aprobacion", "sin_la_palabra", "centro_inexistente", "sin_autoridad"],
)
def test_rc3_nunca_bloquea_si_rc2_fallo(descripcion: str) -> None:
    """Las cuatro causas de bloqueo de RC2 dan `no_evaluable` en RC3 (ADR-003)."""
    maestros = cargar_maestros()
    paquete = cargar_paquete("sol-001")

    if descripcion == "sin_aprobacion":
        paquete = paquete.model_copy(update={"aprobacion": None})
    elif descripcion == "sin_la_palabra":
        paquete = con_aprobacion(paquete, aprobado=False, texto="Lo reviso luego.")
    elif descripcion == "centro_inexistente":
        paquete = con_solicitud(paquete, centro_costo="CC-9999")
    else:
        # fvargas@ aprueba en CC-3030; la solicitud es de CC-1010.
        paquete = con_aprobacion(paquete, de="fvargas@periferia-ficticia.com")

    rc2 = rc2_aprobacion(paquete, maestros)
    rc3 = rc3_tope(paquete, maestros, rc2)

    assert rc2.tipo == "bloqueo"
    assert rc3.tipo == "no_evaluable"
    assert rc3.datos["tope"] is None


# ── RC3: el tope ───────────────────────────────────────────────────────────


def test_exceso_de_tope_bloquea_con_los_dos_montos_y_el_exceso() -> None:
    # sol-005 es CC-2020, aprueba rtorres@ con tope 30.000.000.
    maestros = cargar_maestros()
    tope = tope_del_maestro("CC-2020", "rtorres@periferia-ficticia.com")
    assert tope == 30_000_000
    paquete = con_solicitud(cargar_paquete("sol-005"), valor_total=45_000_000)

    rc2 = rc2_aprobacion(paquete, maestros)
    rc3 = rc3_tope(paquete, maestros, rc2)

    assert rc2.tipo == "ok"
    assert rc3.tipo == "bloqueo"
    assert rc3.datos["exceso"] == 15_000_000
    # Los tres montos, con separador de miles, para que se lean de un vistazo.
    assert "45.000.000" in rc3.detalle
    assert "30.000.000" in rc3.detalle
    assert "15.000.000" in rc3.detalle


def test_valor_igual_al_tope_pasa() -> None:
    # PRD §7.3 RC3 dice `≤`, no `<`.
    maestros = cargar_maestros()
    tope = tope_del_maestro("CC-2020", "rtorres@periferia-ficticia.com")
    paquete = con_solicitud(cargar_paquete("sol-005"), valor_total=tope)

    rc3 = rc3_tope(paquete, maestros, rc2_aprobacion(paquete, maestros))

    assert rc3.tipo == "ok"
    assert rc3.datos["exceso"] == 0


def test_un_peso_por_encima_del_tope_bloquea() -> None:
    maestros = cargar_maestros()
    tope = tope_del_maestro("CC-2020", "rtorres@periferia-ficticia.com")
    paquete = con_solicitud(cargar_paquete("sol-005"), valor_total=tope + 1)

    rc3 = rc3_tope(paquete, maestros, rc2_aprobacion(paquete, maestros))

    assert rc3.tipo == "bloqueo"
    assert rc3.datos["exceso"] == 1


def test_rc3_reporta_el_tope_del_aprobador_que_firmo_no_el_mayor_del_centro() -> None:
    # CC-1010 tiene dos aprobadores con topes distintos (50M y 200M). sol-001 la
    # firma mlopez@, así que el tope aplicable es el suyo.
    maestros = cargar_maestros()
    paquete = cargar_paquete("sol-001")

    rc3 = rc3_tope(paquete, maestros, rc2_aprobacion(paquete, maestros))

    assert rc3.datos["aprobador_email"] == "mlopez@periferia-ficticia.com"
    assert rc3.datos["tope"] == tope_del_maestro("CC-1010", "mlopez@periferia-ficticia.com")
