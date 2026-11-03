"""Tests de T0.5 · Bitácora y log de control."""

import csv
import json
from pathlib import Path

import pytest

from app.core.log import ENCABEZADO_CONTROL, registrar_control, registrar_herramienta


def leer_filas(raiz: Path) -> list[list[str]]:
    """Relee out/control.csv con csv.reader, como lo haría auditoría."""
    ruta = raiz / "out" / "control.csv"
    with ruta.open(newline="", encoding="utf-8") as archivo:
        return list(csv.reader(archivo))


# --- registrar_herramienta -------------------------------------------------------


def test_registrar_herramienta_tres_veces_deja_tres_lineas(tmp_path: Path) -> None:
    registrar_herramienta(tmp_path, "oc_leer_paquete", True, "paquete leído", "sol-001")
    registrar_herramienta(tmp_path, "oc_validar", False, "1 bloqueo", "sol-002")
    registrar_herramienta(tmp_path, "oc_crear", True, "OC creada", "sol-001")

    lineas = (tmp_path / "out" / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lineas) == 3
    for linea in lineas:
        registro = json.loads(linea)
        assert set(registro) == {"ts", "herramienta", "caso", "ok", "resumen"}


def test_registrar_herramienta_sin_caso_deja_caso_nulo(tmp_path: Path) -> None:
    registrar_herramienta(tmp_path, "oc_sap_estado", True, "simulador vivo")

    linea = (tmp_path / "out" / "log.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(linea)["caso"] is None


def test_registrar_herramienta_no_propaga_excepciones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explotar(*_args: object, **_kwargs: object) -> Path:
        raise OSError("out/ no escribible")

    monkeypatch.setattr("app.core.log.ruta_salida", explotar)
    # C3: no debe lanzar.
    registrar_herramienta(tmp_path, "oc_validar", True, "resumen")


# --- registrar_control -----------------------------------------------------------


def test_registrar_control_primera_vez_escribe_encabezado_y_una_fila(tmp_path: Path) -> None:
    registrar_control(tmp_path, "SOL-2026-001", "creada", "4500000001", False, [], [])

    filas = leer_filas(tmp_path)
    assert filas[0] == ENCABEZADO_CONTROL
    assert len(filas) == 2
    assert filas[1][0] == "SOL-2026-001"
    assert filas[1][1] == "creada"
    assert filas[1][2] == "4500000001"
    assert filas[1][3] == "false"


def test_encabezado_coincide_literalmente_con_hu5(tmp_path: Path) -> None:
    registrar_control(tmp_path, "SOL-2026-001", "creada", "4500000001", False, [], [])

    primera_linea = (tmp_path / "out" / "control.csv").read_text(encoding="utf-8").splitlines()[0]
    assert primera_linea == "solicitud_id,resultado,numero_oc,retroactiva,bloqueos,confirmaciones,ts"


def test_registrar_control_tres_veces_escribe_el_encabezado_una_sola_vez(tmp_path: Path) -> None:
    for indice in range(3):
        registrar_control(tmp_path, f"SOL-2026-00{indice}", "bloqueada", None, False, ["RC1"], [])

    filas = leer_filas(tmp_path)
    assert filas[0] == ENCABEZADO_CONTROL
    assert len(filas) == 4
    assert all(fila[0] != "solicitud_id" for fila in filas[1:])


def test_bloqueos_se_serializan_con_punto_y_coma(tmp_path: Path) -> None:
    registrar_control(tmp_path, "SOL-2026-003", "bloqueada", None, False, ["RC2", "RC4"], [])

    assert leer_filas(tmp_path)[1][4] == "RC2;RC4"


def test_listas_vacias_y_numero_oc_nulo_dejan_celdas_vacias(tmp_path: Path) -> None:
    registrar_control(tmp_path, "SOL-2026-004", "pendiente_confirmacion", None, False, [], [])

    fila = leer_filas(tmp_path)[1]
    assert fila[2] == ""  # numero_oc
    assert fila[4] == ""  # bloqueos
    assert fila[5] == ""  # confirmaciones


def test_un_detalle_con_coma_no_rompe_las_columnas(tmp_path: Path) -> None:
    # El test que importa: demuestra por qué se usa el módulo csv y no ",".join(...).
    detalle = "RC5: 25.000.000, no 26.500.000"
    registrar_control(tmp_path, "SOL-2026-004", "bloqueada", None, False, [detalle], [])

    filas = leer_filas(tmp_path)
    assert len(filas[1]) == 7
    assert filas[1][4] == detalle
    assert all(len(fila) == 7 for fila in filas)


def test_retroactiva_true_se_escribe_en_minusculas(tmp_path: Path) -> None:
    registrar_control(tmp_path, "SOL-2026-005", "creada", "4500000005", True, [], ["RC8"])

    fila = leer_filas(tmp_path)[1]
    assert fila[3] == "true"
    assert fila[5] == "RC8"


def test_registrar_control_no_propaga_excepciones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explotar(*_args: object, **_kwargs: object) -> Path:
        raise OSError("out/ no escribible")

    monkeypatch.setattr("app.core.log.ruta_salida", explotar)
    # C3: no debe lanzar.
    registrar_control(tmp_path, "SOL-2026-001", "creada", "4500000001", False, [], [])


def test_los_seis_casos_del_oraculo_dejan_seis_filas(tmp_path: Path) -> None:
    # Invariante I6: una fila por intento, sea exitoso, bloqueado o pendiente.
    registrar_control(tmp_path, "SOL-2026-001", "creada", "4500000001", False, [], [])
    registrar_control(tmp_path, "SOL-2026-002", "bloqueada", None, False, ["RC1"], [])
    registrar_control(tmp_path, "SOL-2026-003", "bloqueada", None, False, ["RC2"], [])
    registrar_control(tmp_path, "SOL-2026-004", "pendiente_confirmacion", None, False, [], ["RC5"])
    registrar_control(tmp_path, "SOL-2026-005", "pendiente_confirmacion", None, True, [], ["RC8"])
    registrar_control(tmp_path, "SOL-2026-006", "pendiente_confirmacion", None, False, [], ["RC6"])

    filas = leer_filas(tmp_path)
    assert filas[0] == ENCABEZADO_CONTROL
    assert len(filas) - 1 == 6
    assert all(len(fila) == 7 for fila in filas)
