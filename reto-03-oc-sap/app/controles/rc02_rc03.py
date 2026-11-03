"""RC2 (aprobación) y RC3 (tope del aprobador). Los dos controles de autoridad.

Son el corazón del valor del reto: hoy esta validación *"es mental, no
sistemática"* (PRD §2.2.2).

Ambas funciones son puras y no lanzan excepciones (C3). `rc3_tope` recibe el
veredicto de RC2 en la firma porque la dependencia de ADR-003 es real: sin un
aprobador válido para el centro no existe un tope aplicable. Tenerla en el tipo
y no en un comentario impide que alguien llame a RC3 por su cuenta.
"""

from app.controles.resolutores import resolver_aprobador, resolver_centro
from app.core.modelos import Maestros, Paquete, Veredicto


def rc2_aprobacion(paquete: Paquete, maestros: Maestros) -> Veredicto:
    """RC2 · La aprobación debe existir, contener "Aprobado" y venir de un correo
    listado como aprobador del centro de costo de la solicitud. Bloqueo."""
    solicitud = paquete.solicitud
    aprobacion = paquete.aprobacion

    # Causa 1 · no llegó el correo. La acción es pedirlo al solicitante, distinta
    # de todas las demás, así que se reporta sola.
    if aprobacion is None:
        return Veredicto(
            codigo="RC2",
            tipo="bloqueo",
            detalle=(
                "No llegó el correo de aprobación del líder, así que no hay con qué "
                "acreditar la autorización del gasto. Hay que pedir al solicitante que "
                "adjunte la aprobación."
            ),
            datos={
                "aprobador_email": None,
                "centro_costo": solicitud.centro_costo,
                "aprobadores_validos": _emails_validos(paquete, maestros),
                "aprobado": False,
            },
        )

    datos: dict[str, str | int | float | bool | None] = {
        "aprobador_email": aprobacion.de,
        "centro_costo": solicitud.centro_costo,
        "aprobadores_validos": _emails_validos(paquete, maestros),
        "aprobado": aprobacion.aprobado,
    }

    # Causa 2 · el correo existe pero no aprueba nada. `aprobado` ya viene
    # calculado en el paquete (lo hace `oc_leer_paquete`); RC2 solo lo lee.
    if not aprobacion.aprobado:
        return Veredicto(
            codigo="RC2",
            tipo="bloqueo",
            detalle=(
                f"El correo de {aprobacion.de} no contiene una aprobación explícita: no "
                "dice «Aprobado». Hay que solicitar al líder una aprobación expresa antes "
                "de crear la orden."
            ),
            datos=datos,
        )

    # Causa 3 · sin centro en el maestro no hay lista de aprobadores contra la
    # que verificar la autoridad.
    centro = resolver_centro(paquete, maestros)
    if centro is None:
        return Veredicto(
            codigo="RC2",
            tipo="bloqueo",
            detalle=(
                f"El centro de costo {solicitud.centro_costo} no existe en el maestro, así "
                "que no se puede verificar quién tiene autoridad para aprobar este gasto. "
                "Hay que confirmar el centro con el solicitante."
            ),
            datos=datos,
        )

    # Causa 4 · quien aprobó no tiene autoridad sobre ese centro. Es sol-003 y el
    # mensaje debe permitir corregirlo sin investigar: dice quién aprobó, a qué
    # centro se imputa y quién sí puede aprobar ahí.
    aprobador = resolver_aprobador(paquete, maestros)
    if aprobador is None:
        emails = ", ".join(valido.email for valido in centro.aprobadores)
        return Veredicto(
            codigo="RC2",
            tipo="bloqueo",
            detalle=(
                f"La aprobación la firmó {aprobacion.de}, que no es aprobador del centro "
                f"de costo {centro.centro_costo} ({centro.nombre}) al que se imputa este "
                f"gasto. Quien sí puede aprobar ahí es: {emails}. Hay que pedir la "
                "aprobación a esa persona, o corregir el centro de costo si el gasto "
                "pertenece a otra área."
            ),
            datos=datos,
        )

    return Veredicto(
        codigo="RC2",
        tipo="ok",
        detalle=(
            f"Aprobado por {aprobador.nombre} ({aprobador.email}), aprobador del centro de "
            f"costo {centro.centro_costo} ({centro.nombre})."
        ),
        datos=datos,
    )


def rc3_tope(paquete: Paquete, maestros: Maestros, rc2: Veredicto) -> Veredicto:
    """RC3 · El valor total no puede exceder el tope del aprobador. Bloqueo.

    Recibe el veredicto de RC2 porque solo se evalúa si RC2 pasó (ADR-003):
    sin un aprobador válido para el centro no existe un tope aplicable.
    """
    valor_total = paquete.solicitud.valor_total

    # ADR-003: aquí NO se devuelve `bloqueo`. Duplicar la causa raíz haría que el
    # usuario viera dos fallos donde hay uno y no sabría cuál corregir.
    if rc2.tipo != "ok":
        return Veredicto(
            codigo="RC3",
            tipo="no_evaluable",
            detalle=(
                "No se puede verificar el tope de aprobación porque RC2 ya bloqueó la "
                "solicitud: sin un aprobador válido para el centro de costo no existe un "
                "tope aplicable. Se evalúa de nuevo cuando se resuelva la aprobación."
            ),
            datos={
                "valor_total": valor_total,
                "tope": None,
                "exceso": None,
                "aprobador_email": None,
            },
        )

    aprobador = resolver_aprobador(paquete, maestros)
    if aprobador is None:
        # Defensivo: RC2 en `ok` garantiza que existe. Se cubre igual porque un
        # control nunca puede lanzar (C3) y nunca debe devolver un tipo inválido.
        return Veredicto(
            codigo="RC3",
            tipo="no_evaluable",
            detalle=(
                "No se pudo identificar al aprobador del centro de costo, así que no hay "
                "tope contra el que comparar el valor de la solicitud."
            ),
            datos={
                "valor_total": valor_total,
                "tope": None,
                "exceso": None,
                "aprobador_email": None,
            },
        )

    # PRD §7.3 RC3: la comparación es `≤`, no `<`. El tope es un valor que el
    # aprobador sí puede autorizar.
    exceso = max(0, valor_total - aprobador.tope)

    datos: dict[str, str | int | float | bool | None] = {
        "valor_total": valor_total,
        "tope": aprobador.tope,
        "exceso": exceso,
        "aprobador_email": aprobador.email,
    }

    if exceso > 0:
        return Veredicto(
            codigo="RC3",
            tipo="bloqueo",
            detalle=(
                f"El valor de la solicitud es {_pesos(valor_total)} y el tope de aprobación "
                f"de {aprobador.nombre} es {_pesos(aprobador.tope)}: excede en "
                f"{_pesos(exceso)}. Hay que escalar la aprobación a alguien con tope "
                "suficiente para ese centro de costo."
            ),
            datos=datos,
        )

    return Veredicto(
        codigo="RC3",
        tipo="ok",
        detalle=(
            f"El valor de la solicitud, {_pesos(valor_total)}, está dentro del tope de "
            f"{_pesos(aprobador.tope)} de {aprobador.nombre}."
        ),
        datos=datos,
    )


def _emails_validos(paquete: Paquete, maestros: Maestros) -> str:
    """Emails de los aprobadores del centro de la solicitud, unidos por `;`.

    Cadena vacía si el centro no existe en el maestro.
    """
    centro = resolver_centro(paquete, maestros)
    if centro is None:
        return ""
    return ";".join(aprobador.email for aprobador in centro.aprobadores)


def _pesos(monto: int) -> str:
    """Formatea un monto en pesos colombianos: 11400000 -> "COP 11.400.000".

    El separador de miles es el punto, como en los fixtures y en la cotización.
    Se arma desde el formato con coma de Python y luego se cambia el símbolo:
    la alternativa es un `locale` del sistema, que no es determinista entre
    máquinas (C9).
    """
    return "COP " + f"{monto:,}".replace(",", ".")
