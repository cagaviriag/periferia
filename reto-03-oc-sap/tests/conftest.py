"""Andamiaje compartido de tests.

`construir_paquete` arma un `Paquete` desde los fixtures reales. Es **andamiaje
de prueba**, no código de producción: el armador real es `oc_leer_paquete` con
`app/tools/parseo.py` (T2.3). Existe aquí porque los controles de la Fase 1 se
prueban antes de que las herramientas de la Fase 2 existan.

Cuando T2.3 aterrice, este módulo debería delegar en el parser real para que no
haya dos lecturas divergentes del mismo fixture.
"""

import json
import re
from pathlib import Path

from app.core.modelos import (
    Aprobacion,
    Correo,
    Cotizacion,
    Factura,
    Maestros,
    Paquete,
    Solicitud,
)

RAIZ = Path(__file__).resolve().parent.parent
FIXTURES = RAIZ / "fixtures" / "reto-03"
SOLICITUDES = FIXTURES / "solicitudes"
MAESTROS = FIXTURES / "maestros"

CASOS = ["sol-001", "sol-002", "sol-003", "sol-004", "sol-005", "sol-006"]


def _leer_json(ruta: Path) -> dict[str, object]:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert isinstance(datos, dict)
    return datos


def cargar_maestros() -> Maestros:
    """Los 4 maestros reales de fixtures/reto-03/maestros/."""
    return Maestros(
        proveedores=json.loads((MAESTROS / "proveedores.json").read_text(encoding="utf-8")),
        centros_costo=json.loads((MAESTROS / "centros-costo.json").read_text(encoding="utf-8")),
        indicadores_iva=json.loads((MAESTROS / "indicadores-iva.json").read_text(encoding="utf-8")),
        condiciones_pago=json.loads(
            (MAESTROS / "condiciones-pago.json").read_text(encoding="utf-8")
        ),
    )


def _monto(texto: str) -> int | None:
    """'COP 11.400.000' -> 11400000."""
    limpio = texto.replace("COP", "").replace(".", "").replace(" ", "").strip()
    return int(limpio) if limpio.isdigit() else None


def _parsear_cotizacion(texto: str) -> Cotizacion | None:
    total = None
    linea_total = re.search(r"TOTAL \(IVA incluido\):\s*(.+)", texto)
    if linea_total:
        total = _monto(linea_total.group(1))
    if total is None:
        return None

    referencia = re.search(r"COTIZACI[ÓO]N\s+(\S+)", texto)
    proveedor = re.search(r"Proveedor:\s*(.+)", texto)
    nit = re.search(r"NIT:\s*(\S+)", texto)
    # El ítem es el texto entre "1. " y el primer "|" de la línea del ítem.
    item = re.search(r"^1\.\s*([^|]+)\|", texto, re.MULTILINE)

    return Cotizacion(
        referencia=referencia.group(1) if referencia else None,
        proveedor=proveedor.group(1).strip() if proveedor else "",
        nit=nit.group(1) if nit else None,
        total=total,
        moneda="COP",
        validez_hasta=None,
        item=item.group(1).strip() if item else None,
        texto=texto,
    )


def _parsear_factura(texto: str) -> Factura | None:
    numero = re.search(r"FACTURA ELECTR[ÓO]NICA DE VENTA No\.\s*(\S+)", texto)
    fecha = re.search(r"Fecha de emisi[óo]n:\s*(\S+)", texto)
    # Ojo: la factura usa "TOTAL:" y la cotización "TOTAL (IVA incluido):".
    total_txt = re.search(r"^TOTAL:\s*(.+)", texto, re.MULTILINE)
    total = _monto(total_txt.group(1)) if total_txt else None
    if not (numero and fecha and total is not None):
        return None
    return Factura(numero=numero.group(1), fecha=fecha.group(1), total=total)


def construir_paquete(caso: str) -> Paquete:
    """Arma el Paquete del caso leyendo sus fixtures reales."""
    carpeta = SOLICITUDES / caso
    faltantes: list[str] = []

    correo = Correo.model_validate(_leer_json(carpeta / "correo.json"))
    solicitud = Solicitud.model_validate(_leer_json(carpeta / "solicitud.json"))

    # `Aprobacion` es un modelo DERIVADO: `aprobado` y `texto` no existen en el
    # fixture, se calculan. Por eso se construye campo por campo y no con
    # `model_validate` del dict crudo, que falla (defecto D1 del QA de Fase 0).
    aprobacion = None
    ruta_aprobacion = carpeta / "aprobacion.json"
    if ruta_aprobacion.exists():
        crudo = _leer_json(ruta_aprobacion)
        cuerpo = str(crudo["cuerpo"])
        aprobacion = Aprobacion(
            de=str(crudo["de"]),
            fecha=str(crudo["fecha"]),
            aprobado="aprobado" in cuerpo.lower(),
            texto=cuerpo,
        )
    else:
        faltantes.append("aprobacion.json")

    cotizacion = None
    ruta_cotizacion = carpeta / "cotizacion.txt"
    if ruta_cotizacion.exists():
        cotizacion = _parsear_cotizacion(ruta_cotizacion.read_text(encoding="utf-8"))
    else:
        faltantes.append("cotizacion.txt")

    factura = None
    ruta_factura = carpeta / "factura.txt"
    if ruta_factura.exists():
        factura = _parsear_factura(ruta_factura.read_text(encoding="utf-8"))

    return Paquete(
        caso=caso,
        correo=correo,
        solicitud=solicitud,
        cotizacion=cotizacion,
        aprobacion=aprobacion,
        factura=factura,
        faltantes=faltantes,
    )
