"""Las piezas compartidas del contrato de respuesta: describir, resumir, codificar.

Existe por una razón de dependencias, no de diseño: el contrato `{"ok": ...}` es
**viral**. Los mensajes de error de una herramienta son mensajes de negocio ("es
el bloqueo RC1", "pide al solicitante el correo de su líder"), así que las capas
de lógica —`app/payload.py`, `app/paquete.py`— también los producen, y necesitan
las mismas utilidades que `app/tools/oc.py`. Dejarlas en `oc.py` obligaría a esos
módulos a importar la capa de herramientas, que es justamente la dependencia que
el refactor rompe.

Ninguna de estas funciones lanza.
"""

from __future__ import annotations

import json

from app.core.modelos import Veredicto

# Límite del mensaje de error que se le devuelve al modelo: una `ValidationError`
# de Pydantic con veinte campos no aporta nada después de las primeras líneas y
# consume contexto (C6).
LARGO_MAXIMO_ERROR = 500

# Largo del error que viaja al resumen de `out/log.jsonl`.
LARGO_RESUMEN_ERROR = 200


def describir(excepcion: Exception) -> str:
    """Mensaje legible de una excepción, con su tipo y recortado."""
    mensaje = " ".join(str(excepcion).split()) or "sin detalle"
    if len(mensaje) > LARGO_MAXIMO_ERROR:
        mensaje = mensaje[:LARGO_MAXIMO_ERROR] + "…"
    return f"{type(excepcion).__name__}: {mensaje}"


def fue_ok(respuesta: str) -> bool:
    """Lee la bandera `ok` de una respuesta ya serializada. No lanza."""
    try:
        cuerpo = json.loads(respuesta)
    except ValueError:
        return False
    return isinstance(cuerpo, dict) and cuerpo.get("ok") is True


def resumen(respuesta: str) -> str:
    """Frase corta para `out/log.jsonl` (CA4).

    Solo los valores escalares de `data`: nunca el payload completo. Así la
    bitácora es auditable sin convertirse en un volcado de datos (C8).
    """
    try:
        cuerpo = json.loads(respuesta)
    except ValueError:
        return "respuesta ilegible"
    if not isinstance(cuerpo, dict):
        return "respuesta ilegible"
    if cuerpo.get("ok") is not True:
        return str(cuerpo.get("error", "error sin detalle"))[:LARGO_RESUMEN_ERROR]

    datos = cuerpo.get("data")
    if not isinstance(datos, dict):
        return "ok"
    escalares = [
        f"{clave}={valor}"
        for clave, valor in datos.items()
        if isinstance(valor, bool | int | str) and len(str(valor)) <= 60
    ]
    return " ".join(escalares) or "ok"


def codigos(veredictos: list[Veredicto]) -> list[str]:
    """Solo los códigos: es lo que va a `control.csv`."""
    return [veredicto.codigo for veredicto in veredictos]


def detallar(veredictos: list[Veredicto]) -> str:
    """`"RC1: el proveedor no existe; RC4: ..."` para el mensaje al usuario."""
    return "; ".join(f"{veredicto.codigo}: {veredicto.detalle}" for veredicto in veredictos)
