"""La evidencia de la aprobación: `out/<caso>/aprobacion.txt` y su sha256 (HU-4).

Lo que se audita es el archivo que queda en `out/`, no el fixture: el hash se
calcula sobre el contenido exacto que se escribió. Por eso
`oc_generar_evidencia` va **antes** de `oc_construir_payload`, que consume ese
hash.

Todo destino de escritura sale de `ruta_salida` (mitigación del defecto **D7**):
nunca se arma `raiz / "out" / ...` a mano, porque `ruta_salida` es el único punto
que verifica que el destino cae dentro de `out/`.

Separado de `app/tools/oc.py` en la rama `refactor/langchain`. Devuelve el string
JSON del contrato porque sus dos desenlaces —hay evidencia o falta el correo del
líder— son mensajes de negocio, no excepciones técnicas.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.core.io import escribir_texto, ruta_salida
from app.core.modelos import error, ok
from app.paquete import carpeta_del_caso, leer_adjunto, ruta_legible

ARCHIVO_EVIDENCIA = "aprobacion.txt"


def generar_evidencia(raiz: Path, caso: str) -> str:
    """Escribe la evidencia y devuelve su ruta y su sha256, o el error legible."""
    carpeta = carpeta_del_caso(raiz, caso)
    crudo = leer_adjunto(carpeta, "aprobacion.json")
    if crudo is None:
        return error(
            f"El caso {caso} no tiene aprobacion.json, así que no hay evidencia que "
            "generar. Pide al solicitante el correo de aprobación de su líder."
        )

    contenido = _texto_evidencia(crudo)
    # D7: el destino sale de `ruta_salida`, nunca de `raiz / "out" / ...`.
    destino = escribir_texto(ruta_evidencia(raiz, caso), contenido)
    return ok(
        {
            "ruta": ruta_legible(raiz, destino),
            # El hash es del contenido exacto escrito, no del fixture: lo que se
            # audita es el archivo de evidencia que queda en out/.
            "sha256": sha256(contenido),
        }
    )


def ruta_evidencia(raiz: Path, caso: str) -> Path:
    """`out/<caso>/aprobacion.txt`, siempre por `ruta_salida` (D7)."""
    return ruta_salida(raiz, caso, ARCHIVO_EVIDENCIA)


def _texto_evidencia(crudo: dict[str, Any]) -> str:
    """Encabezados del correo, línea en blanco y cuerpo.

    Es el formato de un `.eml` legible por una persona: lo que una auditoría
    quiere ver es quién aprobó, cuándo y qué dijo.
    """
    encabezados = [
        f"De: {crudo.get('de', '')}",
        f"Para: {crudo.get('para', '')}",
        f"Fecha: {crudo.get('fecha', '')}",
        f"Asunto: {crudo.get('asunto', '')}",
    ]
    return "\n".join(encabezados) + "\n\n" + str(crudo.get("cuerpo", "")) + "\n"


def sha256(contenido: str) -> str:
    """Huella del contenido en UTF-8. Es la que viaja al payload de la OC."""
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()
