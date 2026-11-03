"""Tests de los modelos de dominio (T0.6).

Los modelos de entrada se validan contra los **fixtures reales**, no contra
diccionarios inventados: si un fixture cambia, el test se entera.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.modelos import (
    Aprobacion,
    AprobadorOC,
    Correo,
    Cotizacion,
    ExcepcionOC,
    Factura,
    Maestros,
    OrdenCompra,
    Paquete,
    PosicionOC,
    ReferenciaOC,
    ResultadoValidacion,
    Solicitud,
    Veredicto,
    error,
    ok,
)

RAIZ = Path(__file__).resolve().parent.parent
FIXTURES = RAIZ / "fixtures" / "reto-03"
SOLICITUDES = FIXTURES / "solicitudes"
MAESTROS = FIXTURES / "maestros"

CASOS = ["sol-001", "sol-002", "sol-003", "sol-004", "sol-005", "sol-006"]


def leer_json(ruta: Path) -> object:
    """Lee un fixture JSON. Solo lectura: C1 declara `fixtures/` inmutable."""
    return json.loads(ruta.read_text(encoding="utf-8"))


def leer_json_objeto(ruta: Path) -> dict[str, Any]:
    """Lee un fixture JSON que debe ser un objeto."""
    crudo = leer_json(ruta)
    assert isinstance(crudo, dict)
    return crudo


def leer_texto(ruta: Path) -> str:
    """Lee un fixture de texto plano (cotizacion.txt, factura.txt)."""
    return ruta.read_text(encoding="utf-8")


# ── Entrada: los 6 fixtures reales ─────────────────────────────────────────


@pytest.mark.parametrize("caso", CASOS)
def test_solicitud_valida_desde_fixture_real(caso: str) -> None:
    crudo = leer_json(SOLICITUDES / caso / "solicitud.json")

    solicitud = Solicitud.model_validate(crudo)

    assert solicitud.solicitud_id.startswith("SOL-2026-")
    assert solicitud.cantidad > 0
    assert isinstance(solicitud.valor_total, int)      # montos en int, no float
    assert isinstance(solicitud.fecha_solicitud, str)  # la fecha no se convierte aquí


@pytest.mark.parametrize("caso", CASOS)
def test_correo_valida_desde_fixture_real(caso: str) -> None:
    # Regresión: el correo.json real trae ocho campos (para, cuerpo, adjuntos,
    # nota_fixture) y el modelo declara cuatro. Con extra="forbid" esto fallaba,
    # y el fallo habría aparecido recién en Fase 2 al armar el Paquete.
    crudo = leer_json(SOLICITUDES / caso / "correo.json")
    assert isinstance(crudo, dict)
    assert "nota_fixture" in crudo, "el fixture dejó de traer campos extra"

    correo = Correo.model_validate(crudo)

    assert correo.id == f"{caso}-correo"
    assert correo.de.endswith("@periferia-ficticia.com")
    assert not hasattr(correo, "nota_fixture")  # el extra se descarta, no se arrastra


def test_sol_006_valida_con_los_tres_campos_opcionales_en_none() -> None:
    # sol-006 no trae proveedor_nit, indicador_iva ni condiciones_pago.
    # Son los disparadores de RC1 (derivado), RC6 y RC7.
    crudo = leer_json(SOLICITUDES / "sol-006" / "solicitud.json")

    solicitud = Solicitud.model_validate(crudo)

    assert solicitud.proveedor_nit is None
    assert solicitud.indicador_iva is None
    assert solicitud.condiciones_pago is None


def test_solicitud_ignora_campos_extra() -> None:
    # extra="ignore": un campo ajeno al dominio no debe romper la carga.
    crudo = leer_json(SOLICITUDES / "sol-001" / "solicitud.json")
    assert isinstance(crudo, dict)
    crudo["nota_fixture"] = "campo que no pertenece al dominio"

    solicitud = Solicitud.model_validate(crudo)

    assert not hasattr(solicitud, "nota_fixture")


# ── Aprobacion: modelo derivado, se construye campo por campo ──────────────
#
# `Aprobacion` NO es la forma del `aprobacion.json`. El fixture trae
# ['asunto', 'cc', 'cuerpo', 'de', 'fecha', 'para'] y el modelo declara
# ['de', 'fecha', 'aprobado', 'texto']: `aprobado` y `texto` no existen en el
# archivo, se **calculan** al leerlo. Por eso `extra="forbid"` es correcto y por
# eso `model_validate` del dict crudo tiene que fallar. Estos tests fijan la
# forma de construcción para que nadie en Fase 2 (T2.3 `oc_leer_paquete`) intente
# el parseo directo, que es la trampa en la que ya cayó `Correo`.


def construir_aprobacion(caso: str) -> Aprobacion:
    """Construye `Aprobacion` desde el fixture real, campo por campo.

    Deliberadamente **no** se usa `Aprobacion.model_validate(crudo)`: el modelo
    es derivado, no un espejo del archivo. Esta es la forma que debe reusar la
    herramienta de Fase 2.
    """
    crudo = leer_json_objeto(SOLICITUDES / caso / "aprobacion.json")
    cuerpo = crudo["cuerpo"]
    return Aprobacion(
        de=crudo["de"],
        fecha=crudo["fecha"],
        # El PRD solo pide detectar la palabra; la comparación va en minúsculas
        # porque el cuerpo real empieza con "Aprobado".
        aprobado="aprobado" in cuerpo.lower(),
        texto=cuerpo,
    )


@pytest.mark.parametrize("caso", CASOS)
def test_aprobacion_se_construye_campo_por_campo_desde_fixture_real(caso: str) -> None:
    aprobacion = construir_aprobacion(caso)

    assert aprobacion.de.endswith("@periferia-ficticia.com")
    assert aprobacion.fecha.startswith("2026-08-")
    assert aprobacion.aprobado is True          # los 6 fixtures están aprobados
    assert "probado" in aprobacion.texto        # el texto es el cuerpo íntegro


@pytest.mark.parametrize("caso", CASOS)
def test_aprobacion_rechaza_el_dict_crudo_del_fixture(caso: str) -> None:
    """Regresión del contrato, no un bug: el parseo directo DEBE fallar.

    Si algún día este test pasa, alguien relajó el `extra=` de `Aprobacion` y el
    modelo dejó de ser derivado.
    """
    crudo = leer_json_objeto(SOLICITUDES / caso / "aprobacion.json")

    with pytest.raises(ValidationError):
        Aprobacion.model_validate(crudo)


def test_aprobacion_rechaza_el_dict_crudo_aunque_se_enriquezca() -> None:
    # La variante que tentaría a Fase 2: agregar los derivados al dict crudo.
    # Sigue fallando por los extras del fixture ('para', 'cc', 'asunto', 'cuerpo').
    crudo = leer_json_objeto(SOLICITUDES / "sol-001" / "aprobacion.json")
    enriquecido = {**crudo, "aprobado": True, "texto": crudo["cuerpo"]}

    with pytest.raises(ValidationError):
        Aprobacion.model_validate(enriquecido)


# ── Cotizacion y Factura: derivadas de texto plano ─────────────────────────
#
# Nacen de `cotizacion.txt` y `factura.txt`, así que no existe dict crudo que
# tiente al parseo directo. El parser real es de Fase 2: estos tests solo fijan
# que los valores del fixture caben en el modelo.


def test_cotizacion_valida_con_datos_derivados_del_fixture_real() -> None:
    texto = leer_texto(SOLICITUDES / "sol-001" / "cotizacion.txt")

    cotizacion = Cotizacion(
        referencia="COT-TS-2026-0451",
        proveedor="TecnoSuministros S.A.S.",
        nit="900.555.111-2",
        total=11_400_000,                 # TOTAL (IVA incluido), en int
        moneda="COP",
        validez_hasta=None,               # el fixture dice "30 días", no una fecha
        item="Licencia antivirus corporativo 12 meses",
        texto=texto,
    )

    # Cada valor derivado tiene que existir literalmente en el fixture.
    assert cotizacion.referencia is not None
    assert cotizacion.referencia in texto
    assert cotizacion.proveedor in texto
    assert cotizacion.nit is not None
    assert cotizacion.nit in texto
    assert cotizacion.item is not None
    assert cotizacion.item in texto
    assert isinstance(cotizacion.total, int)


def test_cotizacion_acepta_los_opcionales_en_none() -> None:
    # sol-002 no cruza contra el maestro (bloqueo RC1): el modelo debe poder
    # representar una cotización con referencia, NIT, validez e ítem ausentes.
    cotizacion = Cotizacion(
        referencia=None,
        proveedor="Proveedor sin cruce",
        nit=None,
        total=1,
        moneda="COP",
        validez_hasta=None,
        item=None,
        texto="",
    )

    assert cotizacion.nit is None
    assert cotizacion.item is None


def test_factura_valida_con_datos_derivados_del_fixture_real() -> None:
    # Solo sol-005 trae factura: es el caso retroactivo del oráculo.
    texto = leer_texto(SOLICITUDES / "sol-005" / "factura.txt")

    factura = Factura(numero="FC-88231", fecha="2026-08-10", total=3_200_000)

    assert factura.numero in texto
    assert factura.fecha in texto
    assert isinstance(factura.total, int)


def test_factura_rechaza_campos_extra() -> None:
    with pytest.raises(ValidationError):
        Factura.model_validate(
            {"numero": "FC-88231", "fecha": "2026-08-10", "total": 1, "vendedor": "x"}
        )


# ── Paquete: el agregado completo del PRD §7.2 ─────────────────────────────


def construir_cotizacion_minima(caso: str) -> Cotizacion:
    """Cotización con el texto real del caso y los derivados en None.

    Basta para armar el `Paquete`: la extracción de campos es de Fase 2.
    """
    return Cotizacion(
        referencia=None,
        proveedor="",
        nit=None,
        total=0,
        moneda="COP",
        validez_hasta=None,
        item=None,
        texto=leer_texto(SOLICITUDES / caso / "cotizacion.txt"),
    )


def test_paquete_de_sol_001_valida_completo_sin_factura() -> None:
    paquete = Paquete(
        caso="sol-001",
        correo=Correo.model_validate(leer_json(SOLICITUDES / "sol-001" / "correo.json")),
        solicitud=Solicitud.model_validate(
            leer_json(SOLICITUDES / "sol-001" / "solicitud.json")
        ),
        cotizacion=construir_cotizacion_minima("sol-001"),
        aprobacion=construir_aprobacion("sol-001"),
        factura=None,                     # sol-001 no trae factura.txt
    )

    assert paquete.caso == "sol-001"
    assert paquete.correo.id == "sol-001-correo"
    assert paquete.solicitud.solicitud_id == "SOL-2026-001"
    assert paquete.aprobacion is not None
    assert paquete.factura is None
    assert paquete.faltantes == []        # HU-1: sin adjuntos ausentes
    assert not (SOLICITUDES / "sol-001" / "factura.txt").exists()


def test_paquete_de_sol_005_valida_completo_con_factura() -> None:
    paquete = Paquete(
        caso="sol-005",
        correo=Correo.model_validate(leer_json(SOLICITUDES / "sol-005" / "correo.json")),
        solicitud=Solicitud.model_validate(
            leer_json(SOLICITUDES / "sol-005" / "solicitud.json")
        ),
        cotizacion=construir_cotizacion_minima("sol-005"),
        aprobacion=construir_aprobacion("sol-005"),
        factura=Factura(numero="FC-88231", fecha="2026-08-10", total=3_200_000),
    )

    assert paquete.factura is not None
    assert paquete.factura.numero == "FC-88231"
    assert (SOLICITUDES / "sol-005" / "factura.txt").exists()


def test_paquete_acepta_los_tres_adjuntos_en_none_y_registra_faltantes() -> None:
    # Caso de borde del oráculo: paquete sin cotización ni aprobación.
    paquete = Paquete(
        caso="sol-sin-adjuntos",
        correo=Correo.model_validate(leer_json(SOLICITUDES / "sol-001" / "correo.json")),
        solicitud=Solicitud.model_validate(
            leer_json(SOLICITUDES / "sol-001" / "solicitud.json")
        ),
        cotizacion=None,
        aprobacion=None,
        factura=None,
        faltantes=["cotizacion.txt", "aprobacion.json"],
    )

    assert paquete.cotizacion is None
    assert paquete.aprobacion is None
    assert paquete.faltantes == ["cotizacion.txt", "aprobacion.json"]


def test_paquete_rechaza_campos_extra() -> None:
    with pytest.raises(ValidationError):
        Paquete.model_validate(
            {
                "caso": "sol-001",
                "correo": leer_json(SOLICITUDES / "sol-001" / "correo.json"),
                "solicitud": leer_json(SOLICITUDES / "sol-001" / "solicitud.json"),
                "cotizacion": None,
                "aprobacion": None,
                "factura": None,
                "campo_inventado": "x",
            }
        )


# ── Maestros: los 4 JSON reales ────────────────────────────────────────────


def test_maestros_validan_desde_los_cuatro_json_reales() -> None:
    maestros = Maestros.model_validate(
        {
            "proveedores": leer_json(MAESTROS / "proveedores.json"),
            "centros_costo": leer_json(MAESTROS / "centros-costo.json"),
            "indicadores_iva": leer_json(MAESTROS / "indicadores-iva.json"),
            "condiciones_pago": leer_json(MAESTROS / "condiciones-pago.json"),
        }
    )

    assert len(maestros.proveedores) == 5
    assert len(maestros.centros_costo) == 3
    assert len(maestros.indicadores_iva) == 3
    assert len(maestros.condiciones_pago) == 4


def test_maestros_conservan_la_estructura_anidada_de_aprobadores() -> None:
    maestros = Maestros.model_validate(
        {
            "proveedores": leer_json(MAESTROS / "proveedores.json"),
            "centros_costo": leer_json(MAESTROS / "centros-costo.json"),
            "indicadores_iva": leer_json(MAESTROS / "indicadores-iva.json"),
            "condiciones_pago": leer_json(MAESTROS / "condiciones-pago.json"),
        }
    )

    centros = {centro.centro_costo: centro for centro in maestros.centros_costo}
    assert len(centros["CC-1010"].aprobadores) == 2
    assert all(isinstance(ap.tope, int) for c in maestros.centros_costo for ap in c.aprobadores)


# ── Veredicto y ResultadoValidacion: la salida de los controles ────────────


def test_veredicto_de_bloqueo_rc1_valida() -> None:
    # El bloqueo RC1 de sol-002: el proveedor no cruza contra el maestro.
    veredicto = Veredicto(
        codigo="RC1",
        tipo="bloqueo",
        detalle="El proveedor 901999000 no existe en el maestro.",
        datos={"nit_normalizado": "901999000", "encontrado": False},
    )

    assert veredicto.codigo == "RC1"
    assert veredicto.tipo == "bloqueo"
    assert veredicto.datos["encontrado"] is False


def test_veredicto_sin_datos_arranca_con_dict_vacio() -> None:
    assert Veredicto(codigo="RC4", tipo="ok", detalle="Subárea válida.").datos == {}


@pytest.mark.parametrize(
    "tipo", ["ok", "bloqueo", "confirmacion", "derivado", "no_evaluable"]
)
def test_veredicto_acepta_los_cinco_tipos(tipo: str) -> None:
    assert Veredicto(codigo="RC1", tipo=tipo, detalle="x").tipo == tipo


def test_veredicto_rechaza_tipo_fuera_del_literal() -> None:
    with pytest.raises(ValidationError):
        Veredicto.model_validate({"codigo": "RC1", "tipo": "quizas", "detalle": "x"})


def test_resultado_validacion_apta_arranca_con_todo_vacio() -> None:
    resultado = ResultadoValidacion(apta=True)

    assert resultado.bloqueos == []
    assert resultado.confirmaciones == []
    assert resultado.derivados == {}
    assert resultado.retroactiva is False
    assert resultado.veredictos == []


def test_resultado_validacion_no_apta_conserva_bloqueo_y_derivados() -> None:
    # La forma que consume la Fase 2: un bloqueo RC1 más los derivados de RC6/RC7.
    bloqueo = Veredicto(codigo="RC1", tipo="bloqueo", detalle="Proveedor inexistente.")
    resultado = ResultadoValidacion(
        apta=False,
        bloqueos=[bloqueo],
        derivados={"indicador_iva": "C1", "condiciones_pago": "Z030"},
        retroactiva=True,
        veredictos=[bloqueo],
    )

    assert resultado.apta is False
    assert resultado.bloqueos[0].codigo == "RC1"
    assert resultado.derivados["indicador_iva"] == "C1"
    assert resultado.retroactiva is True


# ── ExcepcionOC: la estructura que el oráculo exige para sol-004 ───────────


def test_excepcion_oc_con_contenido_real_nace_sin_confirmar() -> None:
    # sol-004 excede el tope del aprobador: RC5 va al payload como excepción.
    excepcion = ExcepcionOC(
        codigo="RC5",
        detalle="Valor 25.000.000 supera el tope de 20.000.000 del aprobador.",
    )

    assert excepcion.codigo == "RC5"
    assert "tope" in excepcion.detalle
    # ADR-006: una excepción nace sin confirmar; al confirmarse se escribe el
    # session_id de quien lo hizo.
    assert excepcion.confirmado_por is None


def test_excepcion_oc_confirmada_guarda_quien_confirmo() -> None:
    excepcion = ExcepcionOC(codigo="RC5", detalle="x", confirmado_por="sesion-abc123")

    assert excepcion.confirmado_por == "sesion-abc123"


def test_excepcion_oc_rechaza_campos_extra() -> None:
    with pytest.raises(ValidationError):
        ExcepcionOC.model_validate({"codigo": "RC5", "detalle": "x", "nivel": "alto"})


# ── PosicionOC: el límite de 40 caracteres (I1 · ADR-004) ──────────────────


def posicion(descripcion: str, unidad: str = "UN") -> PosicionOC:
    return PosicionOC.model_validate(
        {
            "numero": 10,
            "descripcion": descripcion,
            "cantidad": 120,
            "unidad": unidad,
            "precio_unitario": 95000,
            "centro_costo": "CC-1010",
            "subarea": "Infraestructura",
            "indicador_iva": "C1",
        }
    )


def test_posicion_acepta_descripcion_de_40_caracteres() -> None:
    descripcion = "A" * 40

    resultado = posicion(descripcion)

    assert len(resultado.descripcion) == 40


def test_posicion_rechaza_descripcion_de_41_caracteres() -> None:
    # I1: el texto breve de SAP no admite más de 40 caracteres.
    with pytest.raises(ValidationError):
        posicion("A" * 41)


def test_posicion_rechaza_unidad_fuera_del_literal() -> None:
    with pytest.raises(ValidationError):
        posicion("Licencia antivirus corporativo", unidad="KG")


@pytest.mark.parametrize("unidad", ["UN", "H", "MES"])
def test_posicion_acepta_las_tres_unidades_del_prd(unidad: str) -> None:
    assert posicion("Licencia antivirus corporativo", unidad=unidad).unidad == unidad


# ── OrdenCompra: el payload del PRD §7.4 ───────────────────────────────────


def orden(**sobrescritos: object) -> OrdenCompra:
    payload: dict[str, object] = {
        "referencia": ReferenciaOC(
            solicitud_id="SOL-2026-001",
            correo_id="MAIL-001",
            cotizacion_ref="COT-TS-2026-0451",
        ).model_dump(),
        "proveedor": {
            "codigo_sap": "100234",
            "nit": "900555111",
            "nombre": "TecnoSuministros S.A.S.",
        },
        "moneda": "COP",
        "condiciones_pago": "Z030",
        "aprobador": AprobadorOC(
            email="mlopez@periferia-ficticia.com",
            fecha_aprobacion="2026-08-21",
            evidencia_sha256="0" * 64,
        ).model_dump(),
        "posiciones": [posicion("Licencia antivirus corporativo").model_dump()],
    }
    payload.update(sobrescritos)
    return OrdenCompra.model_validate(payload)


def test_orden_sin_sociedad_queda_en_1000() -> None:
    # I4: sociedad y organizacion_compras son constantes del payload.
    resultado = orden()

    assert resultado.sociedad == "1000"
    assert resultado.organizacion_compras == "1000"


def test_orden_rechaza_posiciones_vacias() -> None:
    with pytest.raises(ValidationError):
        orden(posiciones=[])


def test_orden_rechaza_moneda_fuera_del_literal() -> None:
    # I9: solo COP y USD.
    with pytest.raises(ValidationError):
        orden(moneda="EUR")


def test_orden_rechaza_campos_extra() -> None:
    with pytest.raises(ValidationError):
        orden(campo_inventado="x")


def test_orden_sin_excepciones_arranca_con_lista_vacia() -> None:
    assert orden().excepciones == []


# ── ok() / error(): el contrato uniforme de herramienta (C3) ───────────────


def test_ok_devuelve_string_no_dict() -> None:
    resultado = ok({"a": 1})

    assert isinstance(resultado, str)
    assert resultado == '{"ok": true, "data": {"a": 1}}'


def test_ok_es_json_parseable_con_ok_true() -> None:
    cargado = json.loads(ok({"numero_oc": "4500000001"}))

    assert cargado["ok"] is True
    assert cargado["data"] == {"numero_oc": "4500000001"}


def test_ok_no_escapa_los_acentos() -> None:
    # ensure_ascii=False: "Papelería" debe leerse tal cual en el log y en el chat.
    resultado = ok({"x": "Papelería"})

    assert "Papelería" in resultado
    assert "\\u" not in resultado


def test_error_devuelve_string_parseable_con_ok_false() -> None:
    resultado = error("falló")

    assert isinstance(resultado, str)
    cargado = json.loads(resultado)
    assert cargado["ok"] is False
    assert cargado["error"] == "falló"
    assert "data" not in cargado
