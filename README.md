# Reto 03 · Agente conversacional de Órdenes de Compra SAP

> Prueba técnica · **Periferia IT Group** · Cristian Gaviria Giraldo

Agente que lee un paquete de compra (correo + solicitud + cotización + aprobación), lo
valida contra diez controles de negocio, construye la OC como quedaría en SAP y la crea
en un SAP simulado. Pide confirmación humana cuando una excepción lo exige y deja
trazabilidad de cada valor.

El diseño, las decisiones y los trade-offs están en
[`SOLUCION.md`](reto-03-oc-sap/SOLUCION.md); la metodología y el criterio de aceptación,
en [`specs/`](reto-03-oc-sap/specs/).

**El código vive en [`reto-03-oc-sap/`](reto-03-oc-sap/).**

---

## Enlace público

> ### 👉 https://dthqchyfexanhboh8faz4e.streamlit.app/
>
> Sin clave de acceso: el enlace es público (PRD §6.1 permite no autenticar).
>
> **Si tarda en cargar la primera vez, es normal.** Streamlit Community Cloud suspende
> las aplicaciones inactivas y el primer arranque tiene que instalar dependencias y
> despertar el contenedor: entre 30 segundos y 2 minutos. Una vez despierta responde en
> menos de 2 segundos.
>
> Los pasos de publicación y el mantenimiento están en
> [`DEPLOY.md`](reto-03-oc-sap/DEPLOY.md).

---

## Levantar en local (un comando)

Requiere [uv](https://docs.astral.sh/uv/) y Python 3.12.

```bash
cd reto-03-oc-sap
uv sync && uv run streamlit run web/app.py
```

Abre `http://localhost:8501`. El front llama al ciclo del agente **en el mismo proceso**:
no hay que levantar nada más.

> **Todos los comandos van con `uv run` delante.** Es lo que los ejecuta dentro del
> entorno del proyecto (Python 3.12). Sin el prefijo se usa el `python` del sistema, que
> probablemente sea otra versión y no tenga las dependencias.

<details>
<summary>Sin <code>uv</code></summary>

```bash
cd reto-03-oc-sap
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run web/app.py
```

Con el entorno activado, los comandos funcionan sin el prefijo `uv run`.
</details>

### Variables de entorno

Copia `.env.example` a `.env` y pon la clave. Es el único lugar donde vive: la clave
nunca entra al repositorio, ni al front, ni a los logs, ni a `/api/health`.

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
repositorio: ver [`DEPLOY.md`](reto-03-oc-sap/DEPLOY.md).

---

## `demo.py` — los 6 casos sin modelo de lenguaje

```bash
cd reto-03-oc-sap
uv run python demo.py
```

**No requiere clave de ningún proveedor** (PRD §6.6): llama a las herramientas
directamente, sin consumir un token. Procesa los seis casos de
`fixtures/reto-03/solicitudes/` e imprime por caso `apta`, bloqueos, confirmaciones,
`retroactiva` y el número de OC o el motivo del rechazo. Además demuestra la
idempotencia —`sol-001` dos veces, misma OC— y una confirmación explícita para
`sol-004`.

Es la prueba de que el determinismo no sale del modelo, sino de las herramientas y los
diez controles. **Es la forma más rápida de verificar la solución.**

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

Todos desde `reto-03-oc-sap/`:

```bash
uv run pytest -v                      # 405 pruebas
uv run mypy --strict app/ demo.py     # tipado estricto (equivalente a "TypeScript sin any")
uv run ruff check app/ tests/ web/    # lint
uv run uvicorn app.api:app --reload   # la API HTTP por separado, en localhost:8000
uv run python modulo/construir.py     # regenerar el módulo reutilizable del bonus
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
| `GET` | `/api/health` | `{ok, provider, model, maxIteraciones}` — **nunca** la clave |

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
reto-03-oc-sap/
├── agent/prompt.md          comportamiento del agente (system prompt)
├── app/
│   ├── knowledge/           conocimiento del proceso de compras
│   ├── tools/oc.py          las cinco herramientas oc_*
│   ├── controles/           RC1–RC10, funciones puras
│   ├── core/agente.py       el ciclo modelo → herramientas → modelo (CA1, CA3, CA5)
│   ├── llm/                 adaptador del proveedor: cambiarlo no toca el ciclo
│   ├── sap/                 interfaz SapAdapter + implementación simulada
│   └── api.py               la API HTTP (FastAPI)
├── web/app.py               el chat (Streamlit)
├── demo.py                  los 6 casos sin modelo
├── modulo/                  el agente empaquetado como módulo reutilizable (bonus §9.4)
├── fixtures/                entregados por Periferia · solo lectura
├── out/                     todo lo que se genera en ejecución
├── specs/                   PRD, constitución, oráculo de aceptación, ADR y plan
├── SOLUCION.md              arquitectura, decisiones, riesgos
└── DEPLOY.md                despliegue y mantenimiento
```

Los instructivos en PDF y la carpeta original descomprimida del reto no se versionan
(ver [`.gitignore`](.gitignore)).

---

## Nota sobre el lenguaje

El PRD pedía **TypeScript** y la solución se entrega en **Python 3.12**, con el front en
Streamlit. Fue una decisión consciente del candidato, para poder auditar y defender cada
línea en la sesión técnica — requisito que el propio PRD impone.

Equivalencias adoptadas: `zod` → **Pydantic v2**, `demo.ts` → **`demo.py`**,
*"TypeScript sin `any`"* → **`mypy --strict`**.

El contrato semántico se respeta al 100%: nombres de herramientas `oc_*`, formas de
entrada/salida `{ok, data}` / `{ok, error}`, reglas del ciclo CA1–CA5, controles
RC1–RC10, y la separación entre comportamiento, conocimiento y ejecución.

El razonamiento completo está en `ADR-001` de
[`specs/02-decisions.md`](reto-03-oc-sap/specs/02-decisions.md) y en
[`SOLUCION.md` §8](reto-03-oc-sap/SOLUCION.md).
