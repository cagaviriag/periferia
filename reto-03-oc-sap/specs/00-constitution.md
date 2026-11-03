# Constitución del proyecto — Reto 03 · Órdenes de Compra SAP

> Reglas no negociables. Ningún subagente puede violarlas sin una ADR aprobada en `02-decisions.md`.
> Fuente: `specs/PRD-original.md` + `detalle-instrucciones-reto-3.pdf`.

---

## C1 · Los fixtures son inmutables

`fixtures/` está montado en solo lectura (`chmod a-w`). Toda escritura va a `out/`.
Cualquier tarea que necesite modificar un fixture está mal especificada.

## C2 · Las herramientas son la única fuente de valores

Regla CA2 del PRD. El modelo **no puede afirmar un valor** que no haya salido de una
herramienta. No hay excepciones: ni un total, ni una fecha, ni un código SAP.
El prompt lo prohíbe explícitamente y el diseño lo hace innecesario.

**Consecuencia de diseño:** el LLM orquesta y redacta; nunca calcula. Si un número
aparece en la respuesta al usuario, existe un `tool_call` previo que lo produjo.

## C3 · Ninguna herramienta lanza excepciones

Toda herramienta devuelve un **string** con JSON serializado:

```python
{"ok": True,  "data": {...}}
{"ok": False, "error": "mensaje legible para el usuario"}
```

Un `try/except` envolvente es obligatorio en cada `execute`. Una excepción que
escape a la capa del agente es un defecto bloqueante.

## C4 · Separación de comportamiento, conocimiento y ejecución

| Qué | Dónde | Regla |
|---|---|---|
| **Comportamiento** | `agent/prompt.md` | System prompt en Markdown, nunca embebido en código |
| **Conocimiento** | `app/knowledge/ordenes-compra.md` | El proceso de negocio que el agente consulta |
| **Ejecución** | `app/tools/oc.py` | Las herramientas |

El PRD lo evalúa textualmente: *"Un cambio de reglas de negocio no debería tocar el servidor."*

## C5 · Confirmación humana con turno real

Regla CA3. Cuando una acción requiere confirmación, el agente **termina el turno**
con una pregunta explícita y solo procede si **el siguiente mensaje del usuario**
confirma.

**Prohibido:** que el botón "Confirmar" del front llame a `oc_crear` directamente.
El botón envía un mensaje de usuario normal y el ciclo del agente decide. Saltarse
esto invalida la regla que el PRD está calificando.

## C6 · Tope de iteraciones y de costo

Regla CA1 + requisito no funcional de Costo. Tope de iteraciones herramienta→modelo
por turno configurable (default 25). Tope de tokens por sesión configurable.
Al alcanzar el tope, el agente responde con lo que tiene y lo que falta.

## C7 · La clave del LLM nunca sale del backend

Solo variable de entorno del proceso FastAPI. Nunca en: el repositorio, el front
Streamlit, los logs, `out/`, ni las respuestas de la API. `/api/health` reporta
`provider` y `model` pero jamás la clave.

## C8 · Trazabilidad total

- Cada valor del payload es trazable a `solicitud`, `cotizacion`, `maestro.<nombre>`
  o `derivado`, registrado en `out/<caso>/trazabilidad.json` (HU-3).
- Cada llamada a herramienta se registra en `out/log.jsonl` (CA4) y es visible en el chat.
- Cada intento de OC (exitoso, bloqueado o pendiente) agrega fila a `out/control.csv` (HU-5).

## C9 · Determinismo

`demo.py` produce el mismo resultado en ejecuciones consecutivas, salvo timestamps.
`out/` se limpia al inicio. Ninguna regla de control depende del LLM: RC1–RC10 son
funciones puras sobre datos.

## C10 · Idempotencia de la creación

Crear dos veces el mismo `solicitud_id` devuelve el número de OC existente con
`idempotente: true`. Nunca una segunda OC. Numeración secuencial desde `4500000001`.

---

## Desviación declarada frente al PRD

El PRD exige **TypeScript + zod + `demo.ts`** en seis lugares distintos. Este proyecto
se entrega en **Python**. Es una decisión explícita del candidato (ver `ADR-001`),
tomada para poder auditar y defender cada línea en la sesión técnica, requisito que el
propio PRD impone: *"Debes poder explicar cada línea que entregas."*

Equivalencias adoptadas, documentadas en `SOLUCION.md`:

| PRD exige | Entregamos | Equivalencia |
|---|---|---|
| TypeScript (Bun/Node 20+) | Python 3.12 | — |
| `zod` para args de herramientas | **Pydantic v2** | Validación declarativa + JSON Schema para el tool-calling |
| `demo.ts` | **`demo.py`** | Mismo contrato: procesa los 6 casos sin LLM |
| `src/tools/oc.ts` | `app/tools/oc.py` | Mismos nombres de herramienta (`oc_*`) |
| Front React/Svelte/Vue/HTML | **Streamlit** | Cumple los 5 comportamientos de §6.1 |
| `TypeScript sin any` | **`mypy --strict`** | Equivalente verificable de tipado estricto |

El contrato semántico del PRD (nombres de herramientas, forma de entrada/salida,
reglas CA1–CA5, RC1–RC10) se respeta al 100%. Solo cambia el lenguaje.
