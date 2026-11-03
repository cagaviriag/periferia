"""Tests de los resolutores y de RC1/RC4 (T1.1).

Todo se verifica contra los **fixtures reales**: los 4 maestros y las 6
solicitudes. No se inventan maestros, y los valores esperados que importan
(`codigo_sap`, topes) se leen del archivo, no se escriben a mano.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.controles.rc01_rc04 import rc1_proveedor, rc4_subarea
from app.controles.resolutores import resolver_aprobador, resolver_centro, resolver_proveedor
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

    `cotizacion` y `factura` quedan en None a propósito: RC1 y RC4 no los leen
    (son insumo de RC5, RC8 y RC10). El parser real de esos adjuntos es de la
    Fase 2; aquí no se simula lo que el control no usa.
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
    """Devuelve una copia del paquete con los campos indicados de la solicitud
    modificados. Es la forma de probar un campo sin tocar el fixture."""
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
    "sol-001": ("ok", "ok"),
    "sol-002": ("bloqueo", "ok"),   # NIT 901999000 no existe en el maestro
    "sol-003": ("ok", "ok"),
    "sol-004": ("ok", "ok"),
    "sol-005": ("ok", "ok"),
    "sol-006": ("ok", "ok"),        # sin NIT: cruza por nombre
}


@pytest.mark.parametrize("caso", CASOS)
def test_rc1_y_rc4_en_los_seis_casos(caso: str) -> None:
    maestros = cargar_maestros()
    paquete = cargar_paquete(caso)
    tipo_rc1, tipo_rc4 = ESPERADO_POR_CASO[caso]

    assert rc1_proveedor(paquete, maestros).tipo == tipo_rc1
    assert rc4_subarea(paquete, maestros).tipo == tipo_rc4


def test_sol_002_bloquea_por_proveedor_inexistente() -> None:
    # Oráculo §sol-002: único bloqueo del caso, y la acción sugerida es crear el
    # proveedor en SAP.
    veredicto = rc1_proveedor(cargar_paquete("sol-002"), cargar_maestros())

    assert veredicto.tipo == "bloqueo"
    assert veredicto.datos["cruzado_por"] == "nit"
    assert veredicto.datos["nit_buscado"] == "901999000"
    assert veredicto.datos["codigo_sap"] is None
    assert "no existe" in veredicto.detalle
    assert "SAP" in veredicto.detalle


# ── sol-006: el cruce por nombre ───────────────────────────────────────────


def test_sol_006_cruza_el_proveedor_por_nombre() -> None:
    # Oráculo §sol-006: la solicitud no trae NIT; el proveedor se encuentra por
    # nombre normalizado y es el código SAP 100234.
    paquete = cargar_paquete("sol-006")
    assert paquete.solicitud.proveedor_nit is None

    proveedor = resolver_proveedor(paquete, cargar_maestros())

    assert proveedor is not None
    assert proveedor.codigo_sap == "100234"
    assert proveedor.nit == proveedor_del_maestro("100234")["nit"]


def test_sol_006_el_veredicto_informa_que_se_cruzo_por_nombre() -> None:
    veredicto = rc1_proveedor(cargar_paquete("sol-006"), cargar_maestros())

    assert veredicto.tipo == "ok"
    assert veredicto.datos["cruzado_por"] == "nombre"
    assert veredicto.datos["nit_buscado"] is None
    assert veredicto.datos["codigo_sap"] == "100234"


# ── Casos de borde de RC1 ──────────────────────────────────────────────────


def test_proveedor_inactivo_bloquea_y_lo_dice_sin_confundirlo_con_inexistente() -> None:
    # Oráculo §casos de borde: 901777888 (Consultores Ágiles) está activo=false.
    inactivo = proveedor_del_maestro("100402")
    assert inactivo["activo"] is False, "el fixture dejó de traer un proveedor inactivo"

    paquete = con_solicitud(
        cargar_paquete("sol-001"),
        proveedor_nit=inactivo["nit"],
        proveedor_nombre=inactivo["nombre"],
    )

    veredicto = rc1_proveedor(paquete, cargar_maestros())

    assert veredicto.tipo == "bloqueo"
    assert veredicto.datos["activo"] is False
    assert veredicto.datos["codigo_sap"] == "100402"
    # Son dos fallos distintos y el usuario debe poder distinguirlos.
    assert "inactivo" in veredicto.detalle
    assert "no existe" not in veredicto.detalle


def test_nit_inexistente_no_cae_al_cruce_por_nombre() -> None:
    # El nombre SÍ está en el maestro, pero el NIT no: adivinar por nombre
    # ocultaría el error (decisión de diseño de T1.1).
    paquete = con_solicitud(
        cargar_paquete("sol-001"),
        proveedor_nit="999999999",
        proveedor_nombre="TecnoSuministros S.A.S.",
    )
    maestros = cargar_maestros()

    assert resolver_proveedor(paquete, maestros) is None
    assert rc1_proveedor(paquete, maestros).tipo == "bloqueo"


def test_nit_con_puntos_y_digito_de_verificacion_cruza_igual() -> None:
    # El NIT llega en tres formatos distintos en los fixtures (ADR-008).
    paquete = con_solicitud(cargar_paquete("sol-001"), proveedor_nit="900.555.111-2")

    veredicto = rc1_proveedor(paquete, cargar_maestros())

    assert veredicto.tipo == "ok"
    assert veredicto.datos["codigo_sap"] == "100234"


# ── Casos de borde de RC4 ──────────────────────────────────────────────────


def test_subarea_de_otro_centro_bloquea_y_lista_las_validas() -> None:
    # "Compras" pertenece a CC-2020, no a CC-1010.
    paquete = con_solicitud(cargar_paquete("sol-001"), centro_costo="CC-1010", subarea="Compras")

    veredicto = rc4_subarea(paquete, cargar_maestros())

    assert veredicto.tipo == "bloqueo"
    assert veredicto.datos["subareas_validas"] == "Infraestructura;Soporte;Desarrollo"
    for valida in ("Infraestructura", "Soporte", "Desarrollo"):
        assert valida in veredicto.detalle


def test_centro_de_costo_inexistente_bloquea() -> None:
    paquete = con_solicitud(cargar_paquete("sol-001"), centro_costo="CC-9999")
    maestros = cargar_maestros()

    assert resolver_centro(paquete, maestros) is None

    veredicto = rc4_subarea(paquete, maestros)
    assert veredicto.tipo == "bloqueo"
    assert "CC-9999" in veredicto.detalle
    assert "no existe" in veredicto.detalle


# ── resolver_aprobador: anticipa RC2 (T1.2) ────────────────────────────────


def test_resolver_aprobador_sol_003_es_none_por_centro_equivocado() -> None:
    # Oráculo §sol-003: fvargas@ aprueba en CC-3030 y la solicitud es de CC-2020.
    paquete = cargar_paquete("sol-003")
    assert paquete.aprobacion is not None
    assert paquete.aprobacion.de == "fvargas@periferia-ficticia.com"
    assert paquete.solicitud.centro_costo == "CC-2020"

    assert resolver_aprobador(paquete, cargar_maestros()) is None


def test_resolver_aprobador_sol_001_devuelve_mlopez_con_su_tope() -> None:
    aprobador = resolver_aprobador(cargar_paquete("sol-001"), cargar_maestros())

    assert aprobador is not None
    assert aprobador.email == "mlopez@periferia-ficticia.com"
    assert aprobador.tope == 50_000_000


def test_resolver_aprobador_sin_aprobacion_es_none() -> None:
    paquete = cargar_paquete("sol-001").model_copy(update={"aprobacion": None})

    assert resolver_aprobador(paquete, cargar_maestros()) is None


def test_resolver_aprobador_ignora_mayusculas_y_espacios_del_correo() -> None:
    paquete = cargar_paquete("sol-001")
    assert paquete.aprobacion is not None
    aprobacion = paquete.aprobacion.model_copy(update={"de": "  MLopez@Periferia-Ficticia.com "})
    paquete = paquete.model_copy(update={"aprobacion": aprobacion})

    aprobador = resolver_aprobador(paquete, cargar_maestros())

    assert aprobador is not None
    assert aprobador.email == "mlopez@periferia-ficticia.com"
