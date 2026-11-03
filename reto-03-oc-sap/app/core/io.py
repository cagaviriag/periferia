"""Entrada/salida de archivos.

Toda lectura de fixtures y toda escritura pasan por aquí. `fixtures/` es de solo
lectura (C1) y `ruta_salida` es el único punto del sistema que autoriza escritura:
rechaza cualquier destino que caiga fuera de `out/`.

Solo primitivas de `pathlib` y `json`: este módulo nunca ejecuta un comando del
sistema operativo (PRD §8 Seguridad).

El `Any` de las firmas es la frontera de deserialización: aquí el JSON de los
fixtures todavía no tiene esquema, lo validan los modelos de T0.6 (ADR-009).
"""

import json
import shutil
from pathlib import Path
from typing import Any


class RutaInvalidaError(Exception):
    """Se intentó escribir fuera de out/."""


def ruta_fixture(raiz: Path, *partes: str) -> Path:
    """Construye una ruta dentro de fixtures/. No verifica existencia."""
    return (raiz / "fixtures").joinpath(*partes)


def ruta_salida(raiz: Path, *partes: str) -> Path:
    """Construye una ruta dentro de out/, creando los directorios padre.

    Lanza RutaInvalidaError si el resultado resuelto cae fuera de out/.
    """
    salida = (raiz / "out").resolve()
    destino = (raiz / "out").joinpath(*partes).resolve()

    # Control crítico: bloquea `..`, `..` anidado y rutas absolutas, porque
    # `joinpath` con una ruta absoluta descarta el prefijo (C1 · T0.3).
    if not destino.is_relative_to(salida):
        raise RutaInvalidaError(
            f"Ruta de escritura fuera de out/: {destino}. Toda escritura va a out/."
        )

    destino.parent.mkdir(parents=True, exist_ok=True)
    return destino


def leer_json(ruta: Path) -> dict[str, Any] | list[Any] | None:
    """Lee y parsea JSON. Devuelve None si el archivo no existe.

    Si el JSON está malformado, o si es válido pero no es un objeto ni un
    arreglo, lanza ValueError con un mensaje que incluye el nombre del archivo.
    El llamador (una herramienta) lo convierte en {"ok": false, "error": ...}.
    """
    if not ruta.exists():
        return None

    texto = ruta.read_text(encoding="utf-8")
    try:
        # La anotación explícita evita devolver el `Any` que produce json.loads.
        contenido: dict[str, Any] | list[Any] = json.loads(texto)
    except json.JSONDecodeError as error:
        raise ValueError(f"JSON malformado en {ruta.name}: {error}") from error

    # `json.loads` devuelve `Any` (ADR-009), así que mypy no atrapa que un archivo
    # con `42`, `"hola"` o `true` sea JSON válido y rompa el tipo de retorno
    # declarado. El isinstance hace cumplir el contrato en ejecución: sin él, un
    # `len()` o un `x["clave"]` del llamador daría TypeError lejos de la causa.
    if not isinstance(contenido, dict | list):
        # Se silencia TRY004 (ruff sugiere TypeError) a propósito: el contrato de
        # `leer_json` promete ValueError para "archivo con contenido inválido", y
        # así lo captura el llamador de Fase 2 para devolver {"ok": false, ...}
        # (C3). Un TypeError obligaría a cada herramienta a capturar dos
        # excepciones distintas por la misma causa: el archivo no sirve.
        raise ValueError(  # noqa: TRY004
            f"JSON inesperado en {ruta.name}: se esperaba un objeto o un arreglo, "
            f"llegó {type(contenido).__name__}."
        )
    return contenido


def leer_texto(ruta: Path) -> str | None:
    """Lee un archivo de texto en UTF-8. Devuelve None si no existe."""
    if not ruta.exists():
        return None
    return ruta.read_text(encoding="utf-8")


def escribir_json(ruta: Path, datos: object) -> Path:
    """Escribe JSON indentado con ensure_ascii=False. Devuelve la ruta."""
    # ensure_ascii=False para que los acentos del dominio ("Papelería") queden legibles.
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    return ruta


def escribir_texto(ruta: Path, contenido: str) -> Path:
    """Escribe texto UTF-8. Devuelve la ruta."""
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def agregar_linea_jsonl(ruta: Path, registro: dict[str, Any]) -> None:
    """Agrega una línea JSON al final del archivo."""
    with ruta.open("a", encoding="utf-8") as archivo:
        archivo.write(json.dumps(registro, ensure_ascii=False) + "\n")


def limpiar_salida(raiz: Path) -> None:
    """Borra el contenido de out/ para garantizar determinismo (C9).

    Solo borra dentro de out/. Si out/ no existe, la crea.
    """
    salida = raiz / "out"
    salida.mkdir(parents=True, exist_ok=True)

    # Se itera el contenido y se borra uno por uno: nunca `rmtree` sobre out/ ni
    # sobre la raíz del proyecto.
    for elemento in salida.iterdir():
        if elemento.is_dir() and not elemento.is_symlink():
            shutil.rmtree(elemento)
        else:
            elemento.unlink()
