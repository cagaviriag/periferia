"""Tests del adaptador SAP simulado (T2.1).

Dos decisiones de montaje que se explican una sola vez:

- `raiz` es siempre `tmp_path`. Cada test corre sobre un `out/` vacío, así que la
  numeración empieza de verdad en `4500000001` (I2) y ningún test contamina a
  otro.
- Los maestros son los **reales** de `fixtures/reto-03/maestros/`, leídos en solo
  lectura (C1). El cruce de NIT con puntos y dígito de verificación solo se
  prueba de verdad contra los datos reales.

Los tests `async` se corren con `asyncio.run(...)`: `pytest-asyncio` no está en
las dependencias de `pyproject.toml` y esta tarea no las amplía.
"""

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.core.io import ruta_salida
from app.core.modelos import (
    AprobadorOC,
    Maestros,
    OrdenCompra,
    PosicionOC,
    ProveedorOC,
    ReferenciaOC,
)
from app.sap.adapter import SapAdapter
from app.sap.mock import NUMERO_INICIAL, SapMock

RAIZ = Path(__file__).resolve().parent.parent
MAESTROS = RAIZ / "fixtures" / "reto-03" / "maestros"


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


def orden_de(solicitud_id: str) -> OrdenCompra:
    """Una OC válida mínima, con el `solicitud_id` que interesa al test.

    Los valores son los del caso sol-001 (TecnoSuministros): lo único que varía
    entre tests es la referencia, que es la clave de la idempotencia (C10).
    """
    return OrdenCompra(
        referencia=ReferenciaOC(
            solicitud_id=solicitud_id,
            correo_id="cor-001",
            cotizacion_ref="COT-TS-2026-0451",
        ),
        proveedor=ProveedorOC(
            codigo_sap="100234", nit="900555111", nombre="TecnoSuministros S.A.S."
        ),
        moneda="COP",
        condiciones_pago="Z030",
        aprobador=AprobadorOC(
            email="ana.ramirez@empresa.com",
            fecha_aprobacion="2026-02-10",
            evidencia_sha256="a" * 64,
        ),
        posiciones=[
            PosicionOC(
                numero=10,
                descripcion="Portatiles corporativos",
                cantidad=10,
                unidad="UN",
                precio_unitario=4200000,
                centro_costo="CC-1001",
                subarea="Infraestructura",
                indicador_iva="C1",
            )
        ],
    )


def ruta_ordenes(raiz: Path) -> Path:
    """La misma ruta que usa el mock, resuelta por `ruta_salida` (mitigación D7)."""
    return ruta_salida(raiz, "sap", "ordenes.jsonl")


def lineas_de_ordenes(raiz: Path) -> list[str]:
    ruta = ruta_ordenes(raiz)
    if not ruta.exists():
        return []
    return [linea for linea in ruta.read_text(encoding="utf-8").splitlines() if linea.strip()]


# ── Numeración (I2) ────────────────────────────────────────────────────────


def test_primera_orden_en_out_limpio_arranca_en_4500000001(tmp_path: Path) -> None:
    mock = SapMock(tmp_path, cargar_maestros())

    resultado = asyncio.run(mock.crear_orden(orden_de("sol-001")))

    assert resultado["numero_oc"] == "4500000001"
    assert NUMERO_INICIAL == 4500000001
    assert resultado["fecha"] == date.today().isoformat()


def test_dos_ordenes_distintas_numeran_secuencialmente(tmp_path: Path) -> None:
    mock = SapMock(tmp_path, cargar_maestros())

    primera = asyncio.run(mock.crear_orden(orden_de("sol-001")))
    segunda = asyncio.run(mock.crear_orden(orden_de("sol-002")))

    assert primera["numero_oc"] == "4500000001"
    assert segunda["numero_oc"] == "4500000002"
    assert len(lineas_de_ordenes(tmp_path)) == 2


# ── Idempotencia (C10 · I3) — el test central de la tarea ──────────────────


def test_crear_dos_veces_el_mismo_solicitud_id_devuelve_el_mismo_numero(tmp_path: Path) -> None:
    """C10: nunca una segunda OC. Mismo número y **una sola** línea."""
    mock = SapMock(tmp_path, cargar_maestros())

    primera = asyncio.run(mock.crear_orden(orden_de("sol-001")))
    repetida = asyncio.run(mock.crear_orden(orden_de("sol-001")))

    assert repetida["numero_oc"] == primera["numero_oc"] == "4500000001"
    assert len(lineas_de_ordenes(tmp_path)) == 1


def test_la_idempotencia_no_consume_numeros(tmp_path: Path) -> None:
    """Reintentar sol-001 no debe correr el contador: sol-002 sigue siendo la 2."""
    mock = SapMock(tmp_path, cargar_maestros())

    asyncio.run(mock.crear_orden(orden_de("sol-001")))
    asyncio.run(mock.crear_orden(orden_de("sol-001")))
    tercera = asyncio.run(mock.crear_orden(orden_de("sol-002")))

    assert tercera["numero_oc"] == "4500000002"
    assert len(lineas_de_ordenes(tmp_path)) == 2


def test_la_linea_escrita_lleva_numero_fecha_referencia_y_la_orden_completa(
    tmp_path: Path,
) -> None:
    mock = SapMock(tmp_path, cargar_maestros())
    orden = orden_de("sol-001")

    asyncio.run(mock.crear_orden(orden))

    registro = json.loads(lineas_de_ordenes(tmp_path)[0])
    assert registro["numero_oc"] == "4500000001"
    assert registro["fecha"] == date.today().isoformat()
    assert registro["referencia"]["solicitud_id"] == "sol-001"
    # La orden va completa: es la evidencia de lo que se envió a SAP.
    assert registro["orden"] == orden.model_dump(mode="json")


# ── Búsqueda por referencia ────────────────────────────────────────────────


def test_buscar_orden_existente_devuelve_su_numero(tmp_path: Path) -> None:
    mock = SapMock(tmp_path, cargar_maestros())
    asyncio.run(mock.crear_orden(orden_de("sol-003")))

    assert asyncio.run(mock.buscar_orden_por_referencia("sol-003")) == {
        "numero_oc": "4500000001"
    }


def test_buscar_id_inexistente_devuelve_none(tmp_path: Path) -> None:
    mock = SapMock(tmp_path, cargar_maestros())
    asyncio.run(mock.crear_orden(orden_de("sol-001")))

    assert asyncio.run(mock.buscar_orden_por_referencia("sol-999")) is None


def test_buscar_sin_archivo_devuelve_none_sin_excepcion(tmp_path: Path) -> None:
    """`out/sap/ordenes.jsonl` no existe todavía: None, no FileNotFoundError."""
    mock = SapMock(tmp_path, cargar_maestros())

    assert asyncio.run(mock.buscar_orden_por_referencia("sol-001")) is None


# ── Robustez del .jsonl ────────────────────────────────────────────────────


def test_una_linea_corrupta_se_ignora_y_las_validas_se_siguen_leyendo(tmp_path: Path) -> None:
    mock = SapMock(tmp_path, cargar_maestros())
    asyncio.run(mock.crear_orden(orden_de("sol-001")))

    # Simula un append interrumpido a mitad de escritura.
    with ruta_ordenes(tmp_path).open("a", encoding="utf-8") as archivo:
        archivo.write("{roto\n")

    asyncio.run(mock.crear_orden(orden_de("sol-002")))

    assert asyncio.run(mock.buscar_orden_por_referencia("sol-001")) == {
        "numero_oc": "4500000001"
    }
    # La línea corrupta no consume número de OC: se cuentan solo las válidas.
    assert asyncio.run(mock.buscar_orden_por_referencia("sol-002")) == {
        "numero_oc": "4500000002"
    }


def test_una_linea_valida_pero_sin_referencia_no_revienta_la_busqueda(tmp_path: Path) -> None:
    """JSON válido con forma ajena: se ignora igual que una línea corrupta."""
    mock = SapMock(tmp_path, cargar_maestros())
    with ruta_ordenes(tmp_path).open("a", encoding="utf-8") as archivo:
        archivo.write(json.dumps({"numero_oc": "4500000001"}) + "\n")
        archivo.write(json.dumps(["no", "es", "un", "objeto"]) + "\n")

    assert asyncio.run(mock.buscar_orden_por_referencia("sol-001")) is None


# ── consultar_proveedor ────────────────────────────────────────────────────


def test_consultar_proveedor_cruza_con_puntos_y_digito_de_verificacion(tmp_path: Path) -> None:
    """El NIT llega formateado desde la solicitud y el maestro lo tiene limpio."""
    mock = SapMock(tmp_path, cargar_maestros())

    assert asyncio.run(mock.consultar_proveedor("900.555.111-2")) == {
        "codigo_sap": "100234",
        "activo": True,
    }


def test_consultar_proveedor_reporta_el_inactivo(tmp_path: Path) -> None:
    """Consultores Ágiles está inactivo en el maestro: el mock no lo esconde."""
    mock = SapMock(tmp_path, cargar_maestros())

    resultado = asyncio.run(mock.consultar_proveedor("901777888"))

    assert resultado == {"codigo_sap": "100402", "activo": False}


def test_consultar_proveedor_desconocido_devuelve_none(tmp_path: Path) -> None:
    mock = SapMock(tmp_path, cargar_maestros())

    assert asyncio.run(mock.consultar_proveedor("999999999")) is None


def test_consultar_proveedor_con_nit_sin_digitos_devuelve_none(tmp_path: Path) -> None:
    mock = SapMock(tmp_path, cargar_maestros())

    assert asyncio.run(mock.consultar_proveedor("sin-nit")) is None


# ── Destino de escritura (mitigación D7) ───────────────────────────────────


def test_la_orden_cae_bajo_out_sap_verificado_con_la_ruta_resuelta(tmp_path: Path) -> None:
    """El archivo tiene que estar dentro de `out/sap/`, no en cualquier parte."""
    mock = SapMock(tmp_path, cargar_maestros())
    asyncio.run(mock.crear_orden(orden_de("sol-001")))

    destino = (tmp_path / "out" / "sap" / "ordenes.jsonl").resolve()
    assert destino.exists()
    assert destino.is_relative_to((tmp_path / "out").resolve())
    # Y no se escribió nada fuera de out/.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]


# ── Contrato estructural con SapAdapter ────────────────────────────────────


def test_sap_mock_satisface_sap_adapter(tmp_path: Path) -> None:
    """Aserción de tipado estructural: la verifica `mypy --strict`, no el runtime.

    `SapMock` no hereda de `SapAdapter` (es un `Protocol`), así que esta
    asignación es la única garantía de que el mock es sustituible por el
    adaptador OData o RFC de producción.
    """
    adaptador: SapAdapter = SapMock(tmp_path, cargar_maestros())

    assert asyncio.run(adaptador.consultar_proveedor("900555111")) is not None
