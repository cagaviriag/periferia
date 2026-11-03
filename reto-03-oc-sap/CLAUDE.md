# Reto 03 · Agente de Órdenes de Compra SAP — Periferia IT Group

Prueba técnica. Agente conversacional que lee un paquete de compra (correo + solicitud
+ cotización + aprobación), lo valida contra 10 controles y crea la OC en un SAP simulado.

## Metodología: SDD (Spec-Driven Development)

El trabajo se ejecuta mediante cinco subagentes. **Las especificaciones manda sobre el
código**: si el código contradice `specs/01-oracle.md`, el código está mal.

| Subagente | Rol | Escribe código |
|---|---|---|
| `sdd-orquestador` | Decide qué sigue, despacha, registra estado | No |
| `sdd-planificador` | Fases, dependencias y gates | No |
| `sdd-especificador` | Tareas sin ambigüedad en `specs/tasks/` | No |
| `sdd-ejecutor` | Implementa **una** tarea, sin ampliar alcance | Sí |
| `sdd-qa` | Verifica ejecutando; reporta defectos | No |

**Flujo:** orquestador → especificador → ejecutor → QA → (aprueba) → siguiente fase.
Una fase no avanza sin aprobación de QA.

## Documentos rectores

| Archivo | Qué es |
|---|---|
| `specs/PRD-original.md` | El requisito del cliente. **Inmutable.** |
| `specs/00-constitution.md` | Reglas no negociables C1–C10 |
| `specs/01-oracle.md` | **La verdad de aceptación**: matriz de los 6 casos + invariantes |
| `specs/02-decisions.md` | ADR. Toda desviación se registra aquí |
| `specs/03-plan.md` | 6 fases con gates y estado al día |

## Stack

Python 3.12 · Pydantic v2 (sustituye a `zod`) · SDK `openai` directo · FastAPI ·
Streamlit · pytest · `mypy --strict`.

**Sin LangChain ni LangGraph** — el PRD pide un adaptador LLM propio y un ciclo con
tope de iteraciones visible; un framework esconde justo lo que se evalúa (`ADR-002`).

## Reglas que se violan con facilidad

1. **`fixtures/` es de solo lectura** (`chmod a-w`). Toda escritura va a `out/`.
2. **Las herramientas devuelven un string** con JSON (`{"ok": true, "data": ...}`),
   no un dict, y **nunca lanzan excepciones**.
3. **El modelo no puede afirmar ningún valor** que no haya salido de una herramienta.
   El LLM orquesta y redacta; nunca calcula.
4. **El botón "Confirmar" del front envía un mensaje de usuario normal**, no una
   llamada directa a `oc_crear`. Saltarse esto invalida la regla CA3 que se califica.
5. **RC3 solo se evalúa si RC2 pasó** (`ADR-003`).
6. **`oc_generar_evidencia` va antes de `oc_construir_payload`** — el payload consume
   el `sha256` que produce la evidencia.
7. **La clave de OpenAI nunca sale del backend**: ni en el front, ni en logs, ni en
   `out/`, ni en `/api/health`.

## Desviación declarada

El PRD exige TypeScript en seis lugares. Se entrega en **Python** por decisión del
candidato, para poder auditar y defender cada línea en la sesión técnica — requisito
que el propio PRD impone. Ver `ADR-001`; se documenta abiertamente en `SOLUCION.md`.

Consecuencia operativa: **el código debe ser aburrido y legible**, optimizado para que
una persona lo explique en vivo. Sin metaprogramación, sin abstracciones inteligentes.
Nombres en español, coherentes con el dominio del PRD.

## Comandos

```bash
uv sync                 # instalar
python demo.py          # los 6 casos sin LLM (no requiere clave)
pytest -v               # suite
mypy --strict app/      # tipado estricto
```
