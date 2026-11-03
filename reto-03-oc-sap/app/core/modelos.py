"""Modelos de dominio del agente de Órdenes de Compra.

Esta es la pieza que sustituye a `zod` del PRD (ADR-001): Pydantic v2 valida los
argumentos de las herramientas y el payload de la OC, y genera el JSON Schema que
consume el tool-calling.

Convenciones del módulo:

- Los montos son `int` (pesos colombianos, sin decimales). El único `float` del
  dominio es `IndicadorIva.tasa`.
- Las fechas quedan como `str` (`YYYY-MM-DD` o ISO 8601). Comparar fechas es asunto
  de los controles RC8 y RC9, no de los tipos.
- Todos los modelos prohíben campos extra (`extra="forbid"`), salvo `Solicitud` y
  `Correo`, que los ignoran porque sus fixtures de entrada traen campos ajenos al
  dominio (`nota_fixture`, `adjuntos`, `cuerpo`).
- Los modelos **derivados** —`Aprobacion`, `Cotizacion`, `Factura`— mantienen
  `extra="forbid"` porque no son la forma del fixture: tienen campos calculados
  (`aprobado`, `texto`) que no existen en el archivo. Se construyen campo por
  campo, nunca con `model_validate` del dict crudo; `tests/test_modelos.py` lo
  fija con tests de regresión.
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Moneda = Literal["COP", "USD"]
Unidad = Literal["UN", "H", "MES"]
TipoVeredicto = Literal["ok", "bloqueo", "confirmacion", "derivado", "no_evaluable"]


# ── Entrada ────────────────────────────────────────────────────────────────


class Solicitud(BaseModel):
    # extra="ignore": los fixtures de solicitud traen campos extra ajenos al dominio.
    model_config = ConfigDict(extra="ignore")

    solicitud_id: str
    solicitante: str
    proveedor_nombre: str
    proveedor_nit: str | None = None       # ausente en sol-006
    descripcion: str
    centro_costo: str
    subarea: str
    cantidad: int
    valor_unitario: int
    valor_total: int
    moneda: str
    indicador_iva: str | None = None       # ausente en sol-006 → RC6
    condiciones_pago: str | None = None    # ausente en sol-006 → RC7
    fecha_solicitud: str                   # YYYY-MM-DD


class Correo(BaseModel):
    # `extra="ignore"` porque el correo.json real trae ocho campos (para, cuerpo,
    # adjuntos, nota_fixture) y de ellos solo estos cuatro llegan al paquete
    # normalizado del PRD §7.2. Con "forbid" el parseo del fixture fallaría.
    model_config = ConfigDict(extra="ignore")

    id: str
    de: str
    asunto: str
    fecha: str


class Cotizacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    referencia: str | None                 # ej. "COT-TS-2026-0451"
    proveedor: str
    nit: str | None
    total: int
    moneda: str
    validez_hasta: str | None
    item: str | None                       # nombre del ítem, para inferir_unidad
    texto: str


class Aprobacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    de: str
    fecha: str
    aprobado: bool                         # el cuerpo contiene "Aprobado"
    texto: str


class Factura(BaseModel):
    model_config = ConfigDict(extra="forbid")

    numero: str
    fecha: str
    total: int


class Paquete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caso: str
    correo: Correo
    solicitud: Solicitud
    cotizacion: Cotizacion | None
    aprobacion: Aprobacion | None
    factura: Factura | None
    faltantes: list[str] = Field(default_factory=list)   # HU-1: adjuntos ausentes


# ── Maestros ───────────────────────────────────────────────────────────────


class Proveedor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo_sap: str
    nit: str
    nombre: str
    condiciones_pago_default: str
    indicador_iva_default: str
    activo: bool


class Aprobador(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    nombre: str
    tope: int


class CentroCosto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    centro_costo: str
    nombre: str
    subareas: list[str]
    aprobadores: list[Aprobador]


class IndicadorIva(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    descripcion: str
    tasa: float            # único float del dominio: es una tasa, no dinero


class CondicionPago(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    descripcion: str
    dias: int


class Maestros(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proveedores: list[Proveedor]
    centros_costo: list[CentroCosto]
    indicadores_iva: list[IndicadorIva]
    condiciones_pago: list[CondicionPago]


# ── Validación ─────────────────────────────────────────────────────────────


class Veredicto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str                            # "RC1" … "RC10"
    tipo: TipoVeredicto
    detalle: str                           # frase legible para el usuario
    datos: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ResultadoValidacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apta: bool
    bloqueos: list[Veredicto] = Field(default_factory=list)
    confirmaciones: list[Veredicto] = Field(default_factory=list)
    derivados: dict[str, str] = Field(default_factory=dict)
    retroactiva: bool = False
    veredictos: list[Veredicto] = Field(default_factory=list)   # los 10, para auditoría


# ── Payload de la OC (PRD §7.4) ────────────────────────────────────────────


class ReferenciaOC(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solicitud_id: str
    correo_id: str
    cotizacion_ref: str | None


class ProveedorOC(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo_sap: str
    nit: str
    nombre: str


class AprobadorOC(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    fecha_aprobacion: str
    evidencia_sha256: str


class PosicionOC(BaseModel):
    model_config = ConfigDict(extra="forbid")

    numero: int
    descripcion: str = Field(max_length=40)     # límite de texto breve de SAP · I1 · ADR-004
    cantidad: int
    unidad: Unidad
    precio_unitario: int
    centro_costo: str
    subarea: str
    indicador_iva: str


class ExcepcionOC(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    detalle: str
    # Default None: una excepción nace sin confirmar. Al confirmarse se escribe
    # el session_id de quien lo hizo (ADR-006).
    confirmado_por: str | None = None


class OrdenCompra(BaseModel):
    model_config = ConfigDict(extra="forbid")

    referencia: ReferenciaOC
    sociedad: Literal["1000"] = "1000"                  # I4
    organizacion_compras: Literal["1000"] = "1000"      # I4
    proveedor: ProveedorOC
    moneda: Moneda
    condiciones_pago: str
    aprobador: AprobadorOC
    posiciones: list[PosicionOC] = Field(min_length=1)
    excepciones: list[ExcepcionOC] = Field(default_factory=list)


# ── Resultado uniforme de herramienta (C3) ─────────────────────────────────


def ok(data: object) -> str:
    """Serializa una respuesta exitosa de herramienta: {"ok": true, "data": ...}

    Devuelve un **string**, no un dict: el PRD §6.2 exige que `execute` entregue
    JSON serializado. `ensure_ascii=False` conserva las tildes del dominio
    ("Papelería") legibles en el log y en el chat.
    """
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def error(mensaje: str) -> str:
    """Serializa un fallo de herramienta: {"ok": false, "error": "..."}

    Ninguna herramienta lanza excepciones (C3): el error viaja en el string.
    """
    return json.dumps({"ok": False, "error": mensaje}, ensure_ascii=False)
