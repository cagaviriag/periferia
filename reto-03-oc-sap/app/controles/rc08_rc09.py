"""T1.5 · RC8 (OC retroactiva) y RC9 (fecha de aprobación).

Los dos controles de cronología. RC8 mide el desvío de proceso que la dirección
quiere medir (PRD §1 · O4): una factura anterior a la solicitud significa que la
compra ya se ejecutó y la OC se está creando después del hecho.

Funciones puras y sin excepciones (C3/C5): una fecha ilegible produce un veredicto,
nunca un `ValueError`.
"""

import re
from datetime import date

from app.core.modelos import Paquete, Veredicto

LARGO_FECHA = 10   # 'YYYY-MM-DD'

# Forma mínima exigida a los primeros 10 caracteres de una marca temporal ISO 8601.
_FORMA_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")


def solo_fecha(marca_temporal: str) -> str:
    """Extrae la parte YYYY-MM-DD de una fecha ISO 8601, con o sin hora.

    '2026-08-21T10:02:00-05:00' -> '2026-08-21'
    '2026-08-20'                -> '2026-08-20'

    Devuelve cadena vacía si la entrada no empieza con una fecha reconocible.
    """
    recorte = marca_temporal[:LARGO_FECHA]
    if _FORMA_FECHA.fullmatch(recorte) is None:
        return ""
    return recorte


def _dias_entre(desde: str, hasta: str) -> int:
    """Días calendario entre dos fechas ya recortadas a 'YYYY-MM-DD'.

    Devuelve 0 si alguna no es una fecha válida (p. ej. '2026-02-31'): el control
    nunca puede lanzar, y el veredicto ya informa las fechas en crudo.
    """
    try:
        return (date.fromisoformat(hasta) - date.fromisoformat(desde)).days
    except ValueError:
        return 0


def rc8_retroactiva(paquete: Paquete) -> Veredicto:
    """RC8 · Una factura anterior a la solicitud delata que la OC se está
    creando después del hecho. Confirmación, y marca retroactiva."""
    solicitud = paquete.solicitud
    factura = paquete.factura

    # El flujo normal no trae factura: cinco de los seis casos caen aquí.
    if factura is None:
        return Veredicto(
            codigo="RC8",
            tipo="ok",
            detalle=(
                "El paquete no trae factura, así que la orden no es retroactiva: "
                "se crea antes de que el proveedor facture."
            ),
            datos={
                "fecha_factura": None,
                "fecha_solicitud": solo_fecha(solicitud.fecha_solicitud),
                "dias_desvio": 0,
                "numero_factura": None,
                "retroactiva": False,
            },
        )

    # Se comparan las fechas recortadas como cadenas: en 'YYYY-MM-DD' el orden
    # lexicográfico coincide con el cronológico. Deliberadamente NO se convierte a
    # `datetime`: `aprobacion.fecha` trae offset ('-05:00') y `solicitud` no, así que
    # convertir metería zonas horarias en una comparación que es de calendario, no de
    # instante (dos marcas del mismo día podrían ordenarse distinto según el offset).
    fecha_factura = solo_fecha(factura.fecha)
    fecha_sol = solo_fecha(solicitud.fecha_solicitud)

    datos: dict[str, str | int | float | bool | None] = {
        "fecha_factura": fecha_factura,
        "fecha_solicitud": fecha_sol,
        "dias_desvio": 0,
        "numero_factura": factura.numero,
        "retroactiva": False,
    }

    if fecha_factura == "" or fecha_sol == "":
        return Veredicto(
            codigo="RC8",
            tipo="confirmacion",
            detalle=(
                f"No se pudo leer una de las fechas para evaluar si la orden es retroactiva: "
                f"factura '{factura.fecha}', solicitud '{solicitud.fecha_solicitud}'. "
                "Hay que verificarlas manualmente antes de confirmar."
            ),
            datos=datos,
        )

    if fecha_factura < fecha_sol:
        dias_desvio = _dias_entre(fecha_factura, fecha_sol)
        datos["dias_desvio"] = dias_desvio
        datos["retroactiva"] = True
        return Veredicto(
            codigo="RC8",
            tipo="confirmacion",
            detalle=(
                f"La factura {factura.numero} es del {fecha_factura} y la solicitud del "
                f"{fecha_sol}: {dias_desvio} días de desvío. La compra se ejecutó y se "
                "facturó antes de que existiera la solicitud formal, de modo que la orden "
                "queda retroactiva y se registrará como tal para auditoría."
            ),
            datos=datos,
        )

    return Veredicto(
        codigo="RC8",
        tipo="ok",
        detalle=(
            f"La factura {factura.numero} ({fecha_factura}) no es anterior a la solicitud "
            f"({fecha_sol}): la orden no es retroactiva."
        ),
        datos=datos,
    )


def rc9_fecha_aprobacion(paquete: Paquete) -> Veredicto:
    """RC9 · La aprobación no puede ser anterior a la solicitud.
    Confirmación."""
    solicitud = paquete.solicitud
    aprobacion = paquete.aprobacion

    # Sin aprobación no hay nada que fechar. RC2 ya bloqueó por esa causa
    # (oráculo §orden de evaluación); duplicar el bloqueo solo ruidosearía la salida.
    if aprobacion is None:
        return Veredicto(
            codigo="RC9",
            tipo="no_evaluable",
            detalle=(
                "No hay correo de aprobación, así que no hay fecha de aprobación que "
                "comparar. RC2 ya reporta la ausencia."
            ),
            datos={
                "fecha_aprobacion": None,
                "fecha_solicitud": solo_fecha(solicitud.fecha_solicitud),
                "dias_anticipacion": 0,
            },
        )

    # Mismo criterio que RC8: recortar a 10 caracteres y comparar cadenas. La
    # aprobación trae hora y offset ('2026-08-26T18:45:00-05:00') y la solicitud solo
    # fecha; la regla del PRD es de calendario, así que la hora no participa. Por eso
    # los tres casos de aprobación el mismo día (sol-002, sol-004, sol-006) dan `ok`:
    # la comparación es `>=`, como dice el PRD.
    fecha_aprobacion = solo_fecha(aprobacion.fecha)
    fecha_sol = solo_fecha(solicitud.fecha_solicitud)

    datos: dict[str, str | int | float | bool | None] = {
        "fecha_aprobacion": fecha_aprobacion,
        "fecha_solicitud": fecha_sol,
        "dias_anticipacion": 0,
    }

    if fecha_aprobacion == "" or fecha_sol == "":
        return Veredicto(
            codigo="RC9",
            tipo="confirmacion",
            detalle=(
                f"No se pudo leer una de las fechas para evaluar la aprobación: "
                f"aprobación '{aprobacion.fecha}', solicitud '{solicitud.fecha_solicitud}'. "
                "Hay que verificarlas manualmente antes de confirmar."
            ),
            datos=datos,
        )

    if fecha_aprobacion >= fecha_sol:
        return Veredicto(
            codigo="RC9",
            tipo="ok",
            detalle=(
                f"La aprobación es del {fecha_aprobacion} y la solicitud del {fecha_sol}: "
                "la aprobación no es anterior a la solicitud."
            ),
            datos=datos,
        )

    dias_anticipacion = _dias_entre(fecha_aprobacion, fecha_sol)
    datos["dias_anticipacion"] = dias_anticipacion
    return Veredicto(
        codigo="RC9",
        tipo="confirmacion",
        detalle=(
            f"La aprobación es del {fecha_aprobacion}, {dias_anticipacion} días antes de la "
            f"solicitud del {fecha_sol}: se aprobó algo antes de que se solicitara "
            "formalmente, así que hay que verificar que la aprobación corresponde a esta "
            "compra antes de confirmar."
        ),
        datos=datos,
    )
