# Oráculo de aceptación — los 6 casos

> **Este es el artefacto más importante del proyecto.** Es la verdad contra la que el
> subagente QA valida. Fue derivado a mano desde los fixtures y los maestros, y
> verificado contra los objetivos O1–O4 del PRD.
>
> Si el código contradice este documento, **el código está mal** — salvo que se
> demuestre que el oráculo malinterpretó un fixture, lo que exige una ADR.

---

## Orden de evaluación de controles

Los bloqueos se evalúan **todos** (no hay corto-circuito): el usuario debe ver todo
lo que está mal en una sola pasada, no uno por uno. Única excepción: **RC3 solo se
evalúa si RC2 pasó** (ver `ADR-003`).

| Orden | Regla | Tipo | Depende de |
|---|---|---|---|
| 1 | RC1 · proveedor existe y está activo | **Bloqueo** | — |
| 2 | RC4 · subárea pertenece al centro de costo | **Bloqueo** | — |
| 3 | RC2 · aprobación existe, dice "Aprobado", de un aprobador del centro | **Bloqueo** | — |
| 4 | RC3 · `valor_total` ≤ tope del aprobador | **Bloqueo** | RC2 ✓ |
| 5 | RC10 · `cantidad × valor_unitario == valor_total` (± 1) | **Bloqueo** | — |
| 6 | RC5 · desviación cotización vs solicitud ≤ 2% | Confirmación | — |
| 7 | RC6 · `indicador_iva` ausente → derivar del proveedor | Confirmación + derivado | RC1 ✓ |
| 8 | RC7 · `condiciones_pago` ausente → derivar del proveedor | Derivado (solo informa) | RC1 ✓ |
| 9 | RC8 · factura con fecha < `fecha_solicitud` → retroactiva | Confirmación | — |
| 10 | RC9 · fecha de aprobación ≥ `fecha_solicitud` | Confirmación | RC2 ✓ |

**Semántica obligatoria (HU-2):** `bloqueos` impiden crear la OC. `confirmaciones` la
permiten solo con `confirmado=True`. `derivados` son valores completados desde maestros
y **deben informarse** al usuario.

---

## Matriz de resultados esperados

| Caso | `apta` | `bloqueos` | `confirmaciones` | `derivados` | `retroactiva` | ¿Crea OC? |
|---|---|---|---|---|---|---|
| **sol-001** | `True` | `[]` | `[]` | `{}` | `False` | **Sí, sin intervención** |
| **sol-002** | `False` | `[RC1]` | — | — | `False` | No |
| **sol-003** | `False` | `[RC2]` | — | — | `False` | No |
| **sol-004** | `True` | `[]` | `[RC5]` | `{}` | `False` | Solo con `confirmado=True` |
| **sol-005** | `True` | `[]` | `[RC8]` | `{}` | `True` | Solo con `confirmado=True` |
| **sol-006** | `True` | `[]` | `[RC6]` | `{indicador_iva, condiciones_pago}` | `False` | Solo con `confirmado=True` |

Mapeo a los objetivos del PRD: sol-001 → **O1** · sol-002, sol-003 → **O2** ·
sol-004, sol-006 → **O3** · sol-005 → **O4**.

---

## Detalle por caso

### sol-001 · `SOL-2026-001` — camino feliz (O1)

Todo pasa. Es el único caso que crea OC sin ninguna intervención humana.

| Control | Dato | Veredicto |
|---|---|---|
| RC1 | NIT `900555111` → `TecnoSuministros S.A.S.`, `codigo_sap=100234`, `activo=true` | ✓ |
| RC2 | `mlopez@` aprobador de CC-1010; cuerpo *"Aprobado, proceder con la orden de compra."* | ✓ |
| RC3 | `11.400.000` ≤ tope `mlopez` = `50.000.000` | ✓ |
| RC4 | `Infraestructura` ∈ CC-1010 `[Infraestructura, Soporte, Desarrollo]` | ✓ |
| RC5 | cotización `11.400.000` vs solicitud `11.400.000` → **0.00%** | ✓ |
| RC6 / RC7 | `indicador_iva="C1"` y `condiciones_pago="Z030"` informados | no aplica |
| RC8 | sin `factura.txt` | no aplica |
| RC9 | aprobación `2026-08-21` ≥ solicitud `2026-08-20` | ✓ |
| RC10 | `120 × 95.000 = 11.400.000` | ✓ |

Payload esperado: `codigo_sap="100234"`, `condiciones_pago="Z030"`,
`posiciones[0].indicador_iva="C1"`, `unidad="UN"`, 1 posición con `numero=10`,
`excepciones=[]`. OC `4500000001` en la primera corrida limpia.

---

### sol-002 · `SOL-2026-002` — proveedor inexistente (O2)

| Control | Dato | Veredicto |
|---|---|---|
| **RC1** | NIT `901999000` (`Soluciones Digitales del Norte S.A.S.`) **no existe** en `proveedores.json` | **BLOQUEO** |
| RC2 | `mlopez@` aprobador de CC-1010, cuerpo *"Aprobado."* | ✓ |
| RC3 | `8.500.000` ≤ `50.000.000` | ✓ |
| RC4 | `Desarrollo` ∈ CC-1010 | ✓ |
| RC5 | `8.500.000` vs `8.500.000` → 0.00% | ✓ |
| RC10 | `5 × 1.700.000 = 8.500.000` | ✓ |

Único bloqueo: RC1. **No se llama `oc_crear`.** Acción sugerida al usuario: solicitar
la creación del proveedor en SAP antes de reintentar.

---

### sol-003 · `SOL-2026-003` — aprobador sin autoridad (O2)

El caso más interesante del set. Ver `ADR-003`.

| Control | Dato | Veredicto |
|---|---|---|
| RC1 | NIT `830111222` → `Mobiliario Andino S.A.`, `activo=true` | ✓ |
| **RC2** | aprueba `fvargas@` (aprobador de **CC-3030** Comercial), pero el centro de la solicitud es **CC-2020** Administración | **BLOQUEO** |
| RC3 | `no_evaluable` — sin aprobador válido para el centro no existe un tope aplicable | — |
| RC4 | `Servicios Generales` ∈ CC-2020 `[Compras, Servicios Generales]` | ✓ |
| RC5 | `74.000.000` vs `74.000.000` → 0.00% | ✓ |
| RC10 | `40 × 1.850.000 = 74.000.000` | ✓ |

**Nota para la defensa técnica:** el cuerpo del correo delata el error de proceso —
*"Aprobado desde comercial, es para el equipo de preventa que se muda a la nueva sede"*.
Un líder de Comercial aprueba un gasto imputado a Administración. Y si el centro
hubiera sido el correcto (CC-3030), `74M ≤ 80M` habría pasado el tope: **el problema
es de autoridad, no de monto**. El único aprobador real de CC-2020 es `rtorres@` con
tope `30M`, que no alcanza.

---

### sol-004 · `SOL-2026-004` — cotización ≠ solicitud (O3)

Este es el caso del prompt de demostración del PRD (§11).

| Control | Dato | Veredicto |
|---|---|---|
| RC1 | NIT `901222333` → `Cloud Andina S.A.S.`, `activo=true` | ✓ |
| RC2 | `dgarcia@` aprobador de CC-1010; *"Aprobado por 25 millones según la solicitud."* | ✓ |
| RC3 | `25.000.000` ≤ tope `dgarcia` = `200.000.000` | ✓ |
| RC4 | `Desarrollo` ∈ CC-1010 | ✓ |
| **RC5** | cotización `26.500.000` vs solicitud `25.000.000` → **6.00%** > 2% | **CONFIRMACIÓN** |
| RC9 | aprobación `2026-08-26T18:45` ≥ solicitud `2026-08-26` | ✓ |
| RC10 | `100 × 250.000 = 25.000.000` | ✓ |

`unidad="H"` (ítem de cotización: *"Hora de arquitecto de nube senior"*).

El PRD §11 exige que la UI muestre **los dos valores**: `25.000.000` (solicitud) y
`26.500.000` (cotización), más la desviación `6.00%`. La confirmación debe decir qué
valor se usará: **el de la solicitud**, porque es el monto que el líder aprobó.

`excepciones=[{codigo:"RC5", detalle:"...", confirmado_por:"<session_id>"}]`

---

### sol-005 · `SOL-2026-005` — OC retroactiva (O4)

| Control | Dato | Veredicto |
|---|---|---|
| RC1 | NIT `800444555` → `Papelería Central Ltda.`, `activo=true` | ✓ |
| RC2 | `rtorres@` aprobador de CC-2020; *"Aprobado. Ya llegó la factura..."* | ✓ |
| RC3 | `3.200.000` ≤ tope `rtorres` = `30.000.000` | ✓ |
| RC4 | `Compras` ∈ CC-2020 | ✓ |
| RC5 | `3.200.000` vs `3.200.000` → 0.00% | ✓ |
| **RC8** | factura `FC-88231` del **`2026-08-10`** < solicitud del **`2026-08-27`** | **CONFIRMACIÓN** + `retroactiva=True` |
| RC9 | aprobación `2026-08-28` ≥ solicitud `2026-08-27` | ✓ |
| RC10 | `1 × 3.200.000 = 3.200.000` | ✓ |

17 días de desvío. **`out/control.csv` debe registrar `retroactiva=true`** — es la
métrica que el PRD dice que la dirección quiere medir (§1, §2.2.3). La cotización es
del `2026-08-05`, anterior a la factura: se cotizó, se facturó, y la solicitud formal
llegó 22 días después. El proceso corrió al revés.

---

### sol-006 · `SOL-2026-006` — datos incompletos (O3)

| Control | Dato | Veredicto |
|---|---|---|
| **RC1** | **`proveedor_nit` ausente** → resolver por nombre normalizado `"TecnoSuministros S.A.S."` → `codigo_sap=100234`, NIT `900555111` | ✓ (por nombre) |
| RC2 | `mlopez@` aprobador de CC-1010, *"Aprobado."* | ✓ |
| RC3 | `5.400.000` ≤ `50.000.000` | ✓ |
| RC4 | `Soporte` ∈ CC-1010 | ✓ |
| RC5 | `5.400.000` vs `5.400.000` → 0.00% | ✓ |
| **RC6** | **`indicador_iva` ausente** → derivado `"C1"` del proveedor | **CONFIRMACIÓN** + derivado |
| **RC7** | **`condiciones_pago` ausente** → derivado `"Z030"` del proveedor | Derivado (solo informa) |
| RC9 | aprobación `2026-08-29T12:00` ≥ solicitud `2026-08-29` | ✓ |
| RC10 | `30 × 180.000 = 5.400.000` | ✓ |

`derivados = {"indicador_iva": "C1", "condiciones_pago": "Z030"}`.
**Diferencia clave que el QA debe verificar:** RC6 genera confirmación, RC7 **no**.
Ambos son derivados, pero solo el IVA exige confirmar (impacto fiscal).

---

## Invariantes transversales (aplican a todos los casos)

| # | Invariante | Verificación |
|---|---|---|
| I1 | `descripcion` del payload ≤ **40 caracteres**, cortada en frontera de palabra, y es prefijo de la original. La completa va en `trazabilidad.json`. | Aserción por regla, no por string literal (ver `ADR-004`) |
| I2 | Numeración de OC secuencial desde `4500000001` | Primera OC de una corrida limpia |
| I3 | Idempotencia: segundo `oc_crear` con el mismo `solicitud_id` → mismo número, `idempotente=True`, **sin** segunda línea en `ordenes.jsonl` | `demo.py` ejecuta sol-001 dos veces (§6.6) |
| I4 | `sociedad="1000"`, `organizacion_compras="1000"` constantes | Todos los payloads |
| I5 | `posiciones[].numero` en pasos de 10: `10, 20, 30...` | Todos los payloads |
| I6 | `out/control.csv` tiene **una fila por intento**: exitoso, bloqueado o pendiente | 6 casos + reintentos |
| I7 | `trazabilidad.json` asigna a cada campo del payload una fuente: `solicitud`, `cotizacion`, `maestro.<nombre>` o `derivado` | Todos los casos aptos |
| I8 | Toda herramienta devuelve string JSON con `ok`; ninguna lanza excepción | Test de fuzzing con entradas corruptas |
| I9 | `moneda` ∈ `{COP, USD}`; los 6 casos son `COP` | Validación Pydantic |
| I10 | `demo.py` es determinista entre corridas, salvo timestamps | Dos corridas + diff ignorando `ts` |

---

## Casos de borde no cubiertos por los fixtures

Hay que implementarlos aunque ningún caso los ejercite, porque el PRD los exige:

| Situación | Regla | Comportamiento |
|---|---|---|
| Sin cotización | RC5 | Confirmación (*"no hay cotización para contrastar"*) |
| Aprobación sin la palabra "Aprobado" | RC2 | Bloqueo |
| `aprobacion.json` ausente | RC2 | Bloqueo |
| Fecha de aprobación < `fecha_solicitud` | RC9 | Confirmación |
| `valor_total` no numérico / JSON malformado | HU-6 | `{ok: False, error}` legible + sugerencia de qué pedir al solicitante |
| Proveedor existe pero `activo=false` (`901777888`, Consultores Ágiles) | RC1 | Bloqueo |
