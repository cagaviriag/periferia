"""Interfaz del adaptador SAP (PRD §7.4).

La interfaz se escribe textual del PRD: tres métodos, con sus mismos nombres y la
misma forma de entrada/salida. Existe para que el adaptador real de producción
(OData o RFC, diseñado en `SOLUCION.md` §7.5) sea sustituible sin tocar las
herramientas que la consumen.

Se usa `Protocol` y no `ABC` a propósito: es tipado estructural. `SapMock` no
hereda de nada — cumple el contrato por tener los métodos con las firmas
correctas, y `mypy --strict` lo verifica en la aserción del final de `mock.py`.

Los métodos son `async` porque el PRD los declara `Promise<...>`. El mock no hace
I/O asíncrono real, pero la firma no debe cambiar cuando se sustituya por el
adaptador OData o RFC de producción, que sí será asíncrono.
"""

from typing import Protocol

from app.core.modelos import OrdenCompra


class SapAdapter(Protocol):
    async def consultar_proveedor(self, nit: str) -> dict[str, object] | None:
        """{"codigo_sap": str, "activo": bool} o None."""

    async def crear_orden(self, orden: OrdenCompra) -> dict[str, str]:
        """{"numero_oc": str, "fecha": str}."""

    async def buscar_orden_por_referencia(self, solicitud_id: str) -> dict[str, str] | None:
        """{"numero_oc": str} o None."""
