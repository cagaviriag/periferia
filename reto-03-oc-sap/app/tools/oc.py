"""Las cinco herramientas P0 del contrato del PRD §6.2.

Son la **única** fuente de valores que el agente puede afirmar (CA2): el modelo
orquesta y redacta, nunca calcula.

Tres reglas que gobiernan todo el módulo:

1. **Toda herramienta devuelve un `str` con JSON** — `{"ok": true, "data": ...}` o
   `{"ok": false, "error": ...}` — con `ok()` y `error()` de `app/core/modelos.py`.
   Nunca un dict, nunca un modelo.

2. **Ninguna lanza** (C3). Cada herramienta pública es un envoltorio de seis
   líneas sobre una función privada que sí puede lanzar. El `except` es
   `Exception`, **no** `ValueError`: `leer_json` y `leer_texto` lanzan también
   `IsADirectoryError` y `PermissionError`, que no son `ValueError`, y un
   `except ValueError` las dejaría escapar (defecto **D9** del QA de la Fase 0).

3. **Todo destino de escritura sale de `ruta_salida`** (mitigación del defecto
   **D7**): nunca se arma `raiz / "out" / ...` a mano, porque `ruta_salida` es el
   único punto que verifica que el destino cae dentro de `out/`.

Orden de llamada obligatorio: `oc_generar_evidencia` va **antes** de
`oc_construir_payload`, porque el payload consume el `sha256` de la evidencia.

El `Any` de las firmas es la frontera de deserialización, igual que en
`app/core/io.py`: los argumentos que manda el modelo y el JSON de los fixtures no
tienen esquema hasta que Pydantic los valida (ADR-009).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.controles.evaluar import evaluar_controles
from app.controles.resolutores import resolver_proveedor
from app.core.io import (
    escribir_json,
    escribir_texto,
    leer_json,
    leer_texto,
    ruta_fixture,
    ruta_salida,
)
from app.core.log import ResultadoIntento, registrar_control, registrar_herramienta
from app.core.modelos import (
    Aprobacion,
    Correo,
    Cotizacion,
    Factura,
    Maestros,
    OrdenCompra,
    Paquete,
    ResultadoValidacion,
    Solicitud,
    Veredicto,
    error,
    ok,
)
from app.core.normalizar import inferir_unidad, truncar_descripcion
from app.sap.adapter import SapAdapter
from app.sap.mock import SapMock
from app.tools.parseo import parsear_cotizacion, parsear_factura

# ── Constantes ─────────────────────────────────────────────────────────────

CARPETA_CASOS = ("reto-03", "solicitudes")
CARPETA_MAESTROS = ("reto-03", "maestros")

# Nombre del campo de `Maestros` → archivo del maestro.
ARCHIVOS_MAESTROS = {
    "proveedores": "proveedores.json",
    "centros_costo": "centros-costo.json",
    "indicadores_iva": "indicadores-iva.json",
    "condiciones_pago": "condiciones-pago.json",
}

ARCHIVO_EVIDENCIA = "aprobacion.txt"
ARCHIVO_TRAZABILIDAD = "trazabilidad.json"

# Invariante I5: las posiciones van 10, 20, 30… El modelo `PosicionOC` no lo
# valida (es un `int` cualquiera), así que es responsabilidad de esta
# herramienta (hallazgo H2 del plan).
PASO_POSICION = 10

# Límite del mensaje de error que se le devuelve al modelo: una `ValidationError`
# de Pydantic con veinte campos no aporta nada después de las primeras líneas y
# consume contexto (C6).
LARGO_MAXIMO_ERROR = 500


# ── Contexto y contrato de herramienta (PRD §6.2) ──────────────────────────


@dataclass(frozen=True)
class Contexto:
    """Lo que el ciclo del agente inyecta en cada llamada.

    `raiz` es la raíz del proyecto: todas las rutas se resuelven desde aquí y
    ninguna herramienta acepta rutas absolutas del modelo. `session_id` es la
    identidad que queda en `excepciones[].confirmado_por` (ADR-006).
    """

    raiz: Path
    session_id: str


@dataclass(frozen=True)
class Herramienta:
    """Los tres miembros del contrato del PRD §6.2, más el nombre que ve el modelo.

    `description` es lo único que el modelo lee para decidir cuándo llamarla.
    `args` es el esquema Pydantic que sustituye a `zod` (ADR-001) y del que sale
    el JSON Schema del tool-calling. `execute` recibe los argumentos **crudos**
    del modelo, los valida contra `args` y devuelve el string JSON.
    """

    nombre: str
    description: str
    args: type[BaseModel]
    execute: Callable[[dict[str, Any], Contexto], Awaitable[str]]


# ── Esquemas de argumentos ─────────────────────────────────────────────────

_DESCRIPCION_CASO = "Nombre de la carpeta del caso en fixtures/reto-03/solicitudes/, ej. 'sol-001'"


class ArgsCaso(BaseModel):
    """Argumentos de las dos herramientas que solo necesitan el caso."""

    model_config = ConfigDict(extra="forbid")

    caso: str = Field(description=_DESCRIPCION_CASO)


class ArgsValidar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caso: str = Field(description=_DESCRIPCION_CASO)
    paquete: dict[str, Any] | None = Field(
        default=None,
        description="Paquete devuelto por oc_leer_paquete. Opcional: la validación se "
        "hace siempre sobre los fixtures del caso.",
    )


class ArgsConstruirPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caso: str = Field(description=_DESCRIPCION_CASO)
    paquete: dict[str, Any] | None = Field(
        default=None, description="Paquete devuelto por oc_leer_paquete."
    )
    derivados: dict[str, str] | None = Field(
        default=None,
        description="Valores derivados que devolvió oc_validar, ej. "
        '{"indicador_iva": "C1", "condiciones_pago": "Z030"}.',
    )


class ArgsCrear(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caso: str = Field(description=_DESCRIPCION_CASO)
    payload: dict[str, Any] = Field(
        description="El payload de OrdenCompra que devolvió oc_construir_payload, sin modificar."
    )
    confirmado: bool = Field(
        default=False,
        description="True solo si el usuario confirmó explícitamente las excepciones "
        "en su último mensaje. No levanta bloqueos.",
    )


# ── Lectura de fixtures ────────────────────────────────────────────────────


def casos_disponibles(raiz: Path) -> list[str]:
    """Los casos que existen en `fixtures/reto-03/solicitudes/`, ordenados."""
    carpeta = ruta_fixture(raiz, *CARPETA_CASOS)
    if not carpeta.is_dir():
        return []
    return sorted(sub.name for sub in carpeta.iterdir() if sub.is_dir())


def _carpeta_del_caso(raiz: Path, caso: str) -> Path:
    """Carpeta del caso, validando contra la lista de casos que existen.

    La comprobación es una **lista blanca**, no un saneamiento de la cadena: un
    `caso="../../etc"` o `caso="/etc"` simplemente no está en la lista y se
    rechaza con el mismo mensaje que un caso mal escrito. Es la defensa contra
    el recorrido de rutas en el lado de la lectura (C1).
    """
    disponibles = casos_disponibles(raiz)
    if caso not in disponibles:
        raise ValueError(
            f"No existe el caso {caso!r}. Casos disponibles: {', '.join(disponibles) or 'ninguno'}."
        )
    return ruta_fixture(raiz, *CARPETA_CASOS, caso)


def _leer_objeto(ruta: Path) -> dict[str, Any] | None:
    """Lee un JSON que debe ser un objeto. None si el archivo no existe."""
    contenido = leer_json(ruta)
    if contenido is None:
        return None
    if not isinstance(contenido, dict):
        raise ValueError(  # noqa: TRY004
            f"{ruta.name} debe ser un objeto JSON, llegó {type(contenido).__name__}."
        )
    return contenido


def _leer_adjunto(carpeta: Path, nombre: str) -> dict[str, Any] | None:
    """Como `_leer_objeto`, pero el error dice qué pedirle al solicitante (HU-6)."""
    try:
        return _leer_objeto(carpeta / nombre)
    except Exception as excepcion:
        raise ValueError(
            f"No pude leer {nombre} de {carpeta.name}: {excepcion} "
            f"Pide al solicitante que reenvíe {nombre} del paquete."
        ) from excepcion


def _cargar_maestros(raiz: Path) -> Maestros:
    """Los cuatro maestros de `fixtures/reto-03/maestros/`."""
    crudo: dict[str, Any] = {}
    for campo, archivo in ARCHIVOS_MAESTROS.items():
        contenido = leer_json(ruta_fixture(raiz, *CARPETA_MAESTROS, archivo))
        if contenido is None:
            raise ValueError(f"Falta el maestro {archivo} en fixtures/reto-03/maestros/.")
        crudo[campo] = contenido
    return Maestros.model_validate(crudo)


def _construir_paquete(raiz: Path, caso: str) -> Paquete:
    """Arma el `Paquete` normalizado del PRD §7.2 desde los fixtures del caso.

    Un adjunto ausente deja su campo en `None` y su nombre en `faltantes`; no
    lanza (HU-1). Lo que sí lanza es un adjunto **ilegible**, porque un JSON
    malformado no es lo mismo que un JSON ausente y el usuario debe saberlo.
    """
    carpeta = _carpeta_del_caso(raiz, caso)
    faltantes: list[str] = []

    crudo_correo = _leer_adjunto(carpeta, "correo.json")
    if crudo_correo is None:
        raise ValueError(f"El caso {caso} no tiene correo.json; sin el correo no hay paquete.")
    crudo_solicitud = _leer_adjunto(carpeta, "solicitud.json")
    if crudo_solicitud is None:
        raise ValueError(
            f"El caso {caso} no tiene solicitud.json. Pide al solicitante el Excel de la solicitud."
        )

    # `Correo` y `Solicitud` admiten extras (`extra="ignore"`): sus fixtures traen
    # campos ajenos al dominio (`cuerpo`, `adjuntos`, `nota_fixture`).
    correo = Correo.model_validate(crudo_correo)
    solicitud = Solicitud.model_validate(crudo_solicitud)

    aprobacion = _armar_aprobacion(_leer_adjunto(carpeta, "aprobacion.json"))
    if aprobacion is None:
        faltantes.append("aprobacion.json")

    cotizacion = _armar_cotizacion(leer_texto(carpeta / "cotizacion.txt"))
    if cotizacion is None:
        faltantes.append("cotizacion.txt")

    # La factura solo existe en sol-005 y su ausencia es lo normal: no se reporta
    # como faltante porque no falta nada (RC8 simplemente no aplica).
    factura = _armar_factura(leer_texto(carpeta / "factura.txt"))

    return Paquete(
        caso=caso,
        correo=correo,
        solicitud=solicitud,
        cotizacion=cotizacion,
        aprobacion=aprobacion,
        factura=factura,
        faltantes=faltantes,
    )


def _armar_aprobacion(crudo: dict[str, Any] | None) -> Aprobacion | None:
    """Construye `Aprobacion` **campo por campo** desde `aprobacion.json`.

    `Aprobacion` es un modelo **derivado**: `aprobado` y `texto` no existen en el
    fixture, se calculan desde `cuerpo`. Por eso NO se usa
    `Aprobacion.model_validate(crudo)`, que falla en los 6 casos (defecto **D1**
    del QA de la Fase 0): el dict crudo trae `para`, `cc` y `asunto` —que el
    modelo prohíbe con `extra="forbid"`— y no trae los dos campos calculados.
    """
    if crudo is None:
        return None

    cuerpo = str(crudo.get("cuerpo", ""))
    return Aprobacion(
        de=str(crudo.get("de", "")),
        fecha=str(crudo.get("fecha", "")),
        # RC2 · PRD §7.3: la aprobación vale si el cuerpo dice "Aprobado". Se
        # compara en minúsculas para no depender de cómo lo escribió el líder.
        aprobado="aprobado" in cuerpo.lower(),
        texto=cuerpo,
    )


def _armar_cotizacion(texto: str | None) -> Cotizacion | None:
    """`cotizacion.txt` → `Cotizacion`, o None si falta o no se pudo parsear."""
    if texto is None:
        return None
    campos = parsear_cotizacion(texto)
    if campos is None:
        return None
    return Cotizacion.model_validate(campos)


def _armar_factura(texto: str | None) -> Factura | None:
    """`factura.txt` → `Factura`, o None si falta o no se pudo parsear."""
    if texto is None:
        return None
    campos = parsear_factura(texto)
    if campos is None:
        return None
    return Factura.model_validate(campos)


def _validar_caso(raiz: Path, caso: str) -> tuple[Paquete, Maestros, ResultadoValidacion]:
    """Lee el caso de los fixtures y corre los diez controles.

    Es el punto único de verdad de la validación: lo usan `oc_validar`,
    `oc_construir_payload` y `oc_crear`. Que las tres partan de aquí —y no del
    paquete que mande el modelo— es lo que hace cumplible CA2.
    """
    paquete = _construir_paquete(raiz, caso)
    maestros = _cargar_maestros(raiz)
    return paquete, maestros, evaluar_controles(paquete, maestros)


# ── Utilidades de respuesta ────────────────────────────────────────────────


def _describir(excepcion: Exception) -> str:
    """Mensaje legible de una excepción, con su tipo y recortado."""
    mensaje = " ".join(str(excepcion).split()) or "sin detalle"
    if len(mensaje) > LARGO_MAXIMO_ERROR:
        mensaje = mensaje[:LARGO_MAXIMO_ERROR] + "…"
    return f"{type(excepcion).__name__}: {mensaje}"


def _fue_ok(respuesta: str) -> bool:
    """Lee la bandera `ok` de una respuesta ya serializada. No lanza."""
    try:
        cuerpo = json.loads(respuesta)
    except ValueError:
        return False
    return isinstance(cuerpo, dict) and cuerpo.get("ok") is True


def _resumen(respuesta: str) -> str:
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
        return str(cuerpo.get("error", "error sin detalle"))[:200]

    datos = cuerpo.get("data")
    if not isinstance(datos, dict):
        return "ok"
    escalares = [
        f"{clave}={valor}"
        for clave, valor in datos.items()
        if isinstance(valor, bool | int | str) and len(str(valor)) <= 60
    ]
    return " ".join(escalares) or "ok"


def _codigos(veredictos: list[Veredicto]) -> list[str]:
    """Solo los códigos: es lo que va a `control.csv`."""
    return [veredicto.codigo for veredicto in veredictos]


def _detallar(veredictos: list[Veredicto]) -> str:
    """`"RC1: el proveedor no existe; RC4: ..."` para el mensaje al usuario."""
    return "; ".join(f"{veredicto.codigo}: {veredicto.detalle}" for veredicto in veredictos)


def _ruta_legible(raiz: Path, destino: Path) -> str:
    """Ruta relativa a la raíz del proyecto, ej. `out/sol-001/aprobacion.txt`.

    No se devuelve la ruta absoluta: es información del servidor que no aporta
    nada al modelo ni al chat (PRD §8 Seguridad).
    """
    try:
        return destino.relative_to(raiz.resolve()).as_posix()
    except ValueError:
        return destino.as_posix()


# ── 1 · oc_leer_paquete (HU-1) ─────────────────────────────────────────────

DESCRIPCION_LEER_PAQUETE = (
    "Lee el paquete de compra de un caso (correo, solicitud, cotización, aprobación y "
    "factura si existe) y devuelve sus datos normalizados, incluidos los adjuntos que faltan."
)


async def oc_leer_paquete(args: ArgsCaso, ctx: Contexto) -> str:
    """Devuelve el paquete normalizado del caso. No lanza (C3)."""
    try:
        respuesta = _leer_paquete(args, ctx)
    except Exception as excepcion:  # noqa: BLE001
        respuesta = error(_describir(excepcion))
    registrar_herramienta(
        ctx.raiz, "oc_leer_paquete", _fue_ok(respuesta), _resumen(respuesta), args.caso
    )
    return respuesta


def _leer_paquete(args: ArgsCaso, ctx: Contexto) -> str:
    paquete = _construir_paquete(ctx.raiz, args.caso)
    return ok(paquete.model_dump(mode="json"))


# ── 2 · oc_validar (HU-2) ─────────────────────────────────────────────────

DESCRIPCION_VALIDAR = (
    "Evalúa los 10 controles del caso y devuelve si la orden es apta, sus bloqueos, "
    "las confirmaciones que requiere, los valores derivados y si es retroactiva."
)


async def oc_validar(args: ArgsValidar, ctx: Contexto) -> str:
    """Corre los diez controles del caso. No lanza (C3)."""
    try:
        respuesta = _validar(args, ctx)
    except Exception as excepcion:  # noqa: BLE001
        respuesta = error(_describir(excepcion))
    registrar_herramienta(
        ctx.raiz, "oc_validar", _fue_ok(respuesta), _resumen(respuesta), args.caso
    )
    return respuesta


def _validar(args: ArgsValidar, ctx: Contexto) -> str:
    """No reimplementa ningún control: delega en `evaluar_controles`.

    El `paquete` que manda el modelo se valida como esquema —para devolverle un
    error claro si envió basura— pero **no** es lo que se evalúa: los controles
    corren sobre el paquete releído de los fixtures. Si el modelo alterara un
    monto, la validación no cambiaría (CA2).
    """
    if args.paquete is not None:
        recibido = Paquete.model_validate(args.paquete)
        if recibido.caso != args.caso:
            return error(
                f"El paquete recibido es del caso {recibido.caso!r} y pediste validar "
                f"{args.caso!r}. Vuelve a llamar a oc_leer_paquete."
            )

    paquete, _maestros, resultado = _validar_caso(ctx.raiz, args.caso)

    # HU-5 exige una fila en control.csv por **cada intento: exitoso, bloqueado
    # o pendiente**. Un caso con bloqueos muere aquí: nunca llega a `oc_crear`,
    # porque un bloqueo no es confirmable y el agente no tiene nada que
    # intentar. Si no se registrara en este punto, auditoría no vería que el
    # caso se procesó y se rechazó — justamente lo que el control debe medir.
    if resultado.bloqueos:
        registrar_control(
            ctx.raiz,
            solicitud_id=paquete.solicitud.solicitud_id,
            resultado="bloqueada",
            numero_oc=None,
            retroactiva=resultado.retroactiva,
            bloqueos=[veredicto.codigo for veredicto in resultado.bloqueos],
            confirmaciones=[veredicto.codigo for veredicto in resultado.confirmaciones],
        )

    return ok(
        {
            "caso": args.caso,
            "solicitud_id": paquete.solicitud.solicitud_id,
            "apta": resultado.apta,
            "bloqueos": [veredicto.model_dump(mode="json") for veredicto in resultado.bloqueos],
            "confirmaciones": [
                veredicto.model_dump(mode="json") for veredicto in resultado.confirmaciones
            ],
            "derivados": resultado.derivados,
            "retroactiva": resultado.retroactiva,
            # Los diez veredictos completos: es la matriz que muestra la UI.
            "veredictos": [veredicto.model_dump(mode="json") for veredicto in resultado.veredictos],
        }
    )


# ── 3 · oc_generar_evidencia (HU-4) ───────────────────────────────────────

DESCRIPCION_GENERAR_EVIDENCIA = (
    "Escribe el correo de aprobación como archivo de evidencia en out/<caso>/aprobacion.txt "
    "y devuelve su ruta y su sha256. Debe llamarse ANTES de oc_construir_payload."
)


async def oc_generar_evidencia(args: ArgsCaso, ctx: Contexto) -> str:
    """Genera la evidencia de la aprobación. No lanza (C3)."""
    try:
        respuesta = _generar_evidencia(args, ctx)
    except Exception as excepcion:  # noqa: BLE001
        respuesta = error(_describir(excepcion))
    registrar_herramienta(
        ctx.raiz, "oc_generar_evidencia", _fue_ok(respuesta), _resumen(respuesta), args.caso
    )
    return respuesta


def _generar_evidencia(args: ArgsCaso, ctx: Contexto) -> str:
    carpeta = _carpeta_del_caso(ctx.raiz, args.caso)
    crudo = _leer_adjunto(carpeta, "aprobacion.json")
    if crudo is None:
        return error(
            f"El caso {args.caso} no tiene aprobacion.json, así que no hay evidencia que "
            "generar. Pide al solicitante el correo de aprobación de su líder."
        )

    contenido = _texto_evidencia(crudo)
    # D7: el destino sale de `ruta_salida`, nunca de `raiz / "out" / ...`.
    destino = escribir_texto(_ruta_evidencia(ctx.raiz, args.caso), contenido)
    return ok(
        {
            "ruta": _ruta_legible(ctx.raiz, destino),
            # El hash es del contenido exacto escrito, no del fixture: lo que se
            # audita es el archivo de evidencia que queda en out/.
            "sha256": _sha256(contenido),
        }
    )


def _ruta_evidencia(raiz: Path, caso: str) -> Path:
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


def _sha256(contenido: str) -> str:
    """Huella del contenido en UTF-8. Es la que viaja al payload de la OC."""
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


# ── 4 · oc_construir_payload (HU-3) ───────────────────────────────────────

DESCRIPCION_CONSTRUIR_PAYLOAD = (
    "Construye el payload de la orden de compra para SAP y escribe su trazabilidad en "
    "out/<caso>/trazabilidad.json. Requiere haber llamado antes a oc_generar_evidencia."
)


async def oc_construir_payload(args: ArgsConstruirPayload, ctx: Contexto) -> str:
    """Construye el payload de la OC. No lanza (C3)."""
    try:
        respuesta = _construir_payload(args, ctx)
    except Exception as excepcion:  # noqa: BLE001
        respuesta = error(_describir(excepcion))
    registrar_herramienta(
        ctx.raiz, "oc_construir_payload", _fue_ok(respuesta), _resumen(respuesta), args.caso
    )
    return respuesta


def _construir_payload(args: ArgsConstruirPayload, ctx: Contexto) -> str:
    paquete, maestros, resultado = _validar_caso(ctx.raiz, args.caso)
    solicitud = paquete.solicitud

    # El payload consume el sha256 de la evidencia, así que la evidencia tiene
    # que existir primero. No se genera aquí a escondidas: el orden de llamada es
    # parte del proceso que el agente debe seguir y que el chat debe mostrar.
    evidencia = leer_texto(_ruta_evidencia(ctx.raiz, args.caso))
    if evidencia is None:
        return error("genera primero la evidencia: llama a oc_generar_evidencia con este caso.")

    if paquete.aprobacion is None:
        return error(
            f"El caso {args.caso} no tiene aprobación, y la OC exige registrar quién aprobó. "
            "Pide al solicitante el correo de aprobación (RC2)."
        )

    proveedor = resolver_proveedor(paquete, maestros)
    if proveedor is None:
        return error(
            f"El proveedor {solicitud.proveedor_nombre!r} "
            f"(NIT {solicitud.proveedor_nit!r}) no existe en el maestro, así que no hay "
            "código SAP con el que construir la OC. Es el bloqueo RC1."
        )

    derivados = _derivados_confiables(resultado, args.derivados)
    if isinstance(derivados, str):
        return derivados

    condiciones_pago = solicitud.condiciones_pago or derivados.get("condiciones_pago")
    if not condiciones_pago:
        return error("Falta condiciones_pago y no se pudo derivar del proveedor (RC7).")

    indicador_iva = solicitud.indicador_iva or derivados.get("indicador_iva")
    if not indicador_iva:
        return error("Falta indicador_iva y no se pudo derivar del proveedor (RC6).")

    item_cotizacion = paquete.cotizacion.item if paquete.cotizacion is not None else None

    crudo: dict[str, Any] = {
        "referencia": {
            "solicitud_id": solicitud.solicitud_id,
            "correo_id": paquete.correo.id,
            "cotizacion_ref": (
                paquete.cotizacion.referencia if paquete.cotizacion is not None else None
            ),
        },
        # sociedad y organizacion_compras son constantes del modelo (I4).
        "proveedor": {
            "codigo_sap": proveedor.codigo_sap,
            "nit": proveedor.nit,
            "nombre": proveedor.nombre,
        },
        "moneda": solicitud.moneda,
        "condiciones_pago": condiciones_pago,
        "aprobador": {
            "email": paquete.aprobacion.de,
            "fecha_aprobacion": paquete.aprobacion.fecha,
            "evidencia_sha256": _sha256(evidencia),
        },
        "posiciones": [
            {
                # I5 · H2: la numeración en pasos de 10 la pone esta herramienta,
                # porque `PosicionOC.numero` es un int cualquiera para el modelo.
                "numero": PASO_POSICION,
                # I1 · ADR-004: SAP limita el texto breve a 40 caracteres. La
                # descripción completa queda en trazabilidad.json.
                "descripcion": truncar_descripcion(solicitud.descripcion),
                "cantidad": solicitud.cantidad,
                # ADR-005: la unidad se infiere del ítem de la cotización, que es
                # el texto inequívoco; la descripción es el respaldo.
                "unidad": inferir_unidad(item_cotizacion, solicitud.descripcion),
                # ADR-007: el precio va con IVA incluido, igual que el monto que
                # aprobó el líder y contra el que corrieron RC3, RC5 y RC10.
                "precio_unitario": solicitud.valor_unitario,
                "centro_costo": solicitud.centro_costo,
                "subarea": solicitud.subarea,
                "indicador_iva": indicador_iva,
            }
        ],
        # Una excepción por cada confirmación pendiente. Nacen sin confirmar:
        # `oc_crear` escribe el `confirmado_por` cuando alguien confirma (ADR-006).
        "excepciones": [
            {"codigo": veredicto.codigo, "detalle": veredicto.detalle, "confirmado_por": None}
            for veredicto in resultado.confirmaciones
        ],
    }

    # El payload se valida antes de devolverlo: si una pieza no cumple el esquema
    # de SAP, el error aparece aquí y no al intentar crear la orden.
    orden = OrdenCompra.model_validate(crudo)

    destino = escribir_json(
        ruta_salida(ctx.raiz, args.caso, ARCHIVO_TRAZABILIDAD),
        _trazabilidad(paquete, orden, derivados),
    )
    return ok(
        {
            "payload": orden.model_dump(mode="json"),
            "ruta_trazabilidad": _ruta_legible(ctx.raiz, destino),
            "descripcion_truncada": orden.posiciones[0].descripcion,
        }
    )


def _derivados_confiables(
    resultado: ResultadoValidacion, recibidos: dict[str, str] | None
) -> dict[str, str] | str:
    """Los derivados que calcularon RC6 y RC7, o un error si el modelo los contradice.

    Los valores que se usan son **siempre** los que salieron de los maestros en
    `evaluar_controles`. Los `derivados` del argumento solo se comparan: si el
    modelo manda un `indicador_iva` que ningún control derivó, es un valor
    inventado y se rechaza en lugar de escribirlo en la OC (CA2).
    """
    calculados = dict(resultado.derivados)
    for campo, valor in (recibidos or {}).items():
        if calculados.get(campo) != valor:
            return error(
                f"El derivado {campo}={valor!r} no coincide con el que calculó oc_validar "
                f"({calculados.get(campo)!r}). Usa los derivados que devuelve oc_validar."
            )
    return calculados


def _trazabilidad(
    paquete: Paquete, orden: OrdenCompra, derivados: dict[str, str]
) -> dict[str, Any]:
    """Fuente de cada campo del payload (I7 · ADR-004).

    Responde la pregunta de la auditoría: *¿de dónde salió este valor?* Las
    fuentes posibles son `solicitud`, `correo`, `cotizacion`, `aprobacion`,
    `maestro.<nombre>`, `derivado`, `constante`, `evidencia` y `validacion`.
    """
    posicion = orden.posiciones[0]
    fuente_iva = "derivado" if "indicador_iva" in derivados else "solicitud"
    fuente_pago = "derivado" if "condiciones_pago" in derivados else "solicitud"

    campos: dict[str, dict[str, Any]] = {
        "referencia.solicitud_id": _traza(orden.referencia.solicitud_id, "solicitud"),
        "referencia.correo_id": _traza(orden.referencia.correo_id, "correo"),
        "referencia.cotizacion_ref": _traza(orden.referencia.cotizacion_ref, "cotizacion"),
        "sociedad": _traza(orden.sociedad, "constante"),
        "organizacion_compras": _traza(orden.organizacion_compras, "constante"),
        "proveedor.codigo_sap": _traza(orden.proveedor.codigo_sap, "maestro.proveedores"),
        "proveedor.nit": _traza(orden.proveedor.nit, "maestro.proveedores"),
        "proveedor.nombre": _traza(orden.proveedor.nombre, "maestro.proveedores"),
        "moneda": _traza(orden.moneda, "solicitud"),
        "condiciones_pago": _traza(orden.condiciones_pago, fuente_pago),
        "aprobador.email": _traza(orden.aprobador.email, "aprobacion"),
        "aprobador.fecha_aprobacion": _traza(orden.aprobador.fecha_aprobacion, "aprobacion"),
        "aprobador.evidencia_sha256": _traza(orden.aprobador.evidencia_sha256, "evidencia"),
        "posiciones[0].numero": _traza(posicion.numero, "constante"),
        "posiciones[0].descripcion": _traza(posicion.descripcion, "solicitud"),
        "posiciones[0].cantidad": _traza(posicion.cantidad, "solicitud"),
        "posiciones[0].unidad": _traza(posicion.unidad, "cotizacion"),
        "posiciones[0].precio_unitario": _traza(posicion.precio_unitario, "solicitud"),
        "posiciones[0].centro_costo": _traza(posicion.centro_costo, "solicitud"),
        "posiciones[0].subarea": _traza(posicion.subarea, "solicitud"),
        "posiciones[0].indicador_iva": _traza(posicion.indicador_iva, fuente_iva),
        "excepciones": _traza([e.codigo for e in orden.excepciones], "validacion"),
    }

    # ADR-004: la descripción completa se preserva aquí, porque el payload solo
    # lleva los 40 caracteres que admite el texto breve de SAP.
    campos["posiciones[0].descripcion"]["valor_completo"] = paquete.solicitud.descripcion

    return {
        "caso": paquete.caso,
        "solicitud_id": paquete.solicitud.solicitud_id,
        "descripcion_completa": paquete.solicitud.descripcion,
        "derivados": derivados,
        "campos": campos,
    }


def _traza(valor: object, fuente: str) -> dict[str, Any]:
    """Una entrada de `trazabilidad.json`: el valor y de dónde salió."""
    return {"valor": valor, "fuente": fuente}


# ── 5 · oc_crear (HU-5) ───────────────────────────────────────────────────

DESCRIPCION_CREAR = (
    "Crea la orden de compra en SAP. Rechaza la creación si hay bloqueos, y si hay "
    "excepciones solo crea cuando el usuario ya confirmó (confirmado=true). Es idempotente."
)


async def oc_crear(args: ArgsCrear, ctx: Contexto) -> str:
    """Crea la OC en SAP. No lanza (C3)."""
    try:
        respuesta = await _crear(args, ctx)
    except Exception as excepcion:  # noqa: BLE001
        # I6: un intento que no llegó ni a leerse también deja su fila. Como no
        # hay solicitud_id —el paquete no se pudo armar— se registra el caso.
        registrar_control(ctx.raiz, args.caso, "error", None, False, [], [])
        respuesta = error(_describir(excepcion))
    registrar_herramienta(ctx.raiz, "oc_crear", _fue_ok(respuesta), _resumen(respuesta), args.caso)
    return respuesta


async def _crear(args: ArgsCrear, ctx: Contexto) -> str:
    """Los cinco desenlaces, cada uno con su fila en `control.csv` (I6).

    La validación se **vuelve a correr aquí** sobre los fixtures. No se confía en
    el payload recibido ni en que el modelo diga que ya validó: es la defensa de
    CA2 contra un modelo que afirme un resultado que nunca calculó.
    """
    paquete, maestros, resultado = _validar_caso(ctx.raiz, args.caso)
    solicitud_id = paquete.solicitud.solicitud_id
    bloqueos = _codigos(resultado.bloqueos)
    confirmaciones = _codigos(resultado.confirmaciones)

    def registrar(desenlace: ResultadoIntento, numero_oc: str | None) -> None:
        """Una fila por intento (I6), con el desenlace y los códigos en juego."""
        registrar_control(
            ctx.raiz,
            solicitud_id,
            desenlace,
            numero_oc,
            resultado.retroactiva,
            bloqueos,
            confirmaciones,
        )

    try:
        orden = OrdenCompra.model_validate(args.payload)
    except Exception as excepcion:  # noqa: BLE001
        registrar("error", None)
        return error(
            f"El payload no cumple el esquema de la OC. {_describir(excepcion)} "
            "Reconstrúyelo con oc_construir_payload."
        )

    # El payload debe ser del caso que se dice crear. Sin esta comprobación un
    # payload de otro caso crearía una OC con los controles de este.
    if orden.referencia.solicitud_id != solicitud_id:
        registrar("error", None)
        return error(
            f"El payload es de la solicitud {orden.referencia.solicitud_id!r} y el caso "
            f"{args.caso} corresponde a {solicitud_id!r}. Reconstruye el payload."
        )

    # Un bloqueo NO se levanta con `confirmado=True`: esa es justamente la
    # diferencia entre bloqueo y confirmación (HU-2). sol-002 y sol-003 no crean
    # OC ni con confirmación.
    if resultado.bloqueos:
        registrar("bloqueada", None)
        return error(
            f"No se puede crear la OC de {solicitud_id}: hay bloqueos que requieren "
            f"corrección, no confirmación. {_detallar(resultado.bloqueos)}"
        )

    if resultado.confirmaciones and not args.confirmado:
        registrar("pendiente_confirmacion", None)
        return error(
            f"requiere confirmación del usuario antes de crear la OC de {solicitud_id}: "
            f"{_detallar(resultado.confirmaciones)}"
        )

    if args.confirmado:
        # ADR-006: la única identidad que existe en el sistema es la sesión del
        # chat. Se escribe antes de crear para que quede dentro de la OC guardada.
        for excepcion_oc in orden.excepciones:
            excepcion_oc.confirmado_por = ctx.session_id

    sap: SapAdapter = SapMock(ctx.raiz, maestros)
    # La búsqueda previa es lo que distingue "creada" de "idempotente": el mock
    # devuelve el mismo número en ambos casos (C10), pero el usuario necesita
    # saber que no se creó una segunda OC.
    existente = await sap.buscar_orden_por_referencia(solicitud_id)
    creada = await sap.crear_orden(orden)
    idempotente = existente is not None

    registrar("idempotente" if idempotente else "creada", creada["numero_oc"])
    return ok(
        {
            "numero_oc": creada["numero_oc"],
            "fecha": creada["fecha"],
            "idempotente": idempotente,
            "retroactiva": resultado.retroactiva,
        }
    )


# ── Registro de herramientas (PRD §6.2) ───────────────────────────────────

async def _con_args[Args: BaseModel](
    esquema: type[Args],
    crudo: dict[str, Any],
    ctx: Contexto,
    herramienta: Callable[[Args, Contexto], Awaitable[str]],
) -> str:
    """Valida los argumentos crudos del modelo y ejecuta la herramienta.

    El PRD §6.2 exige que el backend valide los argumentos antes de ejecutar y
    le devuelva el error al modelo. Esta función es ese paso, y tampoco lanza.
    """
    try:
        args = esquema.model_validate(crudo)
    except Exception as excepcion:  # noqa: BLE001
        return error(f"Argumentos inválidos: {_describir(excepcion)}")
    return await herramienta(args, ctx)


async def _ejecutar_leer_paquete(crudo: dict[str, Any], ctx: Contexto) -> str:
    return await _con_args(ArgsCaso, crudo, ctx, oc_leer_paquete)


async def _ejecutar_validar(crudo: dict[str, Any], ctx: Contexto) -> str:
    return await _con_args(ArgsValidar, crudo, ctx, oc_validar)


async def _ejecutar_generar_evidencia(crudo: dict[str, Any], ctx: Contexto) -> str:
    return await _con_args(ArgsCaso, crudo, ctx, oc_generar_evidencia)


async def _ejecutar_construir_payload(crudo: dict[str, Any], ctx: Contexto) -> str:
    return await _con_args(ArgsConstruirPayload, crudo, ctx, oc_construir_payload)


async def _ejecutar_crear(crudo: dict[str, Any], ctx: Contexto) -> str:
    return await _con_args(ArgsCrear, crudo, ctx, oc_crear)


# El orden de la lista es el orden del proceso: leer, validar, evidenciar,
# construir, crear. Es el mismo que el agente debe seguir.
HERRAMIENTAS: list[Herramienta] = [
    Herramienta(
        nombre="oc_leer_paquete",
        description=DESCRIPCION_LEER_PAQUETE,
        args=ArgsCaso,
        execute=_ejecutar_leer_paquete,
    ),
    Herramienta(
        nombre="oc_validar",
        description=DESCRIPCION_VALIDAR,
        args=ArgsValidar,
        execute=_ejecutar_validar,
    ),
    Herramienta(
        nombre="oc_generar_evidencia",
        description=DESCRIPCION_GENERAR_EVIDENCIA,
        args=ArgsCaso,
        execute=_ejecutar_generar_evidencia,
    ),
    Herramienta(
        nombre="oc_construir_payload",
        description=DESCRIPCION_CONSTRUIR_PAYLOAD,
        args=ArgsConstruirPayload,
        execute=_ejecutar_construir_payload,
    ),
    Herramienta(
        nombre="oc_crear",
        description=DESCRIPCION_CREAR,
        args=ArgsCrear,
        execute=_ejecutar_crear,
    ),
]
