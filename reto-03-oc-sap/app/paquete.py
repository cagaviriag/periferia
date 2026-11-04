"""Lectura del caso: el `Paquete` del PRD §7.2, los maestros y los diez controles.

Es la capa más baja del flujo y la única que toca `fixtures/`. Las funciones de
lectura devuelven modelos de `app/core/modelos.py` y **sí lanzan** `ValueError`
cuando un fixture es ilegible: convertir esa excepción en
`{"ok": false, "error": ...}` es trabajo de `app/tools/oc.py` (C3).

`validar` es la excepción: devuelve ya el string JSON del contrato, porque sus
dos desenlaces —el paquete que mandó el modelo es de otro caso, o el resultado de
los diez controles— son respuestas de negocio para el usuario, no excepciones.

Separado de `app/tools/oc.py` en la rama `refactor/langchain`: lo que antes era un
módulo de 955 líneas mezclaba el contrato de la herramienta, el esquema de
argumentos y esta lectura de fixtures, que no tiene nada que ver con LangChain ni
con ningún framework de agentes.

El `Any` de las firmas es la frontera de deserialización, igual que en
`app/core/io.py`: el JSON de los fixtures no tiene esquema hasta que Pydantic lo
valida (ADR-009).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.controles.evaluar import evaluar_controles
from app.core.io import leer_json, leer_texto, ruta_fixture
from app.core.log import registrar_control
from app.core.modelos import (
    Aprobacion,
    Correo,
    Cotizacion,
    Factura,
    Maestros,
    Paquete,
    ResultadoValidacion,
    Solicitud,
    error,
    ok,
)
from app.respuesta import codigos
from app.tools.parseo import parsear_cotizacion, parsear_factura

CARPETA_CASOS = ("reto-03", "solicitudes")
CARPETA_MAESTROS = ("reto-03", "maestros")

# Nombre del campo de `Maestros` → archivo del maestro.
ARCHIVOS_MAESTROS = {
    "proveedores": "proveedores.json",
    "centros_costo": "centros-costo.json",
    "indicadores_iva": "indicadores-iva.json",
    "condiciones_pago": "condiciones-pago.json",
}


# ── Rutas ──────────────────────────────────────────────────────────────────


def casos_disponibles(raiz: Path) -> list[str]:
    """Los casos que existen en `fixtures/reto-03/solicitudes/`, ordenados."""
    carpeta = ruta_fixture(raiz, *CARPETA_CASOS)
    if not carpeta.is_dir():
        return []
    return sorted(sub.name for sub in carpeta.iterdir() if sub.is_dir())


def carpeta_del_caso(raiz: Path, caso: str) -> Path:
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


def ruta_legible(raiz: Path, destino: Path) -> str:
    """Ruta relativa a la raíz del proyecto, ej. `out/sol-001/aprobacion.txt`.

    No se devuelve la ruta absoluta: es información del servidor que no aporta
    nada al modelo ni al chat (PRD §8 Seguridad).
    """
    try:
        return destino.relative_to(raiz.resolve()).as_posix()
    except ValueError:
        return destino.as_posix()


# ── Lectura de fixtures ────────────────────────────────────────────────────


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


def leer_adjunto(carpeta: Path, nombre: str) -> dict[str, Any] | None:
    """Como `_leer_objeto`, pero el error dice qué pedirle al solicitante (HU-6)."""
    try:
        return _leer_objeto(carpeta / nombre)
    except Exception as excepcion:
        raise ValueError(
            f"No pude leer {nombre} de {carpeta.name}: {excepcion} "
            f"Pide al solicitante que reenvíe {nombre} del paquete."
        ) from excepcion


def cargar_maestros(raiz: Path) -> Maestros:
    """Los cuatro maestros de `fixtures/reto-03/maestros/`."""
    crudo: dict[str, Any] = {}
    for campo, archivo in ARCHIVOS_MAESTROS.items():
        contenido = leer_json(ruta_fixture(raiz, *CARPETA_MAESTROS, archivo))
        if contenido is None:
            raise ValueError(f"Falta el maestro {archivo} en fixtures/reto-03/maestros/.")
        crudo[campo] = contenido
    return Maestros.model_validate(crudo)


def construir_paquete(raiz: Path, caso: str) -> Paquete:
    """Arma el `Paquete` normalizado del PRD §7.2 desde los fixtures del caso.

    Un adjunto ausente deja su campo en `None` y su nombre en `faltantes`; no
    lanza (HU-1). Lo que sí lanza es un adjunto **ilegible**, porque un JSON
    malformado no es lo mismo que un JSON ausente y el usuario debe saberlo.
    """
    carpeta = carpeta_del_caso(raiz, caso)
    faltantes: list[str] = []

    crudo_correo = leer_adjunto(carpeta, "correo.json")
    if crudo_correo is None:
        raise ValueError(f"El caso {caso} no tiene correo.json; sin el correo no hay paquete.")
    crudo_solicitud = leer_adjunto(carpeta, "solicitud.json")
    if crudo_solicitud is None:
        raise ValueError(
            f"El caso {caso} no tiene solicitud.json. Pide al solicitante el Excel de la solicitud."
        )

    # `Correo` y `Solicitud` admiten extras (`extra="ignore"`): sus fixtures traen
    # campos ajenos al dominio (`cuerpo`, `adjuntos`, `nota_fixture`).
    correo = Correo.model_validate(crudo_correo)
    solicitud = Solicitud.model_validate(crudo_solicitud)

    aprobacion = _armar_aprobacion(leer_adjunto(carpeta, "aprobacion.json"))
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


def validar_caso(raiz: Path, caso: str) -> tuple[Paquete, Maestros, ResultadoValidacion]:
    """Lee el caso de los fixtures y corre los diez controles.

    Es el punto único de verdad de la validación: lo usan `oc_validar`,
    `oc_construir_payload` y `oc_crear`. Que las tres partan de aquí —y no del
    paquete que mande el modelo— es lo que hace cumplible CA2.
    """
    paquete = construir_paquete(raiz, caso)
    maestros = cargar_maestros(raiz)
    return paquete, maestros, evaluar_controles(paquete, maestros)


def validar(raiz: Path, caso: str, recibido_crudo: dict[str, Any] | None) -> str:
    """Lo que responde `oc_validar`. No reimplementa ningún control: delega.

    El `paquete` que manda el modelo se valida como esquema —para devolverle un
    error claro si envió basura— pero **no** es lo que se evalúa: los controles
    corren sobre el paquete releído de los fixtures. Si el modelo alterara un
    monto, la validación no cambiaría (CA2).
    """
    if recibido_crudo is not None:
        recibido = Paquete.model_validate(recibido_crudo)
        if recibido.caso != caso:
            return error(
                f"El paquete recibido es del caso {recibido.caso!r} y pediste validar "
                f"{caso!r}. Vuelve a llamar a oc_leer_paquete."
            )

    paquete, _maestros, resultado = validar_caso(raiz, caso)

    # HU-5 exige una fila en control.csv por **cada intento: exitoso, bloqueado
    # o pendiente**. Un caso con bloqueos muere aquí: nunca llega a `oc_crear`,
    # porque un bloqueo no es confirmable y el agente no tiene nada que
    # intentar. Si no se registrara en este punto, auditoría no vería que el
    # caso se procesó y se rechazó — justamente lo que el control debe medir.
    if resultado.bloqueos:
        registrar_control(
            raiz,
            solicitud_id=paquete.solicitud.solicitud_id,
            resultado="bloqueada",
            numero_oc=None,
            retroactiva=resultado.retroactiva,
            bloqueos=codigos(resultado.bloqueos),
            confirmaciones=codigos(resultado.confirmaciones),
        )

    return ok(
        {
            "caso": caso,
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
