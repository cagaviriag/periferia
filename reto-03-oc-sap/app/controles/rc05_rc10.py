"""T1.3 · RC10 (aritmética de la solicitud) y RC5 (cotización vs solicitud).

Dos controles que solo miran el `Paquete`: no consultan maestros ni resuelven nada.
Son funciones puras —entra un `Paquete`, sale un `Veredicto`— y no lanzan excepciones
(C3/C5): toda rama devuelve un veredicto, incluido el caso de `valor_total == 0`.

- RC10 (PRD §7.3, **bloqueo**): la solicitud tiene que cuadrar consigo misma.
- RC5 (PRD §7.3, confirmación): la solicitud se contrasta con la cotización del
  proveedor; una desviación mayor al 2 % no bloquea, pide confirmación humana.
"""

from app.core.modelos import Paquete, Veredicto

UMBRAL_DESVIACION = 0.02   # 2 %, PRD §7.3 RC5
TOLERANCIA_REDONDEO = 1    # ± 1 unidad monetaria, PRD §7.3 RC10


def _pesos(monto: int) -> str:
    """Formatea un monto entero con punto de miles: 25000000 → '25.000.000'.

    Se formatea con la coma de Python y se sustituye por punto, que es el separador
    de miles de la convención colombiana y el que usan los fixtures ('COP 25.000.000').
    """
    return f"{monto:,}".replace(",", ".")


def rc10_aritmetica(paquete: Paquete) -> Veredicto:
    """RC10 · cantidad × valor_unitario debe igualar valor_total. Bloqueo."""
    solicitud = paquete.solicitud
    esperado = solicitud.cantidad * solicitud.valor_unitario
    diferencia = abs(esperado - solicitud.valor_total)

    datos: dict[str, str | int | float | bool | None] = {
        "cantidad": solicitud.cantidad,
        "valor_unitario": solicitud.valor_unitario,
        "esperado": esperado,
        "valor_total": solicitud.valor_total,
        "diferencia": diferencia,
    }

    # Caso de borde antes del cálculo: con cantidad en cero la multiplicación
    # "cuadra" cuando el total también es cero, pero una compra de cero unidades
    # no es una solicitud válida. Se bloquea con detalle propio para que el
    # usuario sepa qué dato pedir al solicitante.
    if solicitud.cantidad == 0:
        return Veredicto(
            codigo="RC10",
            tipo="bloqueo",
            detalle=(
                "La solicitud trae cantidad en cero, así que no hay nada que comprar. "
                f"Valor unitario {_pesos(solicitud.valor_unitario)} y valor total "
                f"{_pesos(solicitud.valor_total)}. Pídale al solicitante la cantidad real."
            ),
            datos=datos,
        )

    if diferencia <= TOLERANCIA_REDONDEO:
        return Veredicto(
            codigo="RC10",
            tipo="ok",
            detalle=(
                f"La aritmética cuadra: {_pesos(solicitud.cantidad)} × "
                f"{_pesos(solicitud.valor_unitario)} = {_pesos(esperado)}, "
                f"igual al valor total de la solicitud ({_pesos(solicitud.valor_total)})."
            ),
            datos=datos,
        )

    # El detalle muestra la multiplicación explícita: el error tiene que verse sin
    # que el usuario tenga que recalcular nada.
    return Veredicto(
        codigo="RC10",
        tipo="bloqueo",
        detalle=(
            f"La aritmética no cuadra: {_pesos(solicitud.cantidad)} × "
            f"{_pesos(solicitud.valor_unitario)} = {_pesos(esperado)}, "
            f"pero la solicitud dice {_pesos(solicitud.valor_total)} "
            f"(diferencia de {_pesos(diferencia)}). "
            "Hay que corregir la solicitud antes de crear la orden de compra."
        ),
        datos=datos,
    )


def rc5_cotizacion(paquete: Paquete) -> Veredicto:
    """RC5 · El total de la cotización no puede desviarse más del 2 % del
    valor_total de la solicitud. Confirmación."""
    solicitud = paquete.solicitud
    cotizacion = paquete.cotizacion
    umbral_pct = round(UMBRAL_DESVIACION * 100, 2)

    if cotizacion is None:
        return Veredicto(
            codigo="RC5",
            tipo="confirmacion",
            detalle=(
                "No hay cotización para contrastar el valor de la solicitud "
                f"({_pesos(solicitud.valor_total)}). Si se confirma, la orden se creará "
                "con el valor de la solicitud, sin respaldo de una cotización del proveedor."
            ),
            datos={
                "valor_solicitud": solicitud.valor_total,
                "valor_cotizacion": None,
                "desviacion_pct": None,
                "umbral_pct": umbral_pct,
            },
        )

    datos: dict[str, str | int | float | bool | None] = {
        "valor_solicitud": solicitud.valor_total,
        "valor_cotizacion": cotizacion.total,
        "desviacion_pct": None,
        "umbral_pct": umbral_pct,
    }

    # Guarda explícita contra la división por cero: el PRD no contempla una
    # solicitud con valor total cero, así que se trata como imposible de verificar
    # y se eleva a confirmación. Nunca se calcula la desviación en esta rama.
    if solicitud.valor_total == 0:
        return Veredicto(
            codigo="RC5",
            tipo="confirmacion",
            detalle=(
                "La solicitud tiene valor total en cero, así que la desviación frente a la "
                f"cotización ({_pesos(cotizacion.total)}) no se puede calcular. "
                "Hay que verificar el valor con el solicitante antes de confirmar."
            ),
            datos=datos,
        )

    desviacion = abs(cotizacion.total - solicitud.valor_total) / solicitud.valor_total
    desviacion_pct = round(desviacion * 100, 2)
    datos["desviacion_pct"] = desviacion_pct

    if desviacion <= UMBRAL_DESVIACION:
        return Veredicto(
            codigo="RC5",
            tipo="ok",
            detalle=(
                f"La cotización ({_pesos(cotizacion.total)}) coincide con la solicitud "
                f"({_pesos(solicitud.valor_total)}): desviación de {desviacion_pct:.2f} %, "
                f"dentro del {umbral_pct:.2f} % permitido."
            ),
            datos=datos,
        )

    # PRD §11: la UI muestra la lista de confirmaciones con los dos valores, así que
    # el detalle debe traerlos ambos y decir cuál se usará. Se usa el de la solicitud
    # porque es el monto que el líder aprobó por correo.
    return Veredicto(
        codigo="RC5",
        tipo="confirmacion",
        detalle=(
            f"La cotización del proveedor es {_pesos(cotizacion.total)} y la solicitud dice "
            f"{_pesos(solicitud.valor_total)}: una desviación de {desviacion_pct:.2f} %, "
            f"por encima del {umbral_pct:.2f} % permitido. "
            f"Si se confirma, la orden se creará con el valor de la solicitud "
            f"({_pesos(solicitud.valor_total)}), porque es el monto que aprobó el líder."
        ),
        datos=datos,
    )
