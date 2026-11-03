"""Implementación local del adaptador SAP sobre archivos.

`SapMock` sustituye al SAP real: en lugar de OData o RFC escribe una línea JSON
por orden en `out/sap/ordenes.jsonl`. Ese archivo es a la vez el "libro de
órdenes" y la fuente de la numeración y de la idempotencia (C10).

El destino de escritura **siempre** sale de `ruta_salida` (mitigación del defecto
D7 del informe de QA de la Fase 0): nunca se arma `raiz / "out" / ...` a mano,
porque `ruta_salida` es el único punto que verifica que el destino cae dentro de
`out/`.

Los métodos son `async` porque el PRD §7.4 los declara `Promise<...>`. El mock no
hace I/O asíncrono real, pero la firma no debe cambiar cuando se sustituya por el
adaptador OData o RFC de producción.

El `Any` de los registros del `.jsonl` es la frontera de deserialización, igual
que en `app/core/io.py`: una línea del archivo no tiene esquema hasta que se
inspecciona (ADR-009).
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

from app.core.io import agregar_linea_jsonl, leer_texto, ruta_salida
from app.core.modelos import Maestros, OrdenCompra
from app.core.normalizar import normalizar_nit
from app.sap.adapter import SapAdapter

NUMERO_INICIAL = 4500000001


class SapMock:
    """SAP simulado sobre `out/sap/ordenes.jsonl`."""

    def __init__(self, raiz: Path, maestros: Maestros) -> None:
        self.raiz = raiz
        self.maestros = maestros

    # ── Rutas ──────────────────────────────────────────────────────────────

    def _ruta_ordenes(self) -> Path:
        """Único destino de lectura y escritura de órdenes.

        Pasa por `ruta_salida` para que el archivo quede bajo `out/sap/` por
        construcción y no por convención (mitigación de D7).
        """
        return ruta_salida(self.raiz, "sap", "ordenes.jsonl")

    # ── Lectura del libro de órdenes ───────────────────────────────────────

    def _leer_registros(self) -> list[dict[str, Any]]:
        """Devuelve las órdenes ya escritas, en orden de escritura.

        Una línea corrupta se ignora, no revienta: el archivo es un append-only
        que puede quedar truncado si el proceso muere a mitad de escritura, y una
        orden ilegible no debe impedir leer las demás ni crear nuevas.
        """
        contenido = leer_texto(self._ruta_ordenes())
        if contenido is None:
            return []

        registros: list[dict[str, Any]] = []
        for linea in contenido.splitlines():
            if not linea.strip():
                continue
            try:
                registro: Any = json.loads(linea)
            except json.JSONDecodeError:
                continue  # línea corrupta: se ignora
            if isinstance(registro, dict):
                registros.append(registro)
        return registros

    # ── Interfaz SapAdapter (PRD §7.4) ─────────────────────────────────────

    async def consultar_proveedor(self, nit: str) -> dict[str, object] | None:
        """Cruza por NIT normalizado contra `maestros.proveedores`.

        La normalización es la que hace funcionar el cruce con los tres formatos
        de NIT de los fixtures ("900.555.111-2", "900555111") · RC1 · ADR-008.
        """
        buscado = normalizar_nit(nit)
        if buscado is None:
            return None

        for proveedor in self.maestros.proveedores:
            if normalizar_nit(proveedor.nit) == buscado:
                return {"codigo_sap": proveedor.codigo_sap, "activo": proveedor.activo}
        return None

    async def buscar_orden_por_referencia(self, solicitud_id: str) -> dict[str, str] | None:
        """Primera orden cuya `referencia.solicitud_id` coincida, o None.

        Si el archivo no existe, None. Es la consulta que hace idempotente a
        `crear_orden` (C10).
        """
        for registro in self._leer_registros():
            referencia = registro.get("referencia")
            if not isinstance(referencia, dict):
                continue
            if referencia.get("solicitud_id") != solicitud_id:
                continue
            numero = registro.get("numero_oc")
            if isinstance(numero, str):
                return {"numero_oc": numero}
        return None

    async def crear_orden(self, orden: OrdenCompra) -> dict[str, str]:
        """Crea la OC, o devuelve la existente si ya se creó (C10).

        Crear dos veces el mismo `solicitud_id` devuelve el mismo número y deja
        **una sola** línea en `ordenes.jsonl`: nunca una segunda OC.

        La bandera `idempotente` no viaja aquí: el PRD §7.4 fija el retorno en
        `{numero_oc, fecha}`. La calcula `oc_crear` (T2.2) viendo si la búsqueda
        previa encontró algo.
        """
        existente = await self.buscar_orden_por_referencia(orden.referencia.solicitud_id)
        if existente is not None:
            return {"numero_oc": existente["numero_oc"], "fecha": _fecha_hoy()}

        registros = self._leer_registros()
        # Numeración secuencial desde 4500000001 en una corrida limpia (I2).
        # Se cuenta sobre las líneas válidas, no sobre las totales: una línea
        # corrupta no debe consumir un número de OC.
        numero = NUMERO_INICIAL + len(registros)
        fecha = _fecha_hoy()

        agregar_linea_jsonl(
            self._ruta_ordenes(),
            {
                "numero_oc": str(numero),
                "fecha": fecha,
                "referencia": orden.referencia.model_dump(mode="json"),
                "orden": orden.model_dump(mode="json"),
            },
        )
        return {"numero_oc": str(numero), "fecha": fecha}


def _fecha_hoy() -> str:
    """Fecha del día en ISO (`YYYY-MM-DD`), como el resto del dominio."""
    return date.today().isoformat()


def _verificar_contrato(mock: SapMock) -> SapAdapter:
    """Aserción de tipado estructural: `SapMock` satisface `SapAdapter`.

    `Protocol` no obliga a heredar, así que nada garantiza el contrato salvo una
    asignación como esta: si una firma del mock se desviara de la interfaz del
    PRD §7.4, `mypy --strict` falla aquí y no en el consumidor.
    """
    adaptador: SapAdapter = mock
    return adaptador
