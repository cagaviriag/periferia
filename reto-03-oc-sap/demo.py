#!/usr/bin/env python
"""Verificación sin modelo de lenguaje — PRD §6.6.

Procesa los 6 casos de `fixtures/reto-03/solicitudes/` llamando **directamente
a las herramientas**, sin consumir un solo token. Demuestra que el determinismo
de la solución no sale del modelo: sale de las herramientas y de los diez
controles.

    uv run python demo.py

No requiere clave de ningún proveedor.

Además de los 6 casos, demuestra dos cosas que el PRD exige explícitamente:
idempotencia ejecutando `sol-001` dos veces, y una confirmación explícita para
`sol-004`.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.io import limpiar_salida
from app.tools.oc import (
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

RAIZ = Path(__file__).resolve().parent
CASOS = ["sol-001", "sol-002", "sol-003", "sol-004", "sol-005", "sol-006"]
ANCHO = 78


def _datos(respuesta: str) -> dict[str, Any]:
    """Extrae `data` de la respuesta de una herramienta, o {} si falló."""
    cuerpo = json.loads(respuesta)
    if not cuerpo.get("ok"):
        return {}
    datos = cuerpo.get("data")
    return datos if isinstance(datos, dict) else {}


def _error(respuesta: str) -> str:
    cuerpo = json.loads(respuesta)
    return "" if cuerpo.get("ok") else str(cuerpo.get("error", ""))


def _titulo(texto: str) -> None:
    print(f"\n{'═' * ANCHO}\n  {texto}\n{'═' * ANCHO}")


def _codigos(veredictos: list[dict[str, Any]]) -> str:
    return ", ".join(str(v["codigo"]) for v in veredictos) or "—"


def _pesos(monto: object) -> str:
    return f"{monto:,}".replace(",", ".") if isinstance(monto, int) else str(monto)


async def procesar(caso: str, ctx: Contexto, *, confirmado: bool = False) -> dict[str, Any]:
    """Corre el flujo completo de un caso y devuelve su resumen."""
    print(f"\n┌─ {caso} {'─' * (ANCHO - len(caso) - 4)}")

    # 1 · Leer el paquete (HU-1)
    respuesta = await oc_leer_paquete(ArgsCaso(caso=caso), ctx)
    paquete = _datos(respuesta)
    if not paquete:
        print(f"│  ✗ no se pudo leer: {_error(respuesta)}")
        return {"caso": caso, "resultado": "error"}

    solicitud = paquete["solicitud"]
    print(f"│  solicitud   {solicitud['solicitud_id']} · {solicitud['solicitante']}")
    print(f"│  proveedor   {solicitud['proveedor_nombre']}")
    print(
        f"│  monto       COP {_pesos(solicitud['valor_total'])}"
        f"  ·  {solicitud['centro_costo']} / {solicitud['subarea']}"
    )
    if paquete.get("faltantes"):
        print(f"│  faltantes   {', '.join(paquete['faltantes'])}")

    # 2 · Validar los diez controles (HU-2)
    respuesta = await oc_validar(ArgsValidar(caso=caso), ctx)
    validacion = _datos(respuesta)
    bloqueos = validacion["bloqueos"]
    confirmaciones = validacion["confirmaciones"]
    derivados = validacion["derivados"]

    print(f"│  apta        {validacion['apta']}")
    print(f"│  bloqueos    {_codigos(bloqueos)}")
    print(f"│  confirmac.  {_codigos(confirmaciones)}")
    print(f"│  derivados   {derivados or '—'}")
    print(f"│  retroactiva {validacion['retroactiva']}")

    for veredicto in bloqueos + confirmaciones:
        print(f"│    {veredicto['codigo']} · {veredicto['detalle']}")

    # Un bloqueo cierra el caso. No se llama a `oc_crear`: un bloqueo no es
    # confirmable, así que el agente no tiene nada que intentar. Que
    # `confirmado=True` tampoco lo levante está cubierto en tests/test_tools.py
    # con un payload bien formado; fabricar uno aquí solo ensuciaría control.csv
    # con filas `error` que no reflejan lo que pasó.
    if bloqueos:
        print("│  ✗ BLOQUEADA · no se crea OC (un bloqueo no es confirmable)")
        print(f"└{'─' * (ANCHO - 1)}")
        return {"caso": caso, "resultado": "bloqueada", "bloqueos": _codigos(bloqueos)}

    # 3 · Evidencia de aprobación — antes del payload, que consume su sha256 (HU-4)
    respuesta = await oc_generar_evidencia(ArgsCaso(caso=caso), ctx)
    evidencia = _datos(respuesta)
    print(f"│  evidencia   {evidencia['ruta']}")
    print(f"│  sha256      {str(evidencia['sha256'])[:32]}…")

    # 4 · Construir el payload tal como quedaría en SAP (HU-3)
    respuesta = await oc_construir_payload(
        ArgsConstruirPayload(caso=caso, derivados=derivados or None), ctx
    )
    construccion = _datos(respuesta)
    if not construccion:
        print(f"│  ✗ payload: {_error(respuesta)}")
        return {"caso": caso, "resultado": "error"}

    payload = construccion["payload"] if "payload" in construccion else construccion
    posicion = payload["posiciones"][0]
    print(
        f"│  payload     {payload['proveedor']['codigo_sap']}"
        f" · {payload['condiciones_pago']} · pos {posicion['numero']}"
        f" · {posicion['unidad']} · IVA {posicion['indicador_iva']}"
    )
    print(f"│  descripción \"{posicion['descripcion']}\" ({len(posicion['descripcion'])} ≤ 40)")

    # 5 · Crear la OC (HU-5)
    respuesta = await oc_crear(ArgsCrear(caso=caso, payload=payload, confirmado=confirmado), ctx)
    creacion = _datos(respuesta)

    if not creacion:
        print(f"│  ⏸ PENDIENTE DE CONFIRMACIÓN · {_codigos(confirmaciones)}")
        print(f"└─ {_error(respuesta)[:ANCHO - 4]}")
        return {
            "caso": caso,
            "resultado": "pendiente_confirmacion",
            "confirmaciones": _codigos(confirmaciones),
        }

    marca = " (idempotente)" if creacion.get("idempotente") else ""
    print(f"│  ✓ OC CREADA {creacion['numero_oc']}{marca} · {creacion['fecha']}")
    print(f"└{'─' * (ANCHO - 1)}")
    return {
        "caso": caso,
        "resultado": "idempotente" if creacion.get("idempotente") else "creada",
        "numero_oc": creacion["numero_oc"],
    }


async def main() -> None:
    # `out/` se limpia al inicio para que la demo sea determinista (C9) y la
    # primera OC sea siempre 4500000001 (invariante I2).
    limpiar_salida(RAIZ)
    ctx = Contexto(raiz=RAIZ, session_id="demo")

    _titulo("DEMO · Órdenes de Compra SAP — sin modelo de lenguaje")
    print("  Llama directamente a las herramientas. No consume tokens ni requiere clave.")

    _titulo("PASADA 1 · los 6 casos, sin confirmar nada")
    resumen = [await procesar(caso, ctx) for caso in CASOS]

    _titulo("PASADA 2 · confirmación explícita de sol-004 (PRD §6.6)")
    print("  RC5: la cotización se desvía 6 % de la solicitud. El humano confirma.")
    confirmado = await procesar("sol-004", ctx, confirmado=True)

    _titulo("PASADA 3 · idempotencia de sol-001 (PRD §6.6 · invariante I3)")
    print("  Crear dos veces el mismo solicitud_id debe devolver la MISMA OC.")
    repetido = await procesar("sol-001", ctx)

    _titulo("RESUMEN")
    print(f"  {'caso':<10} {'resultado':<24} {'detalle'}")
    print(f"  {'─' * 10} {'─' * 24} {'─' * 28}")
    for fila in resumen:
        detalle = fila.get("numero_oc") or fila.get("bloqueos") or fila.get("confirmaciones") or ""
        print(f"  {fila['caso']:<10} {fila['resultado']:<24} {detalle}")
    for fila, nota in ((confirmado, "← confirmado"), (repetido, "← reintento")):
        oc = fila.get("numero_oc", "")
        print(f"  {fila['caso']:<10} {fila['resultado']:<24} {oc}  {nota}")

    ordenes = RAIZ / "out" / "sap" / "ordenes.jsonl"
    lineas = len(ordenes.read_text(encoding="utf-8").strip().splitlines()) if ordenes.exists() else 0
    control = RAIZ / "out" / "control.csv"
    filas = len(control.read_text(encoding="utf-8").strip().splitlines()) - 1 if control.exists() else 0

    _titulo("ARTEFACTOS")
    print(f"  out/sap/ordenes.jsonl   {lineas} órdenes (una por OC real, no por intento)")
    print(f"  out/control.csv         {filas} filas (una por INTENTO · invariante I6)")
    print("  out/<caso>/payload.json · trazabilidad.json · aprobacion.txt")
    print("  out/log.jsonl           bitácora de toda llamada a herramienta (CA4)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
