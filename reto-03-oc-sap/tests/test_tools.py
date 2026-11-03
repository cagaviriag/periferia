"""Tests de las cinco herramientas P0 y del parseo (T2.2).

El test que importa es `test_matriz_del_oraculo`: recorre los 6 casos de punta a
punta, sin LLM, llamando a las herramientas en el orden del proceso y afirmando
contra la matriz de `specs/01-oracle.md`.

Dos decisiones de andamiaje:

- **Raíz aislada por test** (`raiz`): `fixtures/` entra por un enlace simbólico de
  solo lectura y `out/` queda dentro del `tmp_path`. Así la numeración de OC
  arranca siempre en `4500000001` (I2) y ningún test ve el `out/` de otro.
- **`asyncio.run` en vez de `pytest-asyncio`**: `pyproject.toml` fija las
  dependencias de desarrollo (T0.1) y sumar un plugin exigiría otra ADR.

Las aserciones del truncado son por **regla**, no por string literal (ADR-004).
"""

import asyncio
import json
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import pytest

from app.core.log import ENCABEZADO_CONTROL
from app.core.modelos import OrdenCompra
from app.tools.oc import (
    HERRAMIENTAS,
    ArgsCaso,
    ArgsConstruirPayload,
    ArgsCrear,
    ArgsValidar,
    Contexto,
    oc_construir_payload,
    oc_crear,
    oc_generar_evidencia,
    oc_leer_paquete,
    oc_validar,
)
from app.tools.parseo import parsear_cotizacion, parsear_factura, parsear_monto
from tests.conftest import CASOS, FIXTURES, construir_paquete

# ── Matriz del oráculo (specs/01-oracle.md §Matriz de resultados) ──────────

MATRIZ: dict[str, dict[str, Any]] = {
    "sol-001": {
        "apta": True,
        "bloqueos": [],
        "confirmaciones": [],
        "derivados": {},
        "retroactiva": False,
        "crea_sin_confirmar": True,
    },
    "sol-002": {
        "apta": False,
        "bloqueos": ["RC1"],
        "confirmaciones": [],
        "derivados": {},
        "retroactiva": False,
        "crea_sin_confirmar": False,
    },
    "sol-003": {
        "apta": False,
        "bloqueos": ["RC2"],
        "confirmaciones": [],
        "derivados": {},
        "retroactiva": False,
        "crea_sin_confirmar": False,
    },
    "sol-004": {
        "apta": True,
        "bloqueos": [],
        "confirmaciones": ["RC5"],
        "derivados": {},
        "retroactiva": False,
        "crea_sin_confirmar": False,
    },
    "sol-005": {
        "apta": True,
        "bloqueos": [],
        "confirmaciones": ["RC8"],
        "derivados": {},
        "retroactiva": True,
        "crea_sin_confirmar": False,
    },
    "sol-006": {
        "apta": True,
        "bloqueos": [],
        "confirmaciones": ["RC6"],
        "derivados": {"indicador_iva": "C1", "condiciones_pago": "Z030"},
        "retroactiva": False,
        "crea_sin_confirmar": False,
    },
}

PRIMERA_OC = "4500000001"


# ── Andamiaje ──────────────────────────────────────────────────────────────


@pytest.fixture
def raiz(tmp_path: Path) -> Path:
    """Raíz de proyecto aislada: `fixtures/` enlazado, `out/` propio."""
    (tmp_path / "fixtures").symlink_to(FIXTURES.parent)
    return tmp_path


@pytest.fixture
def ctx(raiz: Path) -> Contexto:
    return Contexto(raiz=raiz, session_id="sesion-de-prueba")


def llamar(corrutina: Awaitable[str]) -> dict[str, Any]:
    """Ejecuta una herramienta y devuelve su JSON ya parseado.

    Verifica de paso el contrato C3 en **cada** llamada de la suite: lo que
    devuelve es un `str`, y es JSON con la bandera `ok`.
    """
    respuesta = asyncio.run(corrutina)
    assert isinstance(respuesta, str), "una herramienta devolvió algo que no es str"
    cuerpo = json.loads(respuesta)
    assert isinstance(cuerpo, dict)
    assert isinstance(cuerpo["ok"], bool)
    return cuerpo


def datos(cuerpo: dict[str, Any]) -> dict[str, Any]:
    """El `data` de una respuesta exitosa; falla con el error si no lo fue."""
    assert cuerpo["ok"] is True, cuerpo.get("error")
    contenido = cuerpo["data"]
    assert isinstance(contenido, dict)
    return contenido


def codigos(veredictos: list[dict[str, Any]]) -> list[str]:
    return [veredicto["codigo"] for veredicto in veredictos]


def payload_del_caso(ctx: Contexto, caso: str, derivados: dict[str, str]) -> dict[str, Any]:
    """Evidencia + payload, en el orden obligatorio del proceso."""
    datos(llamar(oc_generar_evidencia(ArgsCaso(caso=caso), ctx)))
    construido = datos(
        llamar(oc_construir_payload(ArgsConstruirPayload(caso=caso, derivados=derivados), ctx))
    )
    payload = construido["payload"]
    assert isinstance(payload, dict)
    return payload


def leer_control(raiz: Path) -> list[dict[str, str]]:
    """Filas de `out/control.csv` como diccionarios."""
    lineas = (raiz / "out" / "control.csv").read_text(encoding="utf-8").splitlines()
    assert lineas[0].split(",") == ENCABEZADO_CONTROL
    return [dict(zip(ENCABEZADO_CONTROL, linea.split(","), strict=False)) for linea in lineas[1:]]


# ── El test que importa: los 6 casos end-to-end ────────────────────────────


@pytest.mark.parametrize("caso", CASOS)
def test_matriz_del_oraculo(ctx: Contexto, caso: str) -> None:
    """Los 6 casos reproducen `specs/01-oracle.md` sin pasar por el modelo."""
    esperado = MATRIZ[caso]

    validacion = datos(llamar(oc_validar(ArgsValidar(caso=caso), ctx)))
    assert validacion["apta"] is esperado["apta"]
    assert codigos(validacion["bloqueos"]) == esperado["bloqueos"]
    assert codigos(validacion["confirmaciones"]) == esperado["confirmaciones"]
    assert validacion["derivados"] == esperado["derivados"]
    assert validacion["retroactiva"] is esperado["retroactiva"]
    # Los diez veredictos viajan completos para que la UI pinte la matriz.
    assert len(validacion["veredictos"]) == 10

    if esperado["bloqueos"]:
        _afirmar_bloqueo_no_se_levanta(ctx, caso, esperado["bloqueos"])
        return

    payload = payload_del_caso(ctx, caso, validacion["derivados"])

    sin_confirmar = llamar(oc_crear(ArgsCrear(caso=caso, payload=payload), ctx))
    if esperado["crea_sin_confirmar"]:
        creada = datos(sin_confirmar)
        assert creada["numero_oc"] == PRIMERA_OC          # I2
        assert creada["idempotente"] is False
        return

    # Hay confirmaciones: sin confirmar no se crea, y el mensaje lo dice.
    assert sin_confirmar["ok"] is False
    assert "requiere confirmación" in sin_confirmar["error"]

    confirmada = datos(llamar(oc_crear(ArgsCrear(caso=caso, payload=payload, confirmado=True), ctx)))
    assert confirmada["numero_oc"] == PRIMERA_OC
    assert confirmada["retroactiva"] is esperado["retroactiva"]


def _afirmar_bloqueo_no_se_levanta(ctx: Contexto, caso: str, bloqueos: list[str]) -> None:
    """Un bloqueo no se crea ni con `confirmado=True` (HU-2).

    Es la diferencia gradeable entre bloqueo y confirmación: sol-002 y sol-003
    siguen sin OC aunque el usuario confirme.
    """
    paquete = construir_paquete(caso)
    payload = _payload_que_miente(paquete.solicitud.solicitud_id)

    for confirmado in (False, True):
        respuesta = llamar(
            oc_crear(ArgsCrear(caso=caso, payload=payload, confirmado=confirmado), ctx)
        )
        assert respuesta["ok"] is False
        for codigo in bloqueos:
            assert codigo in respuesta["error"]

    assert not (ctx.raiz / "out" / "sap" / "ordenes.jsonl").exists()


def _payload_que_miente(solicitud_id: str) -> dict[str, Any]:
    """Un payload bien formado y sin excepciones, para un caso que está bloqueado.

    Simula a un modelo que afirma que ya validó y que todo está en orden. Sirve
    para probar que `oc_crear` no le cree (CA2): la validación se vuelve a correr
    sobre los fixtures y el bloqueo aparece igual.
    """
    return {
        "referencia": {"solicitud_id": solicitud_id, "correo_id": "x", "cotizacion_ref": None},
        "proveedor": {"codigo_sap": "100234", "nit": "900555111", "nombre": "Inventado"},
        "moneda": "COP",
        "condiciones_pago": "Z030",
        "aprobador": {"email": "a@b.com", "fecha_aprobacion": "2026-08-21", "evidencia_sha256": ""},
        "posiciones": [
            {
                "numero": 10,
                "descripcion": "lo que sea",
                "cantidad": 1,
                "unidad": "UN",
                "precio_unitario": 1,
                "centro_costo": "CC-1010",
                "subarea": "Soporte",
                "indicador_iva": "C1",
            }
        ],
        "excepciones": [],
    }


# ── Contrato C3 · I8: string JSON y cero excepciones ──────────────────────


def test_las_cinco_devuelven_string_json(ctx: Contexto) -> None:
    """Las 5 herramientas devuelven `str` parseable con `ok` (I8)."""
    caso = "sol-001"
    assert datos(llamar(oc_leer_paquete(ArgsCaso(caso=caso), ctx)))["caso"] == caso
    validacion = datos(llamar(oc_validar(ArgsValidar(caso=caso), ctx)))
    evidencia = datos(llamar(oc_generar_evidencia(ArgsCaso(caso=caso), ctx)))
    assert set(evidencia) == {"ruta", "sha256"}
    payload = payload_del_caso(ctx, caso, validacion["derivados"])
    creada = datos(llamar(oc_crear(ArgsCrear(caso=caso, payload=payload), ctx)))
    assert set(creada) == {"numero_oc", "fecha", "idempotente", "retroactiva"}


@pytest.mark.parametrize(
    "caso",
    ["no-existe", "../../etc", "/etc/passwd", "sol-001/../sol-002", "", "sol-00*"],
)
def test_caso_invalido_en_las_cinco(ctx: Contexto, caso: str) -> None:
    """Ninguna herramienta lanza con un caso inexistente o con recorrido de rutas."""
    respuestas = [
        llamar(oc_leer_paquete(ArgsCaso(caso=caso), ctx)),
        llamar(oc_validar(ArgsValidar(caso=caso), ctx)),
        llamar(oc_generar_evidencia(ArgsCaso(caso=caso), ctx)),
        llamar(oc_construir_payload(ArgsConstruirPayload(caso=caso), ctx)),
        llamar(oc_crear(ArgsCrear(caso=caso, payload={}), ctx)),
    ]
    for respuesta in respuestas:
        assert respuesta["ok"] is False
        assert "error" in respuesta


def test_solicitud_malformada_no_lanza(ctx: Contexto, raiz: Path) -> None:
    """Un `solicitud.json` ilegible devuelve `{ok: false}` con sugerencia (HU-6)."""
    caso = _caso_propio(raiz, "sol-001", "roto", {"solicitud.json": "{esto no es json"})

    respuesta = llamar(oc_leer_paquete(ArgsCaso(caso=caso), ctx))
    assert respuesta["ok"] is False
    assert "solicitud.json" in respuesta["error"]
    assert "reenvíe" in respuesta["error"]

    for otra in (
        llamar(oc_validar(ArgsValidar(caso=caso), ctx)),
        llamar(oc_construir_payload(ArgsConstruirPayload(caso=caso), ctx)),
        llamar(oc_crear(ArgsCrear(caso=caso, payload={}), ctx)),
    ):
        assert otra["ok"] is False


def test_monto_no_numerico_no_lanza(ctx: Contexto, raiz: Path) -> None:
    """Un `valor_total` no numérico es un error legible, no una excepción."""
    crudo = json.loads((FIXTURES / "solicitudes" / "sol-001" / "solicitud.json").read_text())
    crudo["valor_total"] = "once millones"
    caso = _caso_propio(raiz, "sol-001", "monto", {"solicitud.json": json.dumps(crudo)})

    respuesta = llamar(oc_validar(ArgsValidar(caso=caso), ctx))
    assert respuesta["ok"] is False
    assert "valor_total" in respuesta["error"]


def test_adjunto_ausente_no_lanza(ctx: Contexto, raiz: Path) -> None:
    """Sin `aprobacion.json` ni `cotizacion.txt`: campos en None y `faltantes` (HU-1)."""
    caso = _caso_propio(raiz, "sol-001", "pelado", {}, borrar=["aprobacion.json", "cotizacion.txt"])

    paquete = datos(llamar(oc_leer_paquete(ArgsCaso(caso=caso), ctx)))
    assert paquete["aprobacion"] is None
    assert paquete["cotizacion"] is None
    assert sorted(paquete["faltantes"]) == ["aprobacion.json", "cotizacion.txt"]

    # Sin aprobación, RC2 bloquea; la herramienta no se cae.
    validacion = datos(llamar(oc_validar(ArgsValidar(caso=caso), ctx)))
    assert "RC2" in codigos(validacion["bloqueos"])


def _caso_propio(
    raiz: Path,
    base: str,
    nombre: str,
    reemplazos: dict[str, str],
    borrar: list[str] | None = None,
) -> str:
    """Copia un caso de fixtures dentro de la raíz del test y lo altera.

    `fixtures/` es de solo lectura (C1): el caso corrupto se construye en el
    `tmp_path`, nunca sobre el original. El enlace simbólico se sustituye por una
    copia real para poder añadir carpetas.
    """
    enlace = raiz / "fixtures"
    destino_real = raiz / "fixtures-copia"
    if enlace.is_symlink():
        import shutil
        import stat

        shutil.copytree(enlace.resolve(), destino_real)
        enlace.unlink()
        destino_real.rename(enlace)
        # `fixtures/` original está en solo lectura (C1) y `copytree` copia los
        # permisos; la copia del test sí tiene que poder escribirse.
        for elemento in [enlace, *enlace.rglob("*")]:
            elemento.chmod(elemento.stat().st_mode | stat.S_IWUSR)

    origen = enlace / "reto-03" / "solicitudes" / base
    carpeta = enlace / "reto-03" / "solicitudes" / nombre
    carpeta.mkdir(parents=True)
    for archivo in origen.iterdir():
        (carpeta / archivo.name).write_bytes(archivo.read_bytes())
    for archivo in borrar or []:
        (carpeta / archivo).unlink()
    for archivo, contenido in reemplazos.items():
        (carpeta / archivo).write_text(contenido, encoding="utf-8")
    return nombre


# ── Idempotencia (I3) ─────────────────────────────────────────────────────


def test_idempotencia_de_sol_001(ctx: Contexto, raiz: Path) -> None:
    """Dos `oc_crear` del mismo caso: mismo número y **una sola** línea (I3)."""
    validacion = datos(llamar(oc_validar(ArgsValidar(caso="sol-001"), ctx)))
    payload = payload_del_caso(ctx, "sol-001", validacion["derivados"])

    primera = datos(llamar(oc_crear(ArgsCrear(caso="sol-001", payload=payload), ctx)))
    segunda = datos(llamar(oc_crear(ArgsCrear(caso="sol-001", payload=payload), ctx)))

    assert primera["numero_oc"] == segunda["numero_oc"] == PRIMERA_OC
    assert primera["idempotente"] is False
    assert segunda["idempotente"] is True

    lineas = (raiz / "out" / "sap" / "ordenes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([linea for linea in lineas if linea.strip()]) == 1

    # I6: dos intentos, dos filas; la segunda queda marcada como idempotente.
    filas = leer_control(raiz)
    assert [fila["resultado"] for fila in filas] == ["creada", "idempotente"]


# ── Invariantes del payload ───────────────────────────────────────────────


@pytest.mark.parametrize("caso", [caso for caso in CASOS if not MATRIZ[caso]["bloqueos"]])
def test_invariantes_del_payload(ctx: Contexto, caso: str) -> None:
    """I1, I4, I5, I9 y los campos obligatorios del payload."""
    validacion = datos(llamar(oc_validar(ArgsValidar(caso=caso), ctx)))
    payload = payload_del_caso(ctx, caso, validacion["derivados"])
    orden = OrdenCompra.model_validate(payload)          # el payload revalida

    assert orden.sociedad == "1000"                      # I4
    assert orden.organizacion_compras == "1000"
    assert orden.moneda == "COP"                         # I9
    assert [posicion.numero for posicion in orden.posiciones] == [10]   # I5

    posicion = orden.posiciones[0]
    completa = construir_paquete(caso).solicitud.descripcion
    # I1 · ADR-004: por regla, nunca por string literal.
    assert len(posicion.descripcion) <= 40
    assert completa.startswith(posicion.descripcion)
    assert posicion.descripcion == posicion.descripcion.rstrip()
    assert posicion.unidad in ("UN", "H", "MES")

    # Una excepción por confirmación, sin confirmar todavía (ADR-006).
    assert codigos([e.model_dump() for e in orden.excepciones]) == MATRIZ[caso]["confirmaciones"]
    assert all(excepcion.confirmado_por is None for excepcion in orden.excepciones)


def test_unidad_se_infiere_del_item_de_la_cotizacion(ctx: Contexto) -> None:
    """sol-004 cotiza "Hora de arquitecto…" → `H`; sol-001 licencias → `UN` (ADR-005)."""
    validacion = datos(llamar(oc_validar(ArgsValidar(caso="sol-004"), ctx)))
    assert payload_del_caso(ctx, "sol-004", validacion["derivados"])["posiciones"][0]["unidad"] == "H"
    assert payload_del_caso(ctx, "sol-001", {})["posiciones"][0]["unidad"] == "UN"


def test_derivados_de_sol_006_llegan_al_payload(ctx: Contexto) -> None:
    """RC6 y RC7 completan el IVA y las condiciones de pago desde el proveedor."""
    validacion = datos(llamar(oc_validar(ArgsValidar(caso="sol-006"), ctx)))
    payload = payload_del_caso(ctx, "sol-006", validacion["derivados"])
    assert payload["condiciones_pago"] == "Z030"
    assert payload["posiciones"][0]["indicador_iva"] == "C1"


def test_trazabilidad_asigna_fuente_a_cada_campo(ctx: Contexto, raiz: Path) -> None:
    """I7 · ADR-004: cada campo tiene fuente y la descripción completa se preserva."""
    validacion = datos(llamar(oc_validar(ArgsValidar(caso="sol-001"), ctx)))
    # La evidencia va primero: el payload consume su sha256.
    datos(llamar(oc_generar_evidencia(ArgsCaso(caso="sol-001"), ctx)))
    construido = datos(
        llamar(
            oc_construir_payload(
                ArgsConstruirPayload(caso="sol-001", derivados=validacion["derivados"]), ctx
            )
        )
    )
    assert construido["ruta_trazabilidad"] == "out/sol-001/trazabilidad.json"

    traza = json.loads((raiz / "out" / "sol-001" / "trazabilidad.json").read_text(encoding="utf-8"))
    completa = construir_paquete("sol-001").solicitud.descripcion
    assert traza["descripcion_completa"] == completa
    assert len(completa) > 40                            # si no, el test no probaría nada

    fuentes_validas = {
        "solicitud",
        "correo",
        "cotizacion",
        "aprobacion",
        "derivado",
        "constante",
        "evidencia",
        "validacion",
    }
    for campo, entrada in traza["campos"].items():
        assert entrada["fuente"], f"{campo} sin fuente"
        assert entrada["fuente"].startswith("maestro.") or entrada["fuente"] in fuentes_validas
    assert traza["campos"]["proveedor.codigo_sap"]["fuente"] == "maestro.proveedores"


# ── Orden del proceso y evidencia (HU-4) ──────────────────────────────────


def test_payload_exige_la_evidencia_primero(ctx: Contexto) -> None:
    """Sin evidencia, `oc_construir_payload` no inventa el hash: lo pide."""
    respuesta = llamar(oc_construir_payload(ArgsConstruirPayload(caso="sol-001"), ctx))
    assert respuesta["ok"] is False
    assert "genera primero la evidencia" in respuesta["error"]


def test_evidencia_y_su_hash(ctx: Contexto, raiz: Path) -> None:
    """El sha256 es del contenido exacto escrito y viaja al payload."""
    import hashlib

    evidencia = datos(llamar(oc_generar_evidencia(ArgsCaso(caso="sol-001"), ctx)))
    archivo = raiz / "out" / "sol-001" / "aprobacion.txt"
    contenido = archivo.read_text(encoding="utf-8")

    assert evidencia["ruta"] == "out/sol-001/aprobacion.txt"
    assert evidencia["sha256"] == hashlib.sha256(contenido.encode("utf-8")).hexdigest()
    for encabezado in ("De: ", "Para: ", "Fecha: ", "Asunto: "):
        assert encabezado in contenido
    assert "Aprobado, proceder con la orden de compra." in contenido

    payload = datos(llamar(oc_construir_payload(ArgsConstruirPayload(caso="sol-001"), ctx)))
    assert payload["payload"]["aprobador"]["evidencia_sha256"] == evidencia["sha256"]


# ── CA2: la herramienta no cree lo que le manden ──────────────────────────


def test_oc_validar_no_usa_el_paquete_alterado(ctx: Contexto) -> None:
    """Un paquete manipulado no cambia la validación: se relee de fixtures (CA2)."""
    paquete = datos(llamar(oc_leer_paquete(ArgsCaso(caso="sol-002"), ctx)))
    paquete["solicitud"]["proveedor_nit"] = "900555111"   # el NIT de un proveedor que sí existe

    validacion = datos(llamar(oc_validar(ArgsValidar(caso="sol-002", paquete=paquete), ctx)))
    assert codigos(validacion["bloqueos"]) == ["RC1"]


def test_oc_validar_rechaza_un_paquete_de_otro_caso(ctx: Contexto) -> None:
    paquete = datos(llamar(oc_leer_paquete(ArgsCaso(caso="sol-001"), ctx)))
    respuesta = llamar(oc_validar(ArgsValidar(caso="sol-004", paquete=paquete), ctx))
    assert respuesta["ok"] is False
    assert "sol-001" in respuesta["error"]


def test_payload_rechaza_un_derivado_inventado(ctx: Contexto) -> None:
    """Un derivado que ningún control calculó no se escribe en la OC (CA2)."""
    datos(llamar(oc_generar_evidencia(ArgsCaso(caso="sol-001"), ctx)))
    respuesta = llamar(
        oc_construir_payload(
            ArgsConstruirPayload(caso="sol-001", derivados={"indicador_iva": "C9"}), ctx
        )
    )
    assert respuesta["ok"] is False
    assert "C9" in respuesta["error"]


def test_oc_crear_rechaza_un_payload_de_otra_solicitud(ctx: Contexto) -> None:
    validacion = datos(llamar(oc_validar(ArgsValidar(caso="sol-001"), ctx)))
    payload = payload_del_caso(ctx, "sol-001", validacion["derivados"])

    respuesta = llamar(oc_crear(ArgsCrear(caso="sol-004", payload=payload), ctx))
    assert respuesta["ok"] is False
    assert "SOL-2026-001" in respuesta["error"]


def test_confirmado_escribe_la_sesion_en_la_excepcion(ctx: Contexto, raiz: Path) -> None:
    """ADR-006: `confirmado_por` queda con el `session_id` de quien confirmó."""
    validacion = datos(llamar(oc_validar(ArgsValidar(caso="sol-004"), ctx)))
    payload = payload_del_caso(ctx, "sol-004", validacion["derivados"])
    datos(llamar(oc_crear(ArgsCrear(caso="sol-004", payload=payload, confirmado=True), ctx)))

    guardada = json.loads(
        (raiz / "out" / "sap" / "ordenes.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    excepciones = guardada["orden"]["excepciones"]
    assert [excepcion["codigo"] for excepcion in excepciones] == ["RC5"]
    assert excepciones[0]["confirmado_por"] == ctx.session_id


# ── I6 · CA4: bitácoras ───────────────────────────────────────────────────


def test_una_fila_de_control_por_intento(ctx: Contexto, raiz: Path) -> None:
    """I6: tras los 6 casos, `control.csv` tiene una fila por intento."""
    # Un caso bloqueado muere en `oc_validar`, que es donde se registra su
    # intento: un bloqueo no es confirmable, así que el agente no llama a
    # `oc_crear`. Registrar en los dos sitios duplicaría la fila.
    desenlaces: list[str] = []
    for caso in CASOS:
        validacion = datos(llamar(oc_validar(ArgsValidar(caso=caso), ctx)))
        if validacion["bloqueos"]:
            desenlaces.append("bloqueada")
            continue
        payload = payload_del_caso(ctx, caso, validacion["derivados"])
        llamar(oc_crear(ArgsCrear(caso=caso, payload=payload, confirmado=True), ctx))
        desenlaces.append("creada")

    filas = leer_control(raiz)
    assert [fila["resultado"] for fila in filas] == desenlaces
    assert [fila["solicitud_id"] for fila in filas] == [
        f"SOL-2026-00{numero}" for numero in range(1, 7)
    ]
    # sol-005 es el único retroactivo y así debe quedar registrado (O4 · §2.2.3).
    assert [fila["retroactiva"] for fila in filas] == ["false"] * 4 + ["true", "false"]


def test_cada_llamada_queda_en_la_bitacora(ctx: Contexto, raiz: Path) -> None:
    """CA4: una línea por llamada en `out/log.jsonl`, sin volcar el payload."""
    llamar(oc_leer_paquete(ArgsCaso(caso="sol-001"), ctx))
    llamar(oc_validar(ArgsValidar(caso="no-existe"), ctx))

    lineas = (raiz / "out" / "log.jsonl").read_text(encoding="utf-8").splitlines()
    registros = [json.loads(linea) for linea in lineas]
    assert [registro["herramienta"] for registro in registros] == ["oc_leer_paquete", "oc_validar"]
    assert [registro["ok"] for registro in registros] == [True, False]
    assert all(registro["caso"] is not None for registro in registros)
    # El resumen es una frase corta, nunca el paquete entero (C8).
    assert all(len(registro["resumen"]) <= 400 for registro in registros)


# ── Registro de herramientas (PRD §6.2) ───────────────────────────────────


def test_el_registro_declara_las_cinco_herramientas() -> None:
    nombres = [herramienta.nombre for herramienta in HERRAMIENTAS]
    assert nombres == [
        "oc_leer_paquete",
        "oc_validar",
        "oc_generar_evidencia",
        "oc_construir_payload",
        "oc_crear",
    ]
    for herramienta in HERRAMIENTAS:
        assert herramienta.description.strip()
        # El esquema de args es lo que alimenta el JSON Schema del tool-calling.
        assert "properties" in herramienta.args.model_json_schema()


def test_execute_valida_los_argumentos_crudos(ctx: Contexto) -> None:
    """El registro valida antes de ejecutar y devuelve el error, no lanza (PRD §6.2)."""
    leer = HERRAMIENTAS[0]
    assert datos(llamar(leer.execute({"caso": "sol-001"}, ctx)))["caso"] == "sol-001"

    for crudos in ({}, {"caso": 5}, {"caso": "sol-001", "sobra": 1}):
        respuesta = llamar(leer.execute(crudos, ctx))
        assert respuesta["ok"] is False
        assert "Argumentos inválidos" in respuesta["error"]


# ── Parseo (T2.2 · hallazgo H1) ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("COP 11.400.000", 11400000),
        (" COP 3.200.000 ", 3200000),
        ("USD 1.000", 1000),
        ("95.000", 95000),
        ("once millones", None),
        ("", None),
        ("COP", None),
    ],
)
def test_parsear_monto(texto: str, esperado: int | None) -> None:
    assert parsear_monto(texto) == esperado


def test_parsear_cotizacion_de_sol_001() -> None:
    texto = (FIXTURES / "solicitudes" / "sol-001" / "cotizacion.txt").read_text(encoding="utf-8")
    campos = parsear_cotizacion(texto)
    assert campos is not None
    assert campos["referencia"] == "COT-TS-2026-0451"
    assert campos["proveedor"] == "TecnoSuministros S.A.S."
    assert campos["nit"] == "900555111"                    # ADR-008: ya normalizado
    assert campos["total"] == 11400000
    assert campos["moneda"] == "COP"
    assert campos["item"] == "Licencia antivirus corporativo 12 meses"
    # Fecha 2026-08-18 + 30 días de validez.
    assert campos["validez_hasta"] == "2026-09-17"


def test_parsear_cotizacion_toma_el_total_con_iva_no_la_base() -> None:
    """El total es el de `TOTAL (IVA incluido):`, no la base gravable."""
    for caso, total in [("sol-004", 26500000), ("sol-005", 3200000), ("sol-006", 5400000)]:
        texto = (FIXTURES / "solicitudes" / caso / "cotizacion.txt").read_text(encoding="utf-8")
        campos = parsear_cotizacion(texto)
        assert campos is not None
        assert campos["total"] == total


def test_parsear_cotizacion_sin_total_devuelve_none() -> None:
    assert parsear_cotizacion("COTIZACIÓN X-1\nProveedor: Nadie\n") is None
    assert parsear_cotizacion("") is None


def test_parsear_cotizacion_sin_validez_no_inventa_fecha() -> None:
    """CA2: sin los días de validez, `validez_hasta` es None."""
    campos = parsear_cotizacion("Fecha: 2026-08-18\nTOTAL (IVA incluido): COP 100\n")
    assert campos is not None
    assert campos["validez_hasta"] is None
    campos_sin_fecha = parsear_cotizacion(
        "TOTAL (IVA incluido): COP 100\nValidez de la oferta: 30 días\n"
    )
    assert campos_sin_fecha is not None
    assert campos_sin_fecha["validez_hasta"] is None


def test_parsear_factura_de_sol_005() -> None:
    """La factura usa `TOTAL:`, no `TOTAL (IVA incluido):`."""
    texto = (FIXTURES / "solicitudes" / "sol-005" / "factura.txt").read_text(encoding="utf-8")
    campos = parsear_factura(texto)
    assert campos == {"numero": "FC-88231", "fecha": "2026-08-10", "total": 3200000}


def test_parsear_factura_incompleta_devuelve_none() -> None:
    assert parsear_factura("TOTAL: COP 100\n") is None
    assert parsear_factura("") is None
    # Una cotización no es una factura: su `TOTAL (IVA incluido)` no cuela.
    cotizacion = (FIXTURES / "solicitudes" / "sol-001" / "cotizacion.txt").read_text(
        encoding="utf-8"
    )
    assert parsear_factura(cotizacion) is None


def test_el_parseo_de_produccion_coincide_con_el_del_andamiaje(ctx: Contexto) -> None:
    """`oc_leer_paquete` y `construir_paquete` leen lo mismo (hallazgo H1).

    El andamiaje de `tests/conftest.py` nació antes que el parser real. Este test
    fija que no divergen en los campos que usan los controles.
    """
    for caso in CASOS:
        producido = datos(llamar(oc_leer_paquete(ArgsCaso(caso=caso), ctx)))
        andamiaje = construir_paquete(caso)
        assert producido["solicitud"] == andamiaje.solicitud.model_dump(mode="json")
        assert producido["aprobacion"] == (
            andamiaje.aprobacion.model_dump(mode="json") if andamiaje.aprobacion else None
        )
        assert producido["factura"] == (
            andamiaje.factura.model_dump(mode="json") if andamiaje.factura else None
        )
        assert andamiaje.cotizacion is not None
        assert producido["cotizacion"]["total"] == andamiaje.cotizacion.total
        assert producido["cotizacion"]["item"] == andamiaje.cotizacion.item
