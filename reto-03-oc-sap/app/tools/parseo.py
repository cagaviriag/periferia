"""Parseo de los adjuntos de texto del paquete de compra (T2.2 · hallazgo H1).

`cotizacion.pdf` y `factura.pdf` llegan normalizados a `.txt` (ver `nota_fixture`
de cada `correo.json`). Este módulo los convierte en los diccionarios con los que
`oc_leer_paquete` construye `Cotizacion` y `Factura`.

Tres reglas del módulo:

1. **Ninguna función lanza** (C3). Un campo que no aparece queda en `None` y un
   documento sin su dato obligatorio devuelve `None` completo.
2. **No se inventan valores** (CA2). Si falta la fecha o los días de validez,
   `validez_hasta` es `None`; no se asume "30 días" ni la fecha de hoy.
3. Los montos son `int` en pesos, como el resto del dominio.

El detalle que es fácil equivocar: la **factura** dice `TOTAL:` y la **cotización**
dice `TOTAL (IVA incluido):`. Son dos patrones distintos. Reutilizar uno para el
otro hace que la factura de `sol-005` quede sin total y RC8 no detecte la
retroactividad.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.core.normalizar import normalizar_nit

# ── Patrones de cotizacion.txt ─────────────────────────────────────────────
# Se ancla con `^` y re.MULTILINE donde el dato vive en su propia línea: en la
# cotización la palabra "NIT" también aparece dentro de la línea "Cliente:
# Periferia IT Group S.A.S. - NIT 900.123.456-7", y sin ancla se podría cruzar
# el NIT del cliente con el del proveedor.
_REFERENCIA = re.compile(r"COTIZACI[ÓO]N\s+(\S+)")
_FECHA_COTIZACION = re.compile(r"^Fecha:\s*(\S+)", re.MULTILINE)
_PROVEEDOR = re.compile(r"^Proveedor:\s*(.+)$", re.MULTILINE)
_NIT = re.compile(r"^NIT:\s*(\S+)", re.MULTILINE)
# El ítem es el texto entre "1. " y el primer "|" de la línea del ítem.
_ITEM = re.compile(r"^1\.\s*([^|]+)\|", re.MULTILINE)
_TOTAL_COTIZACION = re.compile(r"^TOTAL \(IVA incluido\):\s*(.+)$", re.MULTILINE)
_VALIDEZ = re.compile(r"^Validez de la oferta:\s*(\d+)\s*d[íi]as", re.MULTILINE)

# ── Patrones de factura.txt ────────────────────────────────────────────────
_NUMERO_FACTURA = re.compile(r"FACTURA ELECTR[ÓO]NICA DE VENTA No\.\s*(\S+)")
_FECHA_FACTURA = re.compile(r"^Fecha de emisi[óo]n:\s*(\S+)", re.MULTILINE)
_TOTAL_FACTURA = re.compile(r"^TOTAL:\s*(.+)$", re.MULTILINE)

# ── Auxiliares ─────────────────────────────────────────────────────────────
_MONEDA = re.compile(r"\b(COP|USD)\b")
MONEDA_POR_DEFECTO = "COP"


def parsear_monto(texto: str) -> int | None:
    """`'COP 11.400.000'` → `11400000`. Devuelve None si no queda un entero.

    Quita el código de moneda, los puntos de miles y los espacios. Un monto con
    decimales o con letras ("tres millones") devuelve None: el dominio es
    pesos enteros y adivinar sería inventar un valor (CA2).
    """
    limpio = _MONEDA.sub("", texto)
    limpio = limpio.replace("$", "").replace(".", "").replace(",", "")
    # \xa0 es el espacio duro que suele dejar la extracción de texto de un PDF.
    limpio = limpio.replace("\xa0", "").replace(" ", "").strip()
    if not limpio.isdigit():
        return None
    return int(limpio)


def parsear_cotizacion(texto: str) -> dict[str, object] | None:
    """Extrae los campos de `cotizacion.txt`.

    Devuelve las claves de `Cotizacion` (`referencia`, `proveedor`, `nit`,
    `total`, `moneda`, `validez_hasta`, `item`, `texto`), o None si no se pudo
    leer el **total**, que es el único campo sin el cual la cotización no sirve
    para nada: RC5 contrasta contra él.
    """
    linea_total = _TOTAL_COTIZACION.search(texto)
    if linea_total is None:
        return None

    total = parsear_monto(linea_total.group(1))
    if total is None:
        return None

    proveedor = _PROVEEDOR.search(texto)
    referencia = _REFERENCIA.search(texto)
    nit = _NIT.search(texto)
    item = _ITEM.search(texto)

    return {
        "referencia": referencia.group(1) if referencia else None,
        "proveedor": proveedor.group(1).strip() if proveedor else "",
        # ADR-008: el NIT se guarda ya normalizado ("900.555.111-2" → "900555111")
        # para que cualquier cruce posterior contra el maestro compare lo mismo.
        "nit": normalizar_nit(nit.group(1)) if nit else None,
        "total": total,
        "moneda": _moneda_de(linea_total.group(1)),
        "validez_hasta": _validez_hasta(texto),
        "item": item.group(1).strip() if item else None,
        "texto": texto,
    }


def parsear_factura(texto: str) -> dict[str, object] | None:
    """Extrae `numero`, `fecha` y `total` de `factura.txt`.

    Devuelve None si falta cualquiera de los tres: RC8 compara la fecha de la
    factura contra la de la solicitud, y una factura sin fecha no permite
    concluir si la OC es retroactiva. Antes que suponer, se informa que falta.
    """
    numero = _NUMERO_FACTURA.search(texto)
    fecha = _FECHA_FACTURA.search(texto)
    linea_total = _TOTAL_FACTURA.search(texto)
    if numero is None or fecha is None or linea_total is None:
        return None

    total = parsear_monto(linea_total.group(1))
    if total is None:
        return None

    return {
        "numero": numero.group(1),
        "fecha": fecha.group(1),
        "total": total,
    }


def _moneda_de(linea: str) -> str:
    """Código de moneda de una línea de monto. `COP` es el default del dominio."""
    encontrada = _MONEDA.search(linea)
    if encontrada is None:
        return MONEDA_POR_DEFECTO
    return encontrada.group(1)


def _validez_hasta(texto: str) -> str | None:
    """`Fecha` de la cotización + los días de validez, en `YYYY-MM-DD`.

    None si falta la fecha, si faltan los días o si la fecha no es una fecha ISO.
    No se inventa una vigencia por defecto (CA2).
    """
    fecha = _FECHA_COTIZACION.search(texto)
    dias = _VALIDEZ.search(texto)
    if fecha is None or dias is None:
        return None

    try:
        emision = date.fromisoformat(fecha.group(1))
    except ValueError:
        # La fecha no es ISO: se informa la ausencia en lugar de adivinar.
        return None

    return (emision + timedelta(days=int(dias.group(1)))).isoformat()
