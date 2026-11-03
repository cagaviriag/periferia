# Plan de ejecución — 6 fases

> Mantenido por el **planificador**. El **orquestador** lee este archivo para decidir
> qué sigue. Una fase no arranca hasta que el **QA** aprueba la anterior.

**Principio de ordenamiento:** las herramientas y `demo.py` van **antes** del LLM.
`demo.py` no consume modelo (§6.6), así que tendremos un entregable verificable contra
el oráculo sin haber gastado un token. Eso desriesga lo que más puntos vale.

---

## Estado

| Fase | Nombre | Estado | Gate de QA |
|---|---|---|---|
| 0 | Cimientos | ⬜ pendiente | `mypy --strict` limpio + tests de normalizadores |
| 1 | Controles RC1–RC10 | ⬜ pendiente | 10 controles con test unitario propio |
| 2 | Herramientas + SAP mock | ⬜ pendiente | 6 herramientas, contrato `{ok,...}`, nunca lanzan |
| 3 | `demo.py` | ⬜ pendiente | **Los 6 casos calzan con `01-oracle.md`** + I1–I10 |
| 4 | Agente + API | ⬜ pendiente | CA1–CA5 verificables; prompt de §11 funciona |
| 5 | Front + deploy + docs | ⬜ pendiente | 5 comportamientos de §6.1 + URL viva |

---

## Fase 0 · Cimientos

Sin dependencias. Todo lo demás se apoya aquí.

| Tarea | Entrega | Criterio |
|---|---|---|
| T0.1 | `pyproject.toml`, `.env.example`, `.gitignore` | `uv sync` instala; `.env` fuera del repo |
| T0.2 | `app/core/config.py` | Lee `OPENAI_API_KEY`, `MAX_ITERACIONES=25`, `MAX_TOKENS_SESION`. Falla con mensaje claro si falta la clave |
| T0.3 | `app/core/io.py` | Lectura de fixtures y escritura en `out/`. **Rechaza cualquier ruta fuera de `out/`** |
| T0.4 | `app/core/normalizar.py` | `normalizar_nit` (ADR-008), `normalizar_nombre`, `truncar_descripcion` (ADR-004), `inferir_unidad` (ADR-005) |
| T0.5 | `app/core/log.py` | `out/log.jsonl` (CA4) y `out/control.csv` (HU-5) |
| T0.6 | `app/core/modelos.py` | Modelos Pydantic: `Solicitud`, `Cotizacion`, `Aprobacion`, `Factura`, `Paquete`, `OrdenCompra` (§7.4), `Resultado` |

**Gate:** `mypy --strict app/` sin errores. Tests de los 4 normalizadores, incluido
`900.555.111-2` → `900555111`.

---

## Fase 1 · Controles RC1–RC10

Depende de F0. **Es el núcleo del reto.** Cada control es una función pura.

```python
def rc1_proveedor_existe(paquete: Paquete, maestros: Maestros) -> Veredicto: ...
```

`Veredicto` = `ok` | `bloqueo` | `confirmacion` | `derivado` | `no_evaluable`,
con `codigo`, `detalle` legible y `datos` para la UI.

| Tarea | Controles | Notas |
|---|---|---|
| T1.1 | RC1, RC4 | RC1 cruza por NIT normalizado; si falta NIT, por nombre (sol-006) |
| T1.2 | RC2, RC3 | **RC3 solo si RC2 pasó** (ADR-003) |
| T1.3 | RC10, RC5 | RC10 tolerancia ± 1 unidad monetaria; RC5 umbral 2%; sin cotización → confirmación |
| T1.4 | RC6, RC7 | RC6 confirma, RC7 solo informa. La diferencia es gradeable |
| T1.5 | RC8, RC9 | RC8 fija `retroactiva=True` |
| T1.6 | `evaluar_controles()` | Orquesta los 10 en el orden del oráculo. Devuelve `{apta, bloqueos, confirmaciones, derivados, retroactiva}` |

**Gate:** un test por control, con su caso positivo y su caso negativo. Los casos de
borde del oráculo (proveedor inactivo `901777888`, sin cotización, sin aprobación)
cubiertos aunque ningún fixture los ejercite.

---

## Fase 2 · Herramientas + adaptador SAP

Depende de F1.

| Tarea | Entrega | Prioridad PRD |
|---|---|---|
| T2.1 | `app/sap/adapter.py` — interfaz `SapAdapter` (§7.4, dada textual) | P0 |
| T2.2 | `app/sap/mock.py` — `out/sap/ordenes.jsonl`, secuencia desde `4500000001`, idempotencia por `solicitud_id` | P0 |
| T2.3 | `oc_leer_paquete` | P0 |
| T2.4 | `oc_validar` | P0 |
| T2.5 | `oc_generar_evidencia` — `.txt` + `sha256` | P0 (pdf es P1) |
| T2.6 | `oc_construir_payload` + `trazabilidad.json` | P0 |
| T2.7 | `oc_crear` | P0 |

**Orden obligatorio:** T2.5 antes de T2.6 — el payload consume
`aprobador.evidencia_sha256`, que produce la evidencia.

**Gate:** las 6 herramientas devuelven string JSON con `ok`. Test de fuzzing con JSON
malformado, montos no numéricos y carpetas inexistentes: **ninguna lanza excepción** (C3).

---

## Fase 3 · `demo.py` — el gate grande

Depende de F2. Aquí se demuestra el valor del reto sin gastar un token.

Requisitos textuales de §6.6: procesa los 6 casos; imprime por caso `apta`, bloqueos,
confirmaciones, `retroactiva`, número de OC o motivo; **ejecuta `sol-001` dos veces**
para probar idempotencia; **muestra una confirmación explícita para `sol-004`**.

| Tarea | Entrega |
|---|---|
| T3.1 | `demo.py` con salida legible en tabla |
| T3.2 | `tests/test_oraculo.py` — parametrizado sobre los 6 casos contra `01-oracle.md` |
| T3.3 | `tests/test_invariantes.py` — I1 a I10 |
| T3.4 | Verificación de determinismo: dos corridas, diff ignorando `ts` |

**Gate — el más importante del proyecto:** la matriz de `01-oracle.md` se cumple en los
6 casos, celda por celda. Si una celda falla, el QA devuelve la fase y no se avanza.

---

## Fase 4 · Ciclo del agente + API

Depende de F3. Primera fase que consume LLM.

| Tarea | Entrega | Regla |
|---|---|---|
| T4.1 | `app/llm/adapter.py` — `enviar(mensajes, herramientas) → Respuesta` | §6.1 |
| T4.2 | `app/llm/openai_adapter.py` — única implementación que conoce OpenAI | §6.1 |
| T4.3 | `app/core/agente.py` — el bucle (~80 líneas) | CA1, CA4, CA5 |
| T4.4 | `agent/prompt.md` — system prompt en Markdown aparte | C4, CA2 |
| T4.5 | `app/knowledge/ordenes-compra.md` — el proceso de negocio | C4 |
| T4.6 | `app/api.py` — `POST /api/chat`, `GET /api/sessions/{id}`, `GET /api/health` | §6.4 |
| T4.7 | Topes de iteraciones y tokens por sesión | CA1, §8 Costo |

**Gate:** el prompt de §11 (`"Procesa la solicitud sol-004... no la crees hasta que yo
lo confirme"`) produce el resultado esperado. `/api/health` **no** filtra la clave.
Un error del proveedor LLM no mata la sesión (CA5).

---

## Fase 5 · Front, deploy y documentación

Depende de F4.

| Tarea | Entrega | Requisito |
|---|---|---|
| T5.1 | `web/app.py` — chat Streamlit: historial, entrada, "pensando" | §6.1 |
| T5.2 | Panel de tool calls: nombre, argumentos, resultado resumido | §6.1 |
| T5.3 | Bloque de confirmación resaltado; **el botón envía mensaje de usuario, no llama a la herramienta** | C5, CA3 |
| T5.4 | Panel "la OC como quedaría en SAP" en tabla | HU-3, §11 |
| T5.5 | Deploy con URL pública viva | §9.3 (si no: **−10**) |
| T5.6 | `SOLUCION.md` — las 12 secciones de §9.1 | §9.1 |
| T5.7 | `README.md` — un comando, variables, cómo correr `demo.py` | §9.2 |
| T5.8 | `WALKTHROUGH.md` — mapa archivo→líneas→función, para la defensa | extra (ADR-001) |
| T5.9 | `modulo/` — bonus `agent.md` + tools + `skill/SKILL.md` | §9.4, **+10** |

**Secciones de `SOLUCION.md` que no son código y valen puntos:**
- §9.1.5 **Matriz de controles** RC1–RC10 y cuál fue la más difícil → ADR-003
- §9.1.6 **Diseño del adaptador SAP real** → OData `API_PURCHASEORDER_PROCESS_SRV` vs
  `BAPI_PO_CREATE1`, autenticación, idempotencia ante reintentos, plan B
- §9.1.7 **Lectura del proceso**: media página a la dirección sobre OC retroactivas,
  con el dato de `sol-005` (17 días de desvío, cotización anterior a la factura)
- ADR-001 y ADR-002 alimentan §9.1.8 **Decisiones y trade-offs** (mínimo 3)

---

## Riesgos del plan

| Riesgo | Mitigación |
|---|---|
| El deploy falla y cuesta **−10** | F5 arranca por T5.5: desplegar un "hola mundo" temprano y luego iterar |
| `SOLUCION.md` queda al final sin tiempo | Las ADR se escriben durante el desarrollo; `SOLUCION.md` los compila, no los inventa |
| El candidato no puede defender una pieza | El QA incluye pasada de auditoría con el candidato; sin eso la fase no cierra |
| Streamlit y su modelo de rerun complican CA3 | T5.3 es tarea aparte con criterio explícito; el estado del turno vive en el backend, no en Streamlit |
