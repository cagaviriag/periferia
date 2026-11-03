"""Tests de T0.3 · Entrada/salida de archivos."""

import json
from pathlib import Path

import pytest

from app.core.config import cargar_config
from app.core.io import (
    RutaInvalidaError,
    agregar_linea_jsonl,
    escribir_json,
    escribir_texto,
    leer_json,
    leer_texto,
    limpiar_salida,
    ruta_fixture,
    ruta_salida,
)


@pytest.fixture
def raiz_real() -> Path:
    """Raíz del proyecto, para los tests que LEEN fixtures."""
    return cargar_config(requiere_llm=False).raiz


# --- ruta_salida: el control crítico de escritura ---------------------------------


def test_ruta_salida_construye_bajo_out_y_crea_el_padre(tmp_path: Path) -> None:
    destino = ruta_salida(tmp_path, "sol-001", "payload.json")
    assert destino == (tmp_path / "out" / "sol-001" / "payload.json").resolve()
    assert destino.parent.is_dir()


def test_ruta_salida_rechaza_punto_punto(tmp_path: Path) -> None:
    with pytest.raises(RutaInvalidaError):
        ruta_salida(tmp_path, "..", "escape.txt")


def test_ruta_salida_rechaza_punto_punto_anidado(tmp_path: Path) -> None:
    with pytest.raises(RutaInvalidaError):
        ruta_salida(tmp_path, "../../etc/passwd")


def test_ruta_salida_rechaza_ruta_absoluta(tmp_path: Path) -> None:
    with pytest.raises(RutaInvalidaError):
        ruta_salida(tmp_path, "/etc/passwd")


# --- lectura ---------------------------------------------------------------------


def test_leer_json_de_archivo_inexistente_devuelve_none(tmp_path: Path) -> None:
    assert leer_json(tmp_path / "no-existe.json") is None


def test_leer_json_malformado_lanza_valueerror_con_el_nombre(tmp_path: Path) -> None:
    roto = tmp_path / "roto.json"
    roto.write_text('{"roto":', encoding="utf-8")
    with pytest.raises(ValueError, match="roto.json"):
        leer_json(roto)


@pytest.mark.parametrize(
    ("contenido", "tipo_informado"),
    [("42", "int"), ('"hola"', "str"), ("true", "bool")],
)
def test_leer_json_de_un_escalar_lanza_valueerror(
    tmp_path: Path, contenido: str, tipo_informado: str
) -> None:
    # D4: el tipo de retorno promete `dict | list | None`, pero `json.loads`
    # devuelve `Any` y mypy no atrapa el escalar (ADR-009). Lo cierra un
    # isinstance en ejecución.
    escalar = tmp_path / "escalar.json"
    escalar.write_text(contenido, encoding="utf-8")

    with pytest.raises(ValueError) as capturado:
        leer_json(escalar)

    mensaje = str(capturado.value)
    assert "escalar.json" in mensaje   # el mensaje nombra el archivo culpable
    assert tipo_informado in mensaje


def test_leer_json_de_objeto_y_de_arreglo_si_pasa(tmp_path: Path) -> None:
    objeto = tmp_path / "objeto.json"
    objeto.write_text('{"a": 1}', encoding="utf-8")
    arreglo = tmp_path / "arreglo.json"
    arreglo.write_text("[1, 2]", encoding="utf-8")

    assert leer_json(objeto) == {"a": 1}
    assert leer_json(arreglo) == [1, 2]


def test_leer_json_de_proveedores_devuelve_cinco_elementos(raiz_real: Path) -> None:
    ruta = ruta_fixture(raiz_real, "reto-03", "maestros", "proveedores.json")
    proveedores = leer_json(ruta)
    assert isinstance(proveedores, list)
    assert len(proveedores) == 5


def test_leer_texto_de_cotizacion_contiene_el_numero(raiz_real: Path) -> None:
    ruta = ruta_fixture(raiz_real, "reto-03", "solicitudes", "sol-001", "cotizacion.txt")
    contenido = leer_texto(ruta)
    assert contenido is not None
    assert "COT-TS-2026-0451" in contenido


def test_leer_texto_de_archivo_inexistente_devuelve_none(tmp_path: Path) -> None:
    assert leer_texto(tmp_path / "no-existe.txt") is None


# --- escritura -------------------------------------------------------------------


def test_escribir_y_leer_json_con_acentos_es_ida_y_vuelta_identica(tmp_path: Path) -> None:
    datos = {"categoria": "Papelería", "detalle": "Resmas tamaño carta"}
    destino = ruta_salida(tmp_path, "sol-006", "payload.json")

    escribir_json(destino, datos)

    assert leer_json(destino) == datos
    # ensure_ascii=False: el acento queda literal en el archivo, no como í.
    assert "Papelería" in destino.read_text(encoding="utf-8")


def test_escribir_texto_devuelve_la_ruta(tmp_path: Path) -> None:
    destino = ruta_salida(tmp_path, "sol-001", "evidencia.txt")
    assert escribir_texto(destino, "contenido") == destino
    assert destino.read_text(encoding="utf-8") == "contenido"


def test_agregar_linea_jsonl_tres_veces_deja_tres_lineas_validas(tmp_path: Path) -> None:
    destino = ruta_salida(tmp_path, "log.jsonl")

    for indice in range(3):
        agregar_linea_jsonl(destino, {"n": indice, "herramienta": "oc_leer_paquete"})

    lineas = destino.read_text(encoding="utf-8").strip().splitlines()
    assert len(lineas) == 3
    for indice, linea in enumerate(lineas):
        assert json.loads(linea)["n"] == indice


# --- limpiar_salida --------------------------------------------------------------


def test_limpiar_salida_vacia_out_sin_borrarla(tmp_path: Path) -> None:
    escribir_texto(ruta_salida(tmp_path, "log.jsonl"), "x")
    escribir_texto(ruta_salida(tmp_path, "sol-001", "payload.json"), "{}")
    salida = tmp_path / "out"
    assert list(salida.iterdir()) != []

    limpiar_salida(tmp_path)

    assert salida.is_dir()
    assert list(salida.iterdir()) == []


def test_limpiar_salida_crea_out_si_no_existe(tmp_path: Path) -> None:
    limpiar_salida(tmp_path)
    assert (tmp_path / "out").is_dir()


# --- C1: fixtures son inmutables -------------------------------------------------


def test_escribir_en_fixtures_falla(raiz_real: Path) -> None:
    # C1: fixtures/ está montado en solo lectura (chmod a-w).
    destino = ruta_fixture(raiz_real, "no-debe-crearse.txt")
    with pytest.raises(OSError):
        escribir_texto(destino, "esto no debe escribirse")
    assert not destino.exists()
