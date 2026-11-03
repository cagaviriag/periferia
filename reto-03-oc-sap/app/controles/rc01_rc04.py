"""RC1 (proveedor) y RC4 (subárea). Dos bloqueos que dependen solo de los maestros.

Ambos controles son funciones puras: entran el paquete y los maestros, sale un
`Veredicto`. No hacen I/O y no lanzan excepciones (C3).

Los `detalle` están redactados para una analista de compras: dicen qué pasó y qué
hacer, no vuelcan datos. Los datos van en `datos`, que es lo que consume la
trazabilidad y el CSV de control.
"""

from app.controles.resolutores import resolver_centro, resolver_proveedor
from app.core.modelos import Maestros, Paquete, Veredicto
from app.core.normalizar import normalizar_nit


def rc1_proveedor(paquete: Paquete, maestros: Maestros) -> Veredicto:
    """RC1 · El proveedor debe existir en el maestro y estar activo. Bloqueo."""
    solicitud = paquete.solicitud
    nit_buscado = normalizar_nit(solicitud.proveedor_nit)

    # Qué criterio se usó para cruzar. Importa para la trazabilidad: sol-006 se
    # resuelve por nombre y el usuario debe saber que ese dato no venía en la
    # solicitud (ADR-008).
    cruzado_por = "nit" if nit_buscado is not None else "nombre"

    proveedor = resolver_proveedor(paquete, maestros)

    if proveedor is None:
        if cruzado_por == "nit":
            detalle = (
                f"El proveedor con NIT {solicitud.proveedor_nit} "
                f"({solicitud.proveedor_nombre}) no existe en el maestro de proveedores. "
                "Hay que solicitar su creación en SAP antes de reintentar la orden."
            )
        else:
            detalle = (
                f"La solicitud no informó NIT y el nombre «{solicitud.proveedor_nombre}» "
                "no corresponde a ningún proveedor del maestro. Hay que pedir el NIT al "
                "solicitante o solicitar la creación del proveedor en SAP."
            )
        return Veredicto(
            codigo="RC1",
            tipo="bloqueo",
            detalle=detalle,
            datos={
                "nit_buscado": nit_buscado,
                "nombre_buscado": solicitud.proveedor_nombre,
                "codigo_sap": None,
                "activo": None,
                "cruzado_por": cruzado_por,
            },
        )

    datos: dict[str, str | int | float | bool | None] = {
        "nit_buscado": nit_buscado,
        "nombre_buscado": solicitud.proveedor_nombre,
        "codigo_sap": proveedor.codigo_sap,
        "activo": proveedor.activo,
        "cruzado_por": cruzado_por,
    }

    if not proveedor.activo:
        # Existe pero está inactivo: es un fallo distinto del anterior y la
        # acción del usuario también (reactivar, no crear).
        return Veredicto(
            codigo="RC1",
            tipo="bloqueo",
            detalle=(
                f"El proveedor {proveedor.nombre} (código SAP {proveedor.codigo_sap}) "
                "existe en el maestro pero está inactivo, así que no admite órdenes de "
                "compra. Hay que pedir su reactivación en SAP o cambiar de proveedor."
            ),
            datos=datos,
        )

    return Veredicto(
        codigo="RC1",
        tipo="ok",
        detalle=(
            f"Proveedor {proveedor.nombre} identificado con el código SAP "
            f"{proveedor.codigo_sap} y activo en el maestro (cruzado por {cruzado_por})."
        ),
        datos=datos,
    )


def rc4_subarea(paquete: Paquete, maestros: Maestros) -> Veredicto:
    """RC4 · La subárea debe pertenecer al centro de costo. Bloqueo."""
    solicitud = paquete.solicitud
    centro = resolver_centro(paquete, maestros)

    if centro is None:
        return Veredicto(
            codigo="RC4",
            tipo="bloqueo",
            detalle=(
                f"El centro de costo {solicitud.centro_costo} no existe en el maestro, "
                "así que no se puede verificar a qué área se imputa el gasto. Hay que "
                "confirmar el centro con el solicitante."
            ),
            datos={
                "centro_costo": solicitud.centro_costo,
                "subarea": solicitud.subarea,
                "subareas_validas": None,
            },
        )

    datos: dict[str, str | int | float | bool | None] = {
        "centro_costo": centro.centro_costo,
        "subarea": solicitud.subarea,
        "subareas_validas": ";".join(centro.subareas),
    }

    # RC4 · PRD §7.3: comparación exacta. Los fixtures escriben la subárea igual
    # que el maestro; si algún día varían, se ajusta con una ADR (T1.1).
    if solicitud.subarea not in centro.subareas:
        return Veredicto(
            codigo="RC4",
            tipo="bloqueo",
            detalle=(
                f"La subárea «{solicitud.subarea}» no pertenece al centro de costo "
                f"{centro.centro_costo} ({centro.nombre}). Las subáreas válidas de ese "
                f"centro son: {', '.join(centro.subareas)}. Hay que corregir la subárea "
                "o el centro de costo en la solicitud."
            ),
            datos=datos,
        )

    return Veredicto(
        codigo="RC4",
        tipo="ok",
        detalle=(
            f"La subárea «{solicitud.subarea}» pertenece al centro de costo "
            f"{centro.centro_costo} ({centro.nombre})."
        ),
        datos=datos,
    )
