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
| 0 | Cimientos | ✅ **APROBADA** · `specs/qa/informe-f0.md` · 105 tests · cerrando D1–D5, D10 | `mypy --strict` limpio + tests de normalizadores |
| 1 | Controles RC1–RC10 | 🔵 especificada (T1.1–T1.6), sin implementar | 10 controles con test unitario propio |
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

## Hallazgos arrastrados desde fases cerradas

Reportados por los ejecutores en su sección HALLAZGOS. **No son defectos**: son cosas
que vieron y correctamente no construyeron por estar fuera de su alcance. El
especificador debe incorporarlos a la tarea destino.

| # | Hallazgo | Destino |
|---|---|---|
| H1 | **El parseo de `cotizacion.txt` no existe en `app/`.** Hoy vive en un helper de test. Formato uniforme en los 6 fixtures: `1. <nombre ítem> \| Cantidad: N \| Precio unitario (IVA incl.): COP X \| Subtotal: COP Y`. De ahí salen `item` (para `inferir_unidad`), `total` y `referencia`. | **T2.3** `oc_leer_paquete` |
| H2 | **Invariante I5 (`posiciones[].numero` = 10, 20, 30…) no lo valida el tipo.** `PosicionOC.numero` es un `int` libre; la numeración la debe imponer el constructor del payload. | **T2.6** `oc_construir_payload` |
| H3 | **La autorización de escritura vive solo en `ruta_salida`.** `escribir_json` y `escribir_texto` aceptan cualquier `Path`, así que una herramienta que construya una ruta a mano se salta el control de C1. | Gate de QA de **Fase 2**: verificar que todo destino de escritura venga de `ruta_salida` |
| H4 | **`cargar_config` no lee `.env`.** La spec pidió `os.environ.get` y no hay `python-dotenv`. Existe `.env.example`, así que alguien debe cargar el `.env` al arrancar (`uvicorn --env-file`, o leerlo en el arranque). | **T4.6** `app/api.py` y **T5.7** README |
| H5 | **Ningún payload se construye sin `truncar_descripcion`.** Las 6 descripciones exceden 40 caracteres (sol-001 mide 73) y `PosicionOC` impone `max_length=40`. | **T2.6**: usar `truncar_descripcion` y guardar la completa en `trazabilidad.json` |
| H6 | **`indicadores-iva.json` trae `tasa: 0` como entero** para `C0`; Pydantic lo coacciona a `0.0`. Si un control compara la tasa, el tipo es `float`. | **T1.4** (RC6) |
| H7 | **`Cotizacion`, `ReferenciaOC` tienen campos `X \| None` sin default**, lo que en Pydantic v2 los hace **obligatorios** aunque admitan `None`. Hay que pasarlos explícitamente al construirlos. Es deliberado: fuerza a ser explícito. `ExcepcionOC.confirmado_por` sí recibió default `None`. | **Fase 2**, al construir estos modelos |
| H8 | **`normalizar_nombre` elimina `sa`/`sas`/`ltda` en cualquier posición**, no solo al final. El QA lo cuantificó: los 5 proveedores dan 5 valores únicos, **idénticos** a los de una variante que solo quite el sufijo final, así que el impacto hoy es cero. Muerde con nombres como `"Grupo SA Internacional"` → `"grupo internacional"`. | Riesgo documentado · **Supuestos de `SOLUCION.md`** |

### Defectos menores diferidos a la Fase 2 (QA Fase 0, D6–D9)

Aprobados como deuda consciente: ninguno es explotable hoy, y los cuatro se cierran
mejor cuando exista el consumidor real (las herramientas).

| # | Defecto | Cierre en |
|---|---|---|
| D6 | `test_escribir_en_fixtures_falla` **intenta una escritura real** en `fixtures/` y solo lo frena el `chmod a-w`. Si alguien hace `chmod u+w` o corre como root, el test viola C1 mientras pretende defenderlo. Verificar `stat().st_mode` afirma lo mismo sin tocar el disco. | Gate de **Fase 2** |
| D7 | `escribir_json`, `escribir_texto` y `agregar_linea_jsonl` aceptan cualquier `Path`: la autorización de C1 es disciplina en el sitio de llamada, no un control. El QA demostró escritura en la raíz del proyecto saltándose `ruta_salida`. Hoy inexplotado: el único consumidor es `log.py` y sí pasa por el portero. | **T2.3–T2.7** + gate de Fase 2: todo destino de escritura debe venir de `ruta_salida` |
| D8 | `normalizar_nit` y `normalizar_nombre` lanzan con tipos no-`str`, y peor: `normalizar_nit(["900555111"])` devuelve `None` **en silencio**, lo que haría a RC1 creer que no hay NIT y caer al cruce por nombre. Mitigado porque Pydantic valida antes y `mypy --strict` protege a todo llamador tipado. | Nota de robustez en **T1.1** |
| D9 | `leer_json` y `leer_texto` lanzan `IsADirectoryError` y `PermissionError`, subclases de `OSError` que su contrato no menciona (solo promete `ValueError` y `None`). Una herramienta que capture solo `ValueError` deja escapar la excepción → **violación de C3**. | **T2.3–T2.7**: las herramientas capturan `Exception`, no `ValueError` |

**Nota para el especificador de la Fase 2:** D7 y D9 juntos son el riesgo real. Las seis
herramientas deben (a) obtener todo destino de escritura de `ruta_salida`, y (b) envolver
en `except Exception`, no en `except ValueError`.

## Riesgos del plan

| Riesgo | Mitigación |
|---|---|
| El deploy falla y cuesta **−10** | F5 arranca por T5.5: desplegar un "hola mundo" temprano y luego iterar |
| `SOLUCION.md` queda al final sin tiempo | Las ADR se escriben durante el desarrollo; `SOLUCION.md` los compila, no los inventa |
| El candidato no puede defender una pieza | El QA incluye pasada de auditoría con el candidato; sin eso la fase no cierra |
| Streamlit y su modelo de rerun complican CA3 | T5.3 es tarea aparte con criterio explícito; el estado del turno vive en el backend, no en Streamlit |
