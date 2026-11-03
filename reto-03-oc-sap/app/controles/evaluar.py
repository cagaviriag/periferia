"""Orquestador de la matriz de controles (T1.6).

Corre los diez controles RC1–RC10 en el orden que fija `specs/01-oracle.md` y
consolida sus veredictos en un `ResultadoValidacion`.

Este módulo **no contiene lógica de control**: solo ensambla. Si un control
devuelve algo distinto de lo que dice el oráculo, se corrige el control, nunca
se parchea el resultado aquí — un parche escondería el defecto.

Dos reglas que es fácil equivocar:

1. Se ejecutan **los diez siempre**, sin corto-circuito. Si RC1 bloquea, igual
   se evalúan los otros nueve: la analista debe ver todo lo que está mal en una
   sola pasada, no uno por uno. La única dependencia es la de RC3 respecto a
   RC2 (ADR-003), y se expresa pasándole el veredicto.

2. `derivados` se arma filtrando por `datos["origen"] == "derivado"`, **no** por
   el tipo del veredicto. RC6, cuando deriva, devuelve tipo `confirmacion`
   (el IVA tiene impacto fiscal y alguien debe asumirlo), mientras RC7 devuelve
   tipo `derivado`. Filtrar por tipo perdería el IVA derivado.
"""

from app.controles.rc01_rc04 import rc1_proveedor, rc4_subarea
from app.controles.rc02_rc03 import rc2_aprobacion, rc3_tope
from app.controles.rc05_rc10 import rc5_cotizacion, rc10_aritmetica
from app.controles.rc06_rc07 import rc6_indicador_iva, rc7_condiciones_pago
from app.controles.rc08_rc09 import rc8_retroactiva, rc9_fecha_aprobacion
from app.core.modelos import Maestros, Paquete, ResultadoValidacion, Veredicto

# Campos que RC6 y RC7 pueden completar desde el maestro de proveedores.
# La clave del veredicto en `datos` coincide con el nombre del campo.
CAMPOS_DERIVABLES = ("indicador_iva", "condiciones_pago")


def evaluar_controles(paquete: Paquete, maestros: Maestros) -> ResultadoValidacion:
    """Corre los diez controles y consolida el resultado.

    `apta` es True solo si no hay ningún bloqueo. Las confirmaciones NO impiden
    la aptitud: la permiten condicionada a `confirmado=True` en `oc_crear`.
    """
    # Orden del oráculo. RC3 recibe el veredicto de RC2 porque sin un aprobador
    # válido para el centro no existe un tope aplicable (ADR-003).
    rc1 = rc1_proveedor(paquete, maestros)
    rc4 = rc4_subarea(paquete, maestros)
    rc2 = rc2_aprobacion(paquete, maestros)
    rc3 = rc3_tope(paquete, maestros, rc2)
    rc10 = rc10_aritmetica(paquete)
    rc5 = rc5_cotizacion(paquete)
    rc6 = rc6_indicador_iva(paquete, maestros)
    rc7 = rc7_condiciones_pago(paquete, maestros)
    rc8 = rc8_retroactiva(paquete)
    rc9 = rc9_fecha_aprobacion(paquete)

    veredictos = [rc1, rc4, rc2, rc3, rc10, rc5, rc6, rc7, rc8, rc9]

    bloqueos = [v for v in veredictos if v.tipo == "bloqueo"]
    confirmaciones = [v for v in veredictos if v.tipo == "confirmacion"]

    return ResultadoValidacion(
        apta=len(bloqueos) == 0,
        bloqueos=bloqueos,
        confirmaciones=confirmaciones,
        derivados=_recoger_derivados(rc6, rc7),
        retroactiva=bool(rc8.datos.get("retroactiva", False)),
        veredictos=veredictos,
    )


def _recoger_derivados(rc6: Veredicto, rc7: Veredicto) -> dict[str, str]:
    """Extrae los valores que se completaron desde el maestro de proveedores.

    Se filtra por `datos["origen"] == "derivado"` y no por el tipo del
    veredicto, porque RC6 derivado es una `confirmacion`. Ver el docstring
    del módulo.
    """
    derivados: dict[str, str] = {}
    for veredicto in (rc6, rc7):
        if veredicto.datos.get("origen") != "derivado":
            continue
        for campo in CAMPOS_DERIVABLES:
            valor = veredicto.datos.get(campo)
            if isinstance(valor, str) and valor:
                derivados[campo] = valor
    return derivados
