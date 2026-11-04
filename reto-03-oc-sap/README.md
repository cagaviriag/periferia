# Reto 03 · Agente conversacional de Órdenes de Compra SAP

Agente que lee un paquete de compra (correo + solicitud + cotización + aprobación), lo
valida contra diez controles de negocio, construye la OC como quedaría en SAP y la crea
en un SAP simulado. Pide confirmación humana cuando una excepción lo exige y deja
trazabilidad de cada valor.

Periferia IT Group · prueba técnica. El diseño, las decisiones y los trade-offs están en
[`SOLUCION.md`](SOLUCION.md); la metodología y las reglas no negociables, en
[`CLAUDE.md`](CLAUDE.md) y [`specs/`](specs/).

---

## Enlace público

> **Chat:** _<pendiente: pegar aquí la URL de Streamlit Community Cloud>_
>
> Sin clave de acceso: el enlace es público (PRD §6.1 permite no autenticar).
> Los pasos para publicarlo están en [`DEPLOY.md`](DEPLOY.md).

---

## Levantar en local (un comando)

Requiere [uv](https://docs.astral.sh/uv/) y Python 3.12.

```bash
uv sync && uv run streamlit run web/app.py
```

Abre `http://localhost:8501`. El front llama al ciclo del agente **en el mismo proceso**:
no hay que levantar nada más.

<details>
<summary>Sin <code>uv</code></summary>

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run web/app.py
```
</details>

### Variables de entorno

Copia `.env.example` a `.env` y pon la clave. Es el único lugar donde vive (C7: la clave
nunca entra al repositorio, ni al front, ni a los logs, ni a `/api/health`).

```bash
cp .env.example .env     # y edita OPENAI_API_KEY
```

| Variable | Obligatoria | Default | Para qué |
|---|---|---|---|
| `OPENAI_API_KEY` | **Sí** (no para `demo.py`) | — | Clave del proveedor del modelo |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Modelo que orquesta las herramientas |
| `MAX_ITERACIONES` | No | `25` | Tope de iteraciones herramienta → modelo por turno (CA1) |
| `MAX_TOKENS_SESION` | No | `100000` | Tope de tokens por sesión (PRD §8 Costo) |
| `LLM_TIMEOUT_SEGUNDOS` | No | `60` | Timeout al proveedor (PRD §8 Robustez) |
| `API_URL` | No | — | Si se define, el front habla con la API HTTP en esa URL en vez de llamar al agente en proceso |

En Streamlit Community Cloud estas variables se pegan en **Settings → Secrets**, no en el
repositorio: ver [`DEPLOY.md`](DEPLOY.md).

---

## `demo.py` — los 6 casos sin modelo

```bash
uv run python demo.py
```

**No requiere clave de ningún proveedor** (PRD §6.6): llama a las herramientas
directamente, sin consumir un token. Procesa los seis casos de
`fixtures/reto-03/solicitudes/` e imprime por caso `apta`, bloqueos, confirmaciones,
`retroactiva` y el número de OC o el motivo del rechazo. Además demuestra la
idempotencia —`sol-001` dos veces, misma OC— y una confirmación explícita para
`sol-004`. Es la prueba de que el determinismo no sale del modelo, sino de las
herramientas y los diez controles.

Artefactos que deja en `out/` (se limpia al inicio de cada corrida):

| Ruta | Qué es |
|---|---|
| `out/<caso>/trazabilidad.json` | Fuente de cada campo del payload: `solicitud`, `cotizacion`, `maestro.<nombre>` o `derivado` (HU-3) |
| `out/<caso>/aprobacion.txt` | Evidencia del correo de aprobación con su `sha256` (HU-4) |
| `out/sap/ordenes.jsonl` | Las OC creadas, una línea por OC real |
| `out/control.csv` | Una fila por **intento**: creado, bloqueado o pendiente (HU-5) |
| `out/log.jsonl` | Bitácora de toda llamada a herramienta (CA4) |

---

## Otros comandos

```bash
uv run pytest -v                      # suite de pruebas
uv run mypy --strict app/             # tipado estricto (equivalente a "TypeScript sin any")
uv run ruff check app/ web/ demo.py   # lint
uv run uvicorn app.api:app --reload   # la API HTTP por separado, en localhost:8000
```

---

## La API del chat (PRD §6.4)

El front no la necesita en el despliegue por defecto —llama al agente en proceso—, pero
existe y está probada. Para usarla, levanta `uvicorn` y arranca el front con
`API_URL=http://localhost:8000 uv run streamlit run web/app.py`.

| Método | Ruta | Cuerpo / respuesta |
|---|---|---|
| `POST` | `/api/chat` | `{sessionId, message}` → `{reply, toolCalls[], needsConfirmation}` |
| `GET` | `/api/sessions/{id}` | Historial completo de la sesión, con las llamadas a herramientas |
| `GET` | `/api/health` | `{ok, provider, model, maxIteraciones}` — **nunca** la clave (C7) |

---

## Cómo se usa el chat

1. La barra lateral tiene un botón por cada uno de los seis casos: cargan el texto y lo
   envían, para no tipear en la defensa.
2. El panel derecho muestra, del turno en curso: **la OC como quedaría en SAP en tabla**,
   los diez controles con su veredicto y todas las llamadas a herramientas con sus
   argumentos y resultado.
3. Cuando el agente pide confirmación, aparece un bloque resaltado con los montos en
   juego (por ejemplo, solicitud `25.000.000` vs cotización `26.500.000`, desviación
   `6,00 %`) y dos botones.
4. **El botón "Confirmar" envía un mensaje de usuario normal** (`confirmo`): no llama a
   `oc_crear` ni a ninguna herramienta. Es el ciclo del agente el que decide, que es
   justo lo que exige CA3 del PRD.

Prompt de la demo (PRD §11), con sesión nueva:

```
Procesa la solicitud "sol-004". Muéstrame la OC como quedaría en SAP, qué
validaciones pasó y cuáles no, y no la crees hasta que yo lo confirme.
```

---

## Estructura

```
agent/prompt.md                 comportamiento del agente (system prompt, C4)
app/knowledge/                  conocimiento del proceso de compras (C4)
app/tools/oc.py                 las cinco herramientas oc_* (C4)
app/controles/                  RC1–RC10, funciones puras
app/core/agente.py              el ciclo modelo → herramientas → modelo (CA1, CA3, CA5)
app/llm/                        adaptador del proveedor: cambiarlo no toca el ciclo
app/sap/                        interfaz SapAdapter + implementación simulada
app/api.py                      la API HTTP (FastAPI)
web/app.py                      el chat (Streamlit)
demo.py                         los 6 casos sin modelo
fixtures/                       entregados por Periferia · solo lectura
out/                            todo lo que se genera en ejecución
specs/                          PRD, constitución, oráculo de aceptación, ADR y plan
```

**Desviación declarada:** el PRD pide TypeScript; se entrega en **Python 3.12** por
decisión del candidato, para poder auditar y defender cada línea en la sesión técnica.
`zod` → **Pydantic v2**, `demo.ts` → `demo.py`, `TypeScript sin any` → `mypy --strict`.
El contrato semántico (nombres de herramientas, formas de entrada/salida, CA1–CA5,
RC1–RC10) se respeta al 100 %. Ver `ADR-001` en [`specs/02-decisions.md`](specs/02-decisions.md).
