"""Normalizadores de texto de los fixtures hacia la forma que espera SAP.

Las cuatro funciones son **puras**: reciben strings, devuelven strings. No hacen
I/O, no guardan estado global y no lanzan excepciones (C3). Quien las usa decide
de dónde vienen los datos; aquí solo se arregla el formato.

Motivo de existir:
- `normalizar_nit` / `normalizar_nombre`: el cruce de proveedor de RC1 (PRD §7.3)
  falla en los 6 casos sin ellas, porque el NIT viene en tres formatos y `sol-006`
  no trae NIT (ADR-008).
- `truncar_descripcion`: el payload de SAP limita `descripcion` a 40 caracteres y
  5 de las 6 descripciones de los fixtures lo exceden (PRD §7.4 · ADR-004).
- `inferir_unidad`: el payload exige `unidad ∈ {UN, H, MES}` y ningún fixture la
  trae (PRD §7.4 · ADR-005).
"""

from __future__ import annotations

import unicodedata

_DIGITOS = "0123456789"

# Sufijos societarios: no identifican al proveedor y estorban al cruce por nombre
# (RC1 · PRD §7.3). Se comparan como palabras sueltas y ya sin puntuación, así que
# "S.A.S." llega aquí convertido en "s a s"; por eso las variantes separadas van
# primero, para que "s a s" gane antes de que "s a" muerda solo una parte.
_SUFIJOS_SOCIETARIOS: tuple[tuple[str, ...], ...] = (
    ("sociedad", "anonima"),
    ("s", "a", "s"),
    ("s", "a"),
    ("sas",),
    ("sa",),
    ("ltda",),
)

# Caracteres que no deben quedar al final de un texto breve truncado (ADR-004).
_PUNTUACION_FINAL = " ,;:.-"

_PALABRAS_HORA = ("hora", "horas")
_PALABRAS_MES = ("mes", "meses")


def normalizar_nit(nit: str | None) -> str | None:
    """Deja el NIT en su forma canónica para cruzar contra el maestro.

    Elimina puntos, espacios y guiones. Si venía un dígito de verificación
    separado por guion, lo descarta. Devuelve None si la entrada es None o
    queda vacía tras limpiar.
    """
    if nit is None:
        return None

    # ADR-008: el dígito de verificación solo se descarta cuando hay un guion
    # explícito; un NIT de 10 dígitos sin separador se respeta completo.
    if "-" in nit:
        nit = nit.rsplit("-", 1)[0]

    digitos = "".join(caracter for caracter in nit if caracter in _DIGITOS)
    if not digitos:
        return None
    return digitos


def normalizar_nombre(nombre: str | None) -> str | None:
    """Deja el nombre en forma comparable: minúsculas, sin acentos, sin
    puntuación, sin sufijos societarios (S.A.S., S.A., LTDA) y con espacios
    colapsados. Devuelve None si la entrada es None o queda vacía.
    """
    if nombre is None:
        return None

    texto = _quitar_acentos(nombre.lower())
    # La puntuación se va antes que los sufijos para que "S.A.S." sea "s a s".
    texto = "".join(caracter if caracter.isalnum() else " " for caracter in texto)

    palabras = _quitar_sufijos_societarios(texto.split())
    normalizado = " ".join(palabras)
    if not normalizado:
        return None
    return normalizado


def truncar_descripcion(descripcion: str, limite: int = 40) -> str:
    """Recorta a `limite` caracteres sin partir palabras (ADR-004).

    Si cabe, la devuelve sin el espacio final. Si no, corta en la última
    frontera de palabra que no exceda el límite y elimina espacios y puntuación
    final. Si la primera palabra ya excede el límite, corta en seco a `limite`.
    """
    if len(descripcion) <= limite:
        # El invariante 6 de T0.4 ("nunca termina en espacio") también rige en el
        # camino rápido: un Excel con espacio al final no debe llegar al payload.
        # Solo se quita el espacio, no la puntuación: aquí no se truncó nada, así
        # que el punto o la coma final son parte legítima del texto original.
        return descripcion.rstrip()

    # Se mira un carácter más allá del límite: si justo ahí hay un espacio, la
    # palabra anterior cabe completa.
    ventana = descripcion[: limite + 1]
    corte = ventana.rfind(" ")
    if corte == -1:
        # La primera palabra ya excede el límite: no hay frontera donde cortar.
        return descripcion[:limite]

    return ventana[:corte].rstrip(_PUNTUACION_FINAL)


def inferir_unidad(item_cotizacion: str | None, descripcion: str) -> str:
    """Infiere la unidad de medida de SAP (ADR-005).

    Usa el sustantivo núcleo del ítem de la cotización, que es inequívoco.
    'hora'/'horas' -> 'H'; 'mes'/'meses' -> 'MES'; cualquier otro -> 'UN'.
    Si no hay ítem de cotización, aplica la misma heurística sobre la
    descripción. 'UN' es el default seguro.
    """
    item = normalizar_nombre(item_cotizacion)
    if item is not None:
        # ADR-005: en el ítem manda el sustantivo núcleo. "Licencia antivirus
        # corporativo 12 meses" son 120 licencias (UN), no 120 meses: "meses" ahí
        # es la vigencia, no la unidad contada.
        return _unidad_por_nucleo(item)

    # Sin ítem no hay núcleo confiable (la descripción empieza por "Bolsa de 100
    # horas..."), así que se busca la palabra de unidad en todo el texto.
    return _unidad_por_palabras(normalizar_nombre(descripcion))


def _quitar_acentos(texto: str) -> str:
    """Descompone y descarta los caracteres combinantes: "papelería" -> "papeleria"."""
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(caracter for caracter in descompuesto if not unicodedata.combining(caracter))


def _quitar_sufijos_societarios(palabras: list[str]) -> list[str]:
    """Saca de la lista las palabras que forman un sufijo societario."""
    restantes: list[str] = []
    posicion = 0
    while posicion < len(palabras):
        largo = _largo_sufijo_en(palabras, posicion)
        if largo > 0:
            posicion += largo
            continue
        restantes.append(palabras[posicion])
        posicion += 1
    return restantes


def _largo_sufijo_en(palabras: list[str], inicio: int) -> int:
    """Cuántas palabras ocupa el sufijo societario que empieza en `inicio`, o 0."""
    for sufijo in _SUFIJOS_SOCIETARIOS:
        if tuple(palabras[inicio : inicio + len(sufijo)]) == sufijo:
            return len(sufijo)
    return 0


def _unidad_por_nucleo(texto_normalizado: str) -> str:
    """Unidad según la primera palabra del texto (el sustantivo núcleo)."""
    nucleo = texto_normalizado.split()[0]
    if nucleo in _PALABRAS_HORA:
        return "H"
    if nucleo in _PALABRAS_MES:
        return "MES"
    return "UN"


def _unidad_por_palabras(texto_normalizado: str | None) -> str:
    """Unidad según cualquier palabra del texto. 'hora' se evalúa antes que 'mes'."""
    if texto_normalizado is None:
        return "UN"

    palabras = texto_normalizado.split()
    if any(palabra in _PALABRAS_HORA for palabra in palabras):
        return "H"
    if any(palabra in _PALABRAS_MES for palabra in palabras):
        return "MES"
    return "UN"
