# SOLUCIÓN — Reto 03 · Agente conversacional de Órdenes de Compra SAP

> Prueba técnica · Periferia IT Group · Equipo Perxia 2.0
> Estructura según PRD §9.1

---

## 1. El problema en una frase

Cada compra llega a administración como tres archivos sueltos en un correo, y una
analista los transcribe a mano en SAP verificando de memoria si quien aprobó podía
aprobar ese monto en ese centro de costo — una vez por cada factura que entra a la
compañía.

**A quién le duele:** a la analista administrativa, que repite digitación de alto riesgo
(un centro de costo mal digitado se corrige en cierre contable); a contabilidad, que
descubre los errores un mes después; y a la dirección, que hoy **no puede responder qué
porcentaje de sus órdenes de compra son retroactivas**.

---

## 2. Arquitectura

```
┌───────────────────┐   HTTP    ┌──────────────────────────────────────────┐
│  Front · Streamlit│ ────────▶ │  Backend · FastAPI                       │
│  - historial      │ ◀──────── │  ┌────────────────────────────────────┐  │
│  - tool calls     │           │  │ ciclo del agente (~80 líneas)      │  │
│  - confirmaciones │           │  │ prompt → modelo → herramientas → … │  │
│  - OC como en SAP │           │  └────────────────────────────────────┘  │
└───────────────────┘           │  adaptador LLM (interfaz propia)         │
                                │  herramientas tipadas (Pydantic)        │
                                └──────┬────────────────────────┬──────────┘
                                       │                        │
                              fixtures/ (solo lectura)      out/ (escritura)
```

### Dónde vive cada cosa

| Capa | Ubicación | Qué contiene |
|---|---|---|
| **Comportamiento** | `agent/prompt.md` | El system prompt, en Markdown aparte. Nunca embebido en código. |
| **Conocimiento** | `app/knowledge/ordenes-compra.md` | El proceso de negocio que el agente consulta. |
| **Ejecución** | `app/tools/oc.py` | Las 5 herramientas. Única fuente de valores (CA2). |
| **Reglas de negocio** | `app/controles/` | RC1–RC10, una función pura por control. |
| **Dominio** | `app/core/modelos.py` | Modelos Pydantic, incluido el payload de §7.4. |
| **Datos maestros** | `fixtures/` | Inmutable (`chmod a-w`). |

Esta separación es verificable: **cambiar un tope de aprobación no toca una línea de
Python**, porque los topes viven en `fixtures/reto-03/maestros/centros-costo.json`.

### Capas, de adentro hacia afuera

```
app/core/        cimientos sin dependencias de dominio
   ↓             config · io · log · normalizar · modelos
app/controles/   RC1–RC10 como funciones puras + evaluar_controles
   ↓
app/sap/         SapAdapter (Protocol) + SapMock
   ↓
app/tools/       las 5 herramientas: el contrato con el modelo
   ↓
app/api.py       ciclo del agente + endpoints
   ↓
web/app.py       Streamlit
```

Cada capa solo conoce las de abajo. Los controles no saben que existe un LLM; las
herramientas no saben que existe un front.

---

## 3. Ciclo del agente

El PRD §6.1 exige una interfaz propia de proveedor y un tope de iteraciones visible.
El bucle completo son ~80 líneas:

```python
async def ejecutar_turno(sesion: Sesion, mensaje: str) -> Turno:
    sesion.agregar_usuario(mensaje)
    llamadas: list[ToolCall] = []

    for _ in range(config.max_iteraciones):          # CA1 · tope visible
        respuesta = await llm.enviar(sesion.mensajes, ESQUEMAS)

        if not respuesta.tool_calls:                  # el modelo habla al humano
            sesion.agregar_asistente(respuesta.texto)
            return Turno(respuesta.texto, llamadas,
                         needs_confirmation=hay_confirmacion_pendiente(llamadas))

        for llamada in respuesta.tool_calls:
            resultado = await ejecutar(llamada, ctx)  # C3 · nunca lanza
            registrar_herramienta(...)                # CA4 · out/log.jsonl
            llamadas.append(...)
            sesion.agregar_resultado(llamada.id, resultado)

    return Turno(TOPE_ALCANZADO, llamadas)            # CA1
```

### Confirmación humana (CA3) — el punto de diseño más delicado

Cuando una acción requiere confirmación, el agente **termina el turno** con una pregunta
y solo procede si el siguiente mensaje del usuario confirma.

La trampa está en el front: el botón **"Confirmar" envía un mensaje de usuario normal**,
no una llamada directa a `oc_crear`. Si el botón invocara la herramienta, estaríamos
saltándonos el ciclo del agente, que es justo lo que el PRD califica. Cada request HTTP
**es** un turno, así que "terminar y esperar" es el comportamiento natural de un
servidor: no hace falta orquestarlo.

Defensa en profundidad: **`oc_crear` re-valida internamente** llamando a
`evaluar_controles`. No confía en el payload que recibe. Es la protección contra un
modelo que afirme haber validado sin hacerlo.

---

## 4. Elección del modelo y análisis de costo

**Proveedor:** OpenAI. **Modelo:** `gpt-4o-mini` para el ciclo del agente.

### Por qué el modelo pequeño

Porque el agente **no calcula nada**. Toda la aritmética, los cruces contra maestros y
las diez reglas de control viven en funciones puras de Python; el modelo solo decide
qué herramienta llamar y redacta el resumen en lenguaje natural. Pagar un modelo
frontera para orquestar cinco herramientas con nombres descriptivos es gasto sin
retorno — y lo demuestra el propio `demo.py`, que procesa los 6 casos **sin consumir
un solo token**.

### Costo estimado por caso procesado

Un caso completo son unas 6 llamadas al modelo (leer → validar → evidencia → payload →
preguntar → crear):

| Concepto | Tokens aprox. |
|---|---|
| System prompt + conocimiento del proceso | 1.800 |
| Esquemas de las 5 herramientas | 900 |
| Resultados de herramientas acumulados | 4.000 |
| Mensajes del usuario y respuestas | 1.200 |
| **Entrada total (con repetición de contexto)** | **~22.000** |
| **Salida total** | **~1.500** |

Con tarifas de `gpt-4o-mini` del orden de USD 0,15 por millón de tokens de entrada y
USD 0,60 de salida: **≈ USD 0,004 por caso**, unos **USD 0,05 al mes** para el volumen
descrito. El costo del modelo es ruido frente al tiempo de analista que ahorra.

Con un modelo frontera el mismo flujo costaría ~30 veces más y no mejoraría un solo
resultado, porque el determinismo no sale del modelo: sale de las herramientas.

### Controles de costo (PRD §8)
`MAX_ITERACIONES` (default 25) y `MAX_TOKENS_SESION` (default 100.000) son
configurables por variable de entorno. Un usuario no puede gastar la clave sin límite.

---

## 5. Matriz de controles implementada (RC1–RC10)

Cada control es una **función pura** `(paquete, maestros) → Veredicto`, con su test.
`Veredicto` lleva `codigo`, `tipo`, `detalle` legible para la analista y `datos` para la UI.

| # | Control | Tipo | Implementación | Caso que lo ejercita |
|---|---|---|---|---|
| RC1 | Proveedor existe y está activo | **Bloqueo** | Cruce por NIT normalizado; si no hay NIT, por nombre normalizado | **sol-002** (NIT `901999000` no existe) · **sol-006** (sin NIT, cruza por nombre) |
| RC2 | Aprobación existe, dice "Aprobado" y viene de un aprobador **del centro** | **Bloqueo** | Cuatro causas distintas, cada una con su mensaje y su acción | **sol-003** |
| RC3 | `valor_total` ≤ tope del aprobador | **Bloqueo** | Solo se evalúa si RC2 pasó (ADR-003) | sol-003 → `no_evaluable` |
| RC4 | Subárea pertenece al centro de costo | **Bloqueo** | Pertenencia exacta; el detalle lista las válidas | — (pasa en los 6) |
| RC5 | Desviación cotización vs solicitud ≤ 2 % | Confirmación | Guarda contra división por cero | **sol-004** (6,00 %) |
| RC6 | `indicador_iva` ausente → derivar del proveedor | Confirmación + derivado | Valida el código contra el maestro de IVA | **sol-006** → `C1` |
| RC7 | `condiciones_pago` ausente → derivar | Derivado (solo informa) | Igual que RC6 pero sin confirmar | **sol-006** → `Z030` |
| RC8 | Factura anterior a la solicitud | Confirmación | Marca `retroactiva` | **sol-005** (17 días) |
| RC9 | Fecha de aprobación ≥ `fecha_solicitud` | Confirmación | Comparación de calendario, no de instante | — (pasa en los 6) |
| RC10 | `cantidad × valor_unitario == valor_total` (± 1) | **Bloqueo** | Evalúa `cantidad == 0` antes del cálculo | — (pasa en los 6) |

**Semántica, que es lo que distingue una validación seria:** los bloqueos impiden crear
la OC y `confirmado=True` **no los levanta**. Las confirmaciones sí permiten crear con
confirmación explícita. Los derivados solo se informan.

### La más difícil: RC2 vs RC3 en `sol-003`

`sol-003` dispara ambos a la vez. Aprueba `fvargas@`, que es aprobador de **CC-3030**
(Comercial), pero la solicitud se imputa a **CC-2020** (Administración). Y además los
74 millones exceden el tope de 30 del único aprobador real de ese centro.

Decidí que **RC3 solo se evalúa si RC2 pasó**, y lo expresé en la firma:

```python
def rc3_tope(paquete: Paquete, maestros: Maestros, rc2: Veredicto) -> Veredicto:
```

RC3 **recibe el veredicto de RC2 como parámetro obligatorio**, así la dependencia vive
en el tipo y no en un comentario que alguien puede ignorar. El razonamiento: RC3 dice
*"≤ el tope **del aprobador** para ese centro"*; si el aprobador no pertenece al centro,
no existe un tope aplicable, y evaluarlo contra el tope de un tercero que no aprobó
sería inventar un control. Reportar dos bloqueos donde hay una causa raíz también
confunde sobre qué corregir.

Hay un test que prueba que **con el centro correcto `sol-003` habría pasado el tope**
(74M ≤ 80M en CC-3030): el defecto es de **autoridad, no de monto**.

---

## 6. Diseño del adaptador SAP real para producción

El PRD advierte que *"la viabilidad de conectarse a SAP no está confirmada"*. Por eso la
interfaz está aislada desde el primer día:

```python
class SapAdapter(Protocol):
    async def consultar_proveedor(self, nit: str) -> dict | None: ...
    async def crear_orden(self, orden: OrdenCompra) -> dict[str, str]: ...
    async def buscar_orden_por_referencia(self, solicitud_id: str) -> dict | None: ...
```

`SapMock` la implementa sobre `out/sap/ordenes.jsonl`. Sustituirla por el adaptador real
no toca ni los controles ni las herramientas ni el ciclo del agente. Los métodos son
`async` precisamente porque el adaptador real sí hará I/O de red.

### Opción elegida: OData `API_PURCHASEORDER_PROCESS_SRV`

| Opción | Veredicto |
|---|---|
| **OData `API_PURCHASEORDER_PROCESS_SRV`** | **Elegida.** REST/JSON, contrato versionado y documentado, autenticación OAuth2 estándar, soporta `$batch` e idempotencia por cabecera. No exige conectividad de red a nivel RFC. |
| RFC/BAPI `BAPI_PO_CREATE1` | Descartada como primera opción: exige `pyrfc` + el SAP NetWeaver RFC SDK (binario con licencia), conectividad al puerto de gateway, y un `BAPI_TRANSACTION_COMMIT` explícito cuyo manejo de error parcial es sutil. Es el plan si OData no está activado. |
| SAP Integration Suite | Descartada por costo y tiempo: añade una plataforma intermedia para un volumen de ~1 OC por factura. |
| Carga por archivo | Es el **plan B**, no la opción principal. |

### Mapeo del payload

| Nuestro campo (§7.4) | OData |
|---|---|
| `sociedad` | `CompanyCode` |
| `organizacion_compras` | `PurchasingOrganization` |
| `proveedor.codigo_sap` | `Supplier` |
| `moneda` | `DocumentCurrency` |
| `condiciones_pago` | `PaymentTerms` |
| `referencia.solicitud_id` | `PurchaseOrderByCustomer` (clave de idempotencia) |
| `posiciones[].numero` | `PurchaseOrderItem` (10, 20, 30…) |
| `posiciones[].descripcion` | `PurchaseOrderItemText` (**máx. 40 caracteres**) |
| `posiciones[].unidad` | `PurchaseOrderQuantityUnit` (`UN`→`EA`, `H`→`HUR`, `MES`→`MON`) |
| `posiciones[].centro_costo` | `CostCenter` del `to_PurchaseOrderItemAcctAssgmt` |
| `posiciones[].indicador_iva` | `TaxCode` |
| `aprobador.evidencia_sha256` | adjunto vía `AttachmentContent` + el hash en texto de cabecera |

**Conversión pendiente y declarada:** nuestro `precio_unitario` lleva IVA incluido
(ADR-007). SAP espera base gravable + `TaxCode`. El adaptador real necesita un paso de
conversión usando `indicadores-iva.json`, y hay que decidir con contabilidad qué
magnitud gobierna el tope de aprobación.

### Autenticación y credenciales
OAuth2 *client credentials* contra el servicio de autorización del tenant. El
`client_secret` vive en un gestor de secretos (Azure Key Vault o similar), **nunca** en
el repositorio, en el prompt, en los logs ni en el contexto del modelo. El agente no ve
credenciales: pide al adaptador, y el adaptador las resuelve.

### Idempotencia ante reintentos
Es el riesgo real de una integración de OC: un timeout tras un commit exitoso crea una
orden duplicada al reintentar. Tres capas:

1. **Antes de crear**, `buscar_orden_por_referencia(solicitud_id)` consulta
   `PurchaseOrderByCustomer`. Si existe, se devuelve esa y no se crea otra. Es
   exactamente lo que ya hace el mock y lo que el test de idempotencia verifica.
2. Cabecera `Idempotency-Key` con el `solicitud_id` en el POST.
3. **Ante error parcial** (cabecera creada, posición rechazada), no se reintenta a
   ciegas: se consulta por referencia, y si la orden existe incompleta se reporta al
   humano con el número y el detalle del rechazo. Una OC a medias es un problema
   contable, no algo que un agente deba resolver solo.

### Plan B si la conexión no es viable
El agente **igual ahorra la mayor parte del trabajo**, porque el valor está en los
controles, no en el `INSERT`:

1. Los 10 controles corren igual: nada de eso necesita SAP.
2. `out/<caso>/payload.json` + un `valores-oc.md` con los campos en el orden exacto de
   la transacción `ME21N`, listos para copiar y pegar.
3. Un archivo de **carga masiva** (CSV o LSMW/LTMC) con todas las OC aptas del día.
4. `out/control.csv` sigue midiendo el desvío retroactivo, que es el entregable que la
   dirección pidió y que no depende de la integración.

---

## 7. Lectura del proceso: las OC retroactivas

> Sección requerida por PRD §9.1.7 — media página a la dirección.

**El hallazgo, con el dato duro.** En `sol-005` la cronología real de los fixtures es:
cotización el **2026-08-05**, factura el **2026-08-10**, solicitud formal el
**2026-08-27**. La orden de compra se está creando **17 días después de que llegó la
factura** y 22 después de la cotización. El propio correo de aprobación lo dice sin
rodeos: *"Ya llegó la factura, por favor crear la OC para poder radicarla."*

**Por qué importa más de lo que parece.** Una OC retroactiva no es un retraso
administrativo: es un control que se ejecutó al revés. La orden de compra existe para
autorizar un gasto **antes** de comprometerlo. Cuando se emite después de la factura,
la aprobación del líder deja de ser una decisión y pasa a ser una formalidad — ya no
puede decir no, porque el proveedor ya entregó y ya facturó. El control sigue en el
papel, pero perdió su función.

**Lo que esto le cuesta a la compañía.** Se pierde capacidad de negociación (no se
puede discutir precio sobre una factura emitida), se pierde la trazabilidad
presupuestal del período, y auditoría recibe evidencia construida después del hecho.
En `sol-003` se ve el daño colateral: alguien aprobó un gasto de un centro de costo que
no le corresponde, y nadie lo detectó porque la verificación era mental.

**Lo que propongo, en tres pasos.**

1. **Medir antes de prohibir.** El agente ya marca `retroactiva = true` en
   `out/control.csv` en cada intento. En un mes se sabe el porcentaje real, por centro
   de costo y por solicitante. Hoy ese número no existe y sin él cualquier política es
   a ciegas.
2. **No bloquear todavía.** Si se rechazan las OC retroactivas de golpe, se paraliza la
   radicación de facturas legítimas y el proceso se mueve a un canal informal donde ya
   no se puede medir. Se permiten **con confirmación explícita y registro nominal** de
   quién confirmó — que es exactamente lo que implementa RC8.
3. **Atacar la causa, no el síntoma.** La retroactividad casi siempre significa que
   pedir la OC a tiempo es más costoso que saltarse el paso. Con el dato del punto 1 se
   puede priorizar: si el 80 % se concentra en compras recurrentes de bajo monto, la
   solución es un contrato marco con OC abierta, no más disciplina.

**El umbral que recomiendo discutir:** retroactividad aceptable por debajo del 5 % de
las OC del mes. Por encima, el problema es de diseño del proceso, no de cumplimiento
individual.

---

## 8. Decisiones y trade-offs

Las diez decisiones están registradas con su contexto en
[`specs/02-decisions.md`](specs/02-decisions.md). Las cuatro de más peso:

### ADR-001 · Python en lugar de TypeScript

**El PRD exige TypeScript en seis lugares distintos** (§0 Lenguaje, §6.2 contrato de
herramientas, §6.5 estructura, §6.6 `demo.ts`, §8 `zod` obligatorio y *"TypeScript sin
`any`"*, §9.4 bonus). Se entrega en Python.

**Por qué:** el PRD también impone *"debes poder explicar cada línea que entregas"* y la
aprobación depende de una defensa técnica en vivo. Entre incumplir el requisito de
lenguaje y arriesgar la defensa, prioricé la defensa.

**Qué se preservó:** el 100 % del contrato semántico — nombres `oc_*`, forma
`{ok, data}` / `{ok, error}`, reglas CA1–CA5, controles RC1–RC10, y la separación
comportamiento/conocimiento/ejecución.

| PRD | Entregado | Equivalencia |
|---|---|---|
| `zod` | **Pydantic v2** | Validación declarativa + JSON Schema para el tool-calling |
| `demo.ts` | **`demo.py`** | Mismo contrato: los 6 casos sin LLM |
| `TypeScript sin any` | **`mypy --strict`** | Tipado estricto verificable |

**Alternativa descartada:** entregar en TypeScript con un documento de apoyo.
Descartada por el riesgo en la defensa oral.

### ADR-002 · Sin LangGraph ni LangChain

La intuición inicial era que el reto pedía un grafo de estados complejo. El análisis del
flujo lo desmiente: es lineal con dos compuertas, y las compuertas viven **dentro de
`oc_validar`** (son datos), no en la orquestación.

Tres razones concretas: el PRD pide un **adaptador propio**, y LangChain obligaría a
envolver su abstracción en otra para cumplirlo; CA1 exige un tope de iteraciones
**visible**, que un framework esconde en su runtime; y lo que LangGraph aporta aquí no
se usa (el PRD dice *"persistencia: no, sin base de datos"*, y CA3 es más simple porque
cada request HTTP ya **es** un turno).

**Resultado:** cinco dependencias en total. `pydantic`, `openai`, `fastapi`, `uvicorn`,
`streamlit`. El bucle son 80 líneas que puedo señalar con el dedo.

### ADR-003 · RC3 se evalúa solo si RC2 pasó
Ver §5. La alternativa — reportar ambos bloqueos — se descartó porque duplica una causa
raíz única y confunde sobre qué corregir.

### ADR-009 · `disallow_any_explicit` desactivado

Arranqué con esa opción como equivalente estricto de *"sin `any`"*. Resultó
**incompatible con Pydantic v2** por una razón que no era obvia: `pydantic/main.py`
declara `__pydantic_extra__: Dict[str, Any]` como campo de `dataclass_transform`, y mypy
propaga ese `Any` heredado al `__init__` sintetizado de **toda** subclase. Con la opción
activa, `class A(BaseModel): pass` falla.

Se desactivó globalmente conservando `strict = true`, que ya prohíbe el `Any`
**implícito** — el peligroso. El `Any` explícito que queda vive solo en la frontera de
deserialización de JSON, donde la forma del dato se desconoce por definición hasta que
Pydantic la valida.

---

## 9. Supuestos tomados al interpretar el PRD

| # | Supuesto | Origen |
|---|---|---|
| S1 | `descripcion` se trunca en frontera de palabra sin exceder 40 caracteres, y la completa se preserva en `trazabilidad.json`. **Las 6 descripciones de los fixtures exceden el límite** (sol-001 mide 73), así que sin truncado no se puede construir ningún payload. | ADR-004 |
| S2 | `unidad` no viene en ningún fixture y se infiere del sustantivo núcleo del ítem de la cotización. En producción debería venir del maestro de materiales. | ADR-005 |
| S3 | `confirmado_por` es el identificador de sesión, la única identidad que existe en el sistema (el PRD descarta autenticación). En producción debe ser la identidad corporativa (SSO) de quien confirma, porque es evidencia de auditoría. | ADR-006 |
| S4 | `valor_unitario` se registra con IVA incluido, igual que la solicitud, para que RC3, RC5 y RC10 operen sobre la misma magnitud. SAP real requiere base gravable + `TaxCode`. | ADR-007 |
| S5 | El NIT se normaliza quitando puntos, espacios y el dígito de verificación cuando viene tras guion. Sin esto RC1 falla contra la cotización en los 6 casos. | ADR-008 |
| S6 | La comparación de fechas es de **calendario**, no de instante: se recortan a `YYYY-MM-DD` y se comparan como cadenas. La aprobación trae offset `-05:00` y la solicitud no; convertir a `datetime` metería zonas horarias en una regla que no las tiene. | T1.5 |
| S7 | `normalizar_nombre` elimina sufijos societarios (`S.A.S.`, `LTDA`) en cualquier posición, no solo al final. Verificado: los 5 proveedores del maestro dan 5 valores únicos e idénticos a los de una variante que solo quite el sufijo final. Con un maestro de miles de proveedores podría colisionar (`"Grupo SA Internacional"` → `"grupo internacional"`). | QA Fase 0 |
| S8 | Los maestros de los fixtures son completos. En producción se consultan en SAP en tiempo real, y `consultar_proveedor` del adaptador existe justamente para eso. | PRD §10 |

---

## 10. Cobertura de historias de usuario

| HU | Estado | Qué falta para producción |
|---|---|---|
| HU-1 · Leer el paquete | — | OCR para PDF escaneados; lectura de `.xlsx` real (P1 del PRD) |
| HU-2 · Validar contra maestros | — | Consulta de maestros en vivo contra SAP |
| HU-3 · Construir el payload | — | Conversión a base gravable (S4) |
| HU-4 · Evidencia de aprobación | — | Firma digital si auditoría la exige; PDF (P1) |
| HU-5 · Crear la OC | — | Adaptador OData real (§6) |
| HU-6 · Manejo de errores | — | — |

*(Esta tabla se completa con el estado real al cerrar las fases 2 a 5.)*

---

## 11. Uso de IA en la construcción

**Asistente usado:** Claude (Claude Code), en una sesión de trabajo continua.

**Metodología: SDD (Spec-Driven Development)** con cinco roles ejecutados como
subagentes — orquestador, planificador, especificador, ejecutor y QA. Los artefactos
están en [`specs/`](specs/) y son parte del entregable:

| Artefacto | Qué es |
|---|---|
| `specs/00-constitution.md` | 10 reglas no negociables derivadas del PRD |
| `specs/01-oracle.md` | **La matriz de resultados esperados de los 6 casos**, trazada a mano antes de escribir código |
| `specs/02-decisions.md` | 10 ADR |
| `specs/03-plan.md` | 6 fases con gates ejecutables |
| `specs/tasks/` | Una especificación por tarea, con firmas y valores esperados |
| `specs/qa/` | Informes de QA y auditoría en lenguaje llano |

**Qué descarté de lo que la IA propuso:**

1. **LangGraph.** Era la sugerencia natural para un "agente con grafo". La descarté al
   ver que el PRD pide un adaptador propio y un tope de iteraciones visible: el
   framework habría escondido justo lo que se evalúa (ADR-002).
2. **`disallow_any_explicit = true`.** Parecía el equivalente correcto de *"sin `any`"*.
   Resultó incompatible con Pydantic por una razón no obvia, y el ejecutor la aisló con
   repros mínimos en lugar de suprimir el error (ADR-009).
3. **Reportar RC2 y RC3 como dos bloqueos** en `sol-003`. Técnicamente ambos fallan,
   pero duplicar una causa raíz confunde a quien debe corregirla (ADR-003).

**Lo que el método atrapó y una revisión normal no habría visto.** Tres ejemplos reales:

- El modelo `Correo` tenía `extra="forbid"` declarando 4 campos, pero el `correo.json`
  real trae 8. Habría lanzado `ValidationError` al armar el paquete. Los tests estaban
  verdes porque ninguno validaba `Correo` contra el fixture real.
- El QA buscó ese **mismo patrón** en los otros 19 modelos y encontró `Aprobacion`, que
  falla en 6 de 6 casos. Se cerró fijando con tests la forma de construcción, más un
  test de regresión que afirma que el parseo directo *debe* fallar.
- `cargar_config` lanzaba *"Falta OPENAI_API_KEY. Cópiala en `.env`"* **con el `.env` en
  su sitio y la clave correcta dentro**: el código nunca leía ese archivo. El mensaje de
  error mentía (ADR-010).

Puedo explicar cada línea entregada. La auditoría en lenguaje llano de
`specs/qa/auditoria-f0.md` existe precisamente para sustentarlo.

---

## 12. Riesgos de llevar esto a producción

| Riesgo | Severidad | Mitigación |
|---|---|---|
| **El modelo "arregla" un monto** para que cuadre con la cotización | Alta | Los montos salen de las herramientas, no del modelo (CA2). `oc_crear` re-valida internamente y no confía en el payload recibido. El prompt lo prohíbe; el diseño lo hace innecesario. |
| **La conexión a SAP no es viable** a corto plazo | Alta | `SapAdapter` aislado desde el día uno + plan B de §6: los controles corren igual y se entrega archivo de carga masiva. |
| **OC duplicadas por reintentos** tras un timeout | Alta | Tres capas de idempotencia (§6), con test que verifica que un segundo `crear` no escribe una segunda orden. |
| **PDF escaneados** sin texto extraíble | Media | Hoy fuera de alcance (los fixtures traen texto). Requiere OCR con umbral de confianza y revisión humana bajo ese umbral. |
| **Maestros desactualizados** respecto a SAP | Media | `consultar_proveedor` del adaptador existe para consultar en vivo; hoy lee fixtures. |
| **El correo de aprobación como evidencia** puede no bastarle a auditoría | Media | Se guarda `sha256` del contenido exacto. Si se exige más, hay que pasar a firma digital. |
| **Un `.txt` con formato distinto** rompe el parseo | Media | El parseo devuelve `None` por campo ausente y nunca inventa (CA2); el campo faltante se convierte en bloqueo o confirmación, no en un dato falso. |
| **Prompt injection** desde el cuerpo de un correo | Media | El modelo no puede ejecutar nada fuera de las 5 herramientas, ninguna ejecuta shell, y la escritura está confinada a `out/` por `ruta_salida`. Pendiente para producción: marcar el contenido de correo como no confiable en el prompt. |
| **Costo descontrolado** de la clave | Baja | Topes de iteraciones y de tokens por sesión, configurables. |
