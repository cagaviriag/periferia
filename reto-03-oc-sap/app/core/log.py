"""Bitácora técnica y log de control.

Dos registros obligatorios del PRD:

- `out/log.jsonl`  — una línea por llamada a herramienta (CA4 · C8).
- `out/control.csv` — una fila por cada intento de OC: exitoso, bloqueado o
  pendiente (HU-5 · oráculo I6).

Ninguna de las dos funciones lanza: un fallo de bitácora no puede romper una
herramienta (C3).
"""

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.core.io import agregar_linea_jsonl, ruta_salida

ResultadoIntento = Literal[
    "creada", "bloqueada", "pendiente_confirmacion", "idempotente", "error"
]

# Encabezado textual de HU-5. El orden es parte del contrato con auditoría.
ENCABEZADO_CONTROL = [
    "solicitud_id",
    "resultado",
    "numero_oc",
    "retroactiva",
    "bloqueos",
    "confirmaciones",
    "ts",
]


def _ahora() -> str:
    """Marca de tiempo ISO 8601 en UTC."""
    # La spec T0.5 §Comportamiento 1 prescribe esta expresión literal.
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def registrar_herramienta(
    raiz: Path,
    herramienta: str,
    ok: bool,
    resumen: str,
    caso: str | None = None,
) -> None:
    """Agrega una línea a out/log.jsonl (CA4).

    Formato: {"ts", "herramienta", "caso", "ok", "resumen"}.
    `resumen` es una frase corta, NUNCA el payload completo ni datos sensibles.
    Esta función no lanza: si el log falla, el flujo continúa.
    """
    try:
        registro = {
            "ts": _ahora(),
            "herramienta": herramienta,
            "caso": caso,
            "ok": ok,
            "resumen": resumen,
        }
        agregar_linea_jsonl(ruta_salida(raiz, "log.jsonl"), registro)
    except Exception:  # noqa: BLE001, S110
        # C3: la bitácora es observabilidad, no parte del resultado. Si falla,
        # se pierde la línea pero la herramienta responde igual. El `pass` es
        # deliberado: no hay a dónde reportar si el propio log es lo que falló.
        pass


def registrar_control(
    raiz: Path,
    solicitud_id: str,
    resultado: ResultadoIntento,
    numero_oc: str | None,
    retroactiva: bool,
    bloqueos: list[str],
    confirmaciones: list[str],
) -> None:
    """Agrega una fila a out/control.csv (HU-5).

    Crea el archivo con encabezado si no existe. `bloqueos` y `confirmaciones`
    se serializan como códigos separados por ';' (ej. "RC2;RC4"), vacío si no hay.
    Esta función no lanza.
    """
    try:
        ruta = ruta_salida(raiz, "control.csv")
        hay_que_escribir_encabezado = not ruta.exists()

        fila = [
            solicitud_id,
            resultado,
            numero_oc if numero_oc is not None else "",
            "true" if retroactiva else "false",
            ";".join(bloqueos),
            ";".join(confirmaciones),
            _ahora(),
        ]

        # Se usa el módulo `csv` y no `",".join(...)`: los detalles de un bloqueo
        # pueden contener comas (ej. "RC5: 25.000.000, no 26.500.000") y romperían
        # el conteo de columnas al releer.
        with ruta.open("a", newline="", encoding="utf-8") as archivo:
            escritor = csv.writer(archivo)
            if hay_que_escribir_encabezado:
                escritor.writerow(ENCABEZADO_CONTROL)
            escritor.writerow(fila)
    except Exception:  # noqa: BLE001, S110
        # C3: igual que la bitácora, el log de control nunca rompe el flujo.
        pass
