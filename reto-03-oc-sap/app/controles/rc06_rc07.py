"""RC6 (indicador de IVA) y RC7 (condiciones de pago): completar desde el maestro.

Los dos controles hacen lo mismo —si la solicitud no trae el dato, lo toman del
maestro del proveedor— y devuelven **tipos distintos** cuando derivan:

- RC6 devuelve `confirmacion`: el indicador de IVA tiene impacto fiscal y
  contable que alguien tiene que asumir. Nadie firma un impuesto por defecto.
- RC7 devuelve `derivado`: las condiciones de pago son un acuerdo comercial ya
  pactado con el proveedor y registrado en el maestro, así que basta informarlas.

La asimetría la establece el PRD §7.3 (RC6 vs RC7); está aquí escrita porque es
lo primero que un evaluador pregunta al ver dos funciones casi iguales.

Ambas son puras y no lanzan excepciones (C3).
"""

from app.controles.resolutores import resolver_proveedor
from app.core.modelos import CondicionPago, IndicadorIva, Maestros, Paquete, Veredicto


def rc6_indicador_iva(paquete: Paquete, maestros: Maestros) -> Veredicto:
    """RC6 · Si la solicitud no trae indicador de IVA, se deriva del proveedor
    y se pide confirmación por su impacto fiscal."""
    informado = paquete.solicitud.indicador_iva

    if informado is not None:
        indicador = _buscar_iva(maestros, informado)
        if indicador is None:
            return Veredicto(
                codigo="RC6",
                tipo="bloqueo",
                detalle=(
                    f"El indicador de IVA «{informado}» que trae la solicitud no existe en "
                    f"el maestro de impuestos. Los códigos válidos son: "
                    f"{_codigos_iva(maestros)}. Hay que corregirlo con el solicitante: un "
                    "código de impuesto inventado no se puede enviar a SAP."
                ),
                datos={
                    "indicador_iva": informado,
                    "origen": "solicitud",
                    "tasa": None,
                    "descripcion": None,
                },
            )
        return Veredicto(
            codigo="RC6",
            tipo="ok",
            detalle=(
                f"La solicitud informó el indicador de IVA {indicador.codigo} "
                f"({indicador.descripcion}), que existe en el maestro de impuestos."
            ),
            datos=_datos_iva(indicador, "solicitud"),
        )

    proveedor = resolver_proveedor(paquete, maestros)
    if proveedor is None:
        # Sin proveedor no hay default del que derivar. RC1 ya bloqueó por esto,
        # así que RC6 no repite la causa: se declara no evaluable.
        return Veredicto(
            codigo="RC6",
            tipo="no_evaluable",
            detalle=(
                "La solicitud no informó indicador de IVA y no se pudo identificar al "
                "proveedor en el maestro, así que no hay un valor por defecto del que "
                "tomarlo. Se evalúa de nuevo cuando se resuelva el proveedor (RC1)."
            ),
            datos={
                "indicador_iva": None,
                "origen": None,
                "tasa": None,
                "descripcion": None,
            },
        )

    derivado = proveedor.indicador_iva_default
    indicador = _buscar_iva(maestros, derivado)
    if indicador is None:
        return Veredicto(
            codigo="RC6",
            tipo="bloqueo",
            detalle=(
                f"La solicitud no informó indicador de IVA y el que trae el maestro del "
                f"proveedor {proveedor.nombre} («{derivado}») no existe en el maestro de "
                f"impuestos. Los códigos válidos son: {_codigos_iva(maestros)}. Es un "
                "problema de datos maestros: hay que corregirlo en SAP."
            ),
            datos={
                "indicador_iva": derivado,
                "origen": "derivado",
                "tasa": None,
                "descripcion": None,
            },
        )

    return Veredicto(
        codigo="RC6",
        tipo="confirmacion",
        detalle=(
            f"La solicitud no informó indicador de IVA; se tomó {indicador.codigo} "
            f"({indicador.descripcion}, tasa {_porcentaje(indicador.tasa)}) del maestro del "
            f"proveedor {proveedor.nombre}. Por su impacto fiscal hay que confirmarlo antes "
            "de crear la orden."
        ),
        datos=_datos_iva(indicador, "derivado"),
    )


def rc7_condiciones_pago(paquete: Paquete, maestros: Maestros) -> Veredicto:
    """RC7 · Si la solicitud no trae condiciones de pago, se derivan del
    proveedor y solo se informan."""
    informada = paquete.solicitud.condiciones_pago

    if informada is not None:
        condicion = _buscar_condicion(maestros, informada)
        if condicion is None:
            return Veredicto(
                codigo="RC7",
                tipo="bloqueo",
                detalle=(
                    f"Las condiciones de pago «{informada}» que trae la solicitud no "
                    f"existen en el maestro. Los códigos válidos son: "
                    f"{_codigos_condiciones(maestros)}. Hay que corregirlo con el "
                    "solicitante antes de enviar la orden a SAP."
                ),
                datos={
                    "condiciones_pago": informada,
                    "origen": "solicitud",
                    "dias": None,
                    "descripcion": None,
                },
            )
        return Veredicto(
            codigo="RC7",
            tipo="ok",
            detalle=(
                f"La solicitud informó las condiciones de pago {condicion.codigo} "
                f"({condicion.descripcion}), que existen en el maestro."
            ),
            datos=_datos_condicion(condicion, "solicitud"),
        )

    proveedor = resolver_proveedor(paquete, maestros)
    if proveedor is None:
        return Veredicto(
            codigo="RC7",
            tipo="no_evaluable",
            detalle=(
                "La solicitud no informó condiciones de pago y no se pudo identificar al "
                "proveedor en el maestro, así que no hay un valor por defecto del que "
                "tomarlas. Se evalúa de nuevo cuando se resuelva el proveedor (RC1)."
            ),
            datos={
                "condiciones_pago": None,
                "origen": None,
                "dias": None,
                "descripcion": None,
            },
        )

    derivada = proveedor.condiciones_pago_default
    condicion = _buscar_condicion(maestros, derivada)
    if condicion is None:
        return Veredicto(
            codigo="RC7",
            tipo="bloqueo",
            detalle=(
                f"La solicitud no informó condiciones de pago y las que trae el maestro del "
                f"proveedor {proveedor.nombre} («{derivada}») no existen en el maestro de "
                f"condiciones. Los códigos válidos son: {_codigos_condiciones(maestros)}. Es "
                "un problema de datos maestros: hay que corregirlo en SAP."
            ),
            datos={
                "condiciones_pago": derivada,
                "origen": "derivado",
                "dias": None,
                "descripcion": None,
            },
        )

    # `derivado`, no `confirmacion`: ver la nota de asimetría del encabezado.
    return Veredicto(
        codigo="RC7",
        tipo="derivado",
        detalle=(
            f"La solicitud no informó condiciones de pago; se tomaron {condicion.codigo} "
            f"({condicion.descripcion}) del maestro del proveedor {proveedor.nombre}, que "
            "es el acuerdo comercial vigente con él."
        ),
        datos=_datos_condicion(condicion, "derivado"),
    )


def _buscar_iva(maestros: Maestros, codigo: str) -> IndicadorIva | None:
    """Busca un indicador de IVA por código. None si no existe."""
    for indicador in maestros.indicadores_iva:
        if indicador.codigo == codigo:
            return indicador
    return None


def _buscar_condicion(maestros: Maestros, codigo: str) -> CondicionPago | None:
    """Busca una condición de pago por código. None si no existe."""
    for condicion in maestros.condiciones_pago:
        if condicion.codigo == codigo:
            return condicion
    return None


def _codigos_iva(maestros: Maestros) -> str:
    """Códigos de IVA válidos, para el mensaje de error."""
    return ", ".join(indicador.codigo for indicador in maestros.indicadores_iva)


def _codigos_condiciones(maestros: Maestros) -> str:
    """Códigos de condiciones de pago válidos, para el mensaje de error."""
    return ", ".join(condicion.codigo for condicion in maestros.condiciones_pago)


def _datos_iva(
    indicador: IndicadorIva, origen: str
) -> dict[str, str | int | float | bool | None]:
    """Datos de trazabilidad de RC6."""
    return {
        "indicador_iva": indicador.codigo,
        "origen": origen,
        "tasa": indicador.tasa,
        "descripcion": indicador.descripcion,
    }


def _datos_condicion(
    condicion: CondicionPago, origen: str
) -> dict[str, str | int | float | bool | None]:
    """Datos de trazabilidad de RC7."""
    return {
        "condiciones_pago": condicion.codigo,
        "origen": origen,
        "dias": condicion.dias,
        "descripcion": condicion.descripcion,
    }


def _porcentaje(tasa: float) -> str:
    """Formatea una tasa como porcentaje: 0.19 -> "19%", 0.0 -> "0%".

    `indicadores-iva.json` trae la tasa de C0 como el entero `0` y Pydantic la
    coacciona a `0.0`; el formato con `%g` la muestra sin decimales inútiles y
    sin comparar floats (plan §H6).
    """
    return f"{tasa * 100:g}%"
