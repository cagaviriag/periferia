"""Resolutores: cruzan la solicitud contra los maestros (T1.1).

Las tres funciones son **puras**: reciben el paquete y los maestros ya validados,
devuelven la entidad del maestro o `None`. No hacen I/O, no guardan estado y no
lanzan excepciones (C3).

Reparto de responsabilidades: un resolutor solo dice **si encontró** la entidad.
Decidir si eso es aceptable es trabajo de los controles —`activo` lo evalúa RC1,
el tope lo evalúa RC3— para que un mismo cruce sirva a varios controles sin
repetir la búsqueda ni la regla.
"""

from app.core.modelos import Aprobador, CentroCosto, Maestros, Paquete, Proveedor
from app.core.normalizar import normalizar_nit, normalizar_nombre


def resolver_proveedor(paquete: Paquete, maestros: Maestros) -> Proveedor | None:
    """Encuentra el proveedor de la solicitud en el maestro.

    Cruza primero por NIT normalizado. Si la solicitud no trae NIT, cruza por
    nombre normalizado. Devuelve None si no hay coincidencia.
    Encontrarlo NO implica que sea apto: `activo` lo evalúa RC1.
    """
    nit = normalizar_nit(paquete.solicitud.proveedor_nit)

    if nit is not None:
        for proveedor in maestros.proveedores:
            if normalizar_nit(proveedor.nit) == nit:
                return proveedor
        # Decisión de diseño (T1.1 §resolver_proveedor): con NIT informado NO se
        # cae al cruce por nombre. Un NIT que no existe es un NIT que no existe;
        # adivinar por nombre ocultaría justo el error que RC1 debe bloquear
        # (sol-002) y podría imputar la compra a otro proveedor.
        return None

    # Sin NIT solo queda el nombre: es el camino de sol-006 (ADR-008).
    nombre = normalizar_nombre(paquete.solicitud.proveedor_nombre)
    if nombre is None:
        return None

    for proveedor in maestros.proveedores:
        if normalizar_nombre(proveedor.nombre) == nombre:
            return proveedor
    return None


def resolver_centro(paquete: Paquete, maestros: Maestros) -> CentroCosto | None:
    """Encuentra el centro de costo de la solicitud. None si no existe."""
    codigo = paquete.solicitud.centro_costo.strip()

    for centro in maestros.centros_costo:
        if centro.centro_costo.strip() == codigo:
            return centro
    return None


def resolver_aprobador(paquete: Paquete, maestros: Maestros) -> Aprobador | None:
    """Encuentra al aprobador del correo de aprobación DENTRO del centro de costo
    de la solicitud.

    Devuelve None si no hay aprobación, si el centro no existe, o si quien
    aprueba no está listado como aprobador de ESE centro. Esa última condición
    es la causa del bloqueo de sol-003.
    """
    if paquete.aprobacion is None:
        return None

    centro = resolver_centro(paquete, maestros)
    if centro is None:
        return None

    # El correo puede llegar con mayúsculas o espacios; el maestro no.
    email = paquete.aprobacion.de.strip().lower()

    for aprobador in centro.aprobadores:
        if aprobador.email.strip().lower() == email:
            return aprobador

    # RC2 · PRD §7.3: el aprobador puede existir en otro centro y aun así no
    # tener autoridad aquí. Es el defecto de sol-003 y por eso la búsqueda se
    # hace dentro del centro, no sobre todo el maestro.
    return None
