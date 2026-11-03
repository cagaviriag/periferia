# Informe de QA — Fase 0 · Cimientos

```
FASE:      0
VEREDICTO: APROBADA con 3 defectos mayores de arrastre
```

El gate de `03-plan.md` para la Fase 0 es *`mypy --strict app/` sin errores + tests de los
4 normalizadores, incluido `900.555.111-2 → 900555111`*. Se cumple. **No se halló ningún
defecto bloqueante**: nada contradice el oráculo ni la constitución hoy. Los tres mayores
son deuda latente que estalla en Fase 2 y Fase 4, no en esta fase.

---

## Comandos ejecutados

| Comando | Salida real |
|---|---|
| `uv run mypy --strict app/` | `Success: no issues found in 10 source files` |
| `uv run pytest -q` | `105 passed in 0.12s` |
| `uv run ruff check` | `All checks passed!` |
| `grep -rn "sk-" app/ tests/` | sin resultados (exit 1) |
| `grep -rn "random\|uuid\|datetime.now" app/` | 1 solo hit: `app/core/log.py:39` (timestamp) |
| `grep -rn "Any" app/` | solo `app/core/io.py` (frontera JSON) → mitigación de ADR-009 **cumplida** |
| `shasum` de `fixtures/` antes y después de `pytest` | `200f8f50…3040c40` **idéntico**, 39 entradas |
| `find out -mindepth 1` tras `pytest` | 0 entradas (los tests usan `tmp_path`) |

Scripts de verificación en `/tmp/qa_f0/`: `puente_f1.py`, `modelos_vs_fixtures.py`,
`escape_escritura.py`, `robustez_y_nombres.py`. No escriben en el repositorio.

---

## Cumplimiento de la constitución

**C1 · fixtures intactos → PASA.** Hash byte a byte idéntico antes y después de la suite.
`fixtures/` sigue en `dr-xr-xr-x`. mtime de los 29 archivos: `16:20:50`, anterior a la
escritura de `app/` (17:02–17:07). Ningún test escribe ahí (ver D6 por el matiz).

**C3 · nada lanza donde no debe → PASA con matices.** `normalizar.py` es puro: 39 entradas
hostiles (`None`, `""`, `"-"`, emojis, combinantes sueltos, 5.000 caracteres, inyecciones)
no producen una sola excepción. `log.py` no propaga ni con `raiz=/`, `/dev/null`,
`/etc/passwd` o `/no-existe-zzz`. `config.py` lanza por diseño (autorizado). `io.py` lanza
y debe: su contrato dice que la herramienta llamadora convierte en `{ok:false}`.

**C7 · la clave no se filtra → PASA hoy, con una pistola cargada (D2).** `resumen_publico`
está limpio. Ningún log recibe la `Config`. La clave real que hay en `.env` (164 caracteres,
empieza por `sk-`) no aparece en ninguna otra parte del repositorio; `.env` está en la
línea 1 de `.gitignore`.

**C9 · determinismo → PASA.** Única fuente de no-determinismo: `datetime.now(timezone.utc)`
en `log.py:39`, y solo alimenta el campo `ts` de `log.jsonl` y `control.csv` — exactamente
lo que C9 e I10 exceptúan. Los 4 normalizadores dan el mismo resultado en 100 corridas.
Sin `random`, sin `uuid`, sin iteración sobre `set`.

---

## Cobertura real contra los fixtures — el hallazgo principal

De las 20 clases de `modelos.py`, estas **no se instancian ni validan en ningún test**:
`Aprobacion`, `Cotizacion`, `Factura`, `Paquete`, `Veredicto`, `ResultadoValidacion`,
`ExcepcionOC`. (`Proveedor`, `Aprobador`, `CentroCosto`, `IndicadorIva`, `CondicionPago` y
`ProveedorOC` sí quedan cubiertas indirectamente, anidadas en `Maestros` y `OrdenCompra`.)

**Sí queda otro modelo con el bug latente de `Correo`: `Aprobacion`.** Es el mismo patrón
exacto. Sus campos `de` y `fecha` tienen el mismo nombre que las claves del
`aprobacion.json` real, lo que invita al parseo directo — y con `extra="forbid"` falla en
los 6 casos:

```
Aprobacion.model_validate(aprobacion.json)  →  FAIL en sol-001…sol-006
  claves del fixture: ['asunto', 'cc', 'cuerpo', 'de', 'fecha', 'para']
  errores: missing 'aprobado', missing 'texto',
           extra_forbidden 'para', 'cc', 'asunto', 'cuerpo'

Variante realista de Fase 2 (enriquecer el dict):
Aprobacion.model_validate({**datos, 'aprobado': ..., 'texto': datos['cuerpo']})
  →  FAIL: extra_forbidden 'para', 'cc', 'asunto', 'cuerpo'

Construcción campo por campo:  →  OK
```

`Cotizacion` y `Factura` **no** tienen el problema: nacen de `cotizacion.txt` y
`factura.txt`, texto plano, así que no existe parseo directo que las tiente. Su
`extra="forbid"` es inofensivo. `Paquete` armado a mano desde los 6 fixtures valida.

---

## Preparación para la Fase 1 — `puente_f1.py` sobre los 6 casos

Carga los 4 maestros y los 6 casos usando **solo** funciones de Fase 0. Resultado:
`SIN FALLOS`. Ningún cruce falla.

| Caso | NIT solicitud → norm. | NIT cotización → norm. | Cruce RC1 | Descripción truncada (len) | Unidad |
|---|---|---|---|---|---|
| sol-001 | `900555111` | `900.555.111-2` → `900555111` | vía NIT → `100234`, activo | `Renovación licencias antivirus` (30) | `UN` |
| sol-002 | `901999000` | `901.999.000-5` → `901999000` | **NO CRUZA** → bloqueo RC1 ✓ | `Licencia anual herramienta de pruebas` (37) | `UN` |
| sol-003 | `830111222` | `830.111.222-8` → `830111222` | vía NIT → `100150`, activo | `Mobiliario para nueva sede: 40 puestos` (38) | `UN` |
| sol-004 | `901222333` | `901.222.333-1` → `901222333` | vía NIT → `100310`, activo | `Bolsa de 100 horas de arquitectura de` (37) | `H` |
| sol-005 | `800444555` | `800.444.555-3` → `800444555` | vía NIT → `100118`, activo | intacta, 35 ≤ 40 | `UN` |
| sol-006 | **ausente** → `None` | `900.555.111-2` → `900555111` | **vía nombre** `tecnosuministros` → `100234` | `Diademas con micrófono para la mesa de` (38) | `UN` |

- ADR-008 verificado: los 6 NIT de cotización (con puntos y dígito de verificación) cruzan
  contra el maestro una vez normalizados. Sin esto RC1 fallaría en los 6 casos.
- ADR-005 verificado: `sol-001` da `UN` pese a que su descripción dice *"vigencia 12 meses"*,
  y `sol-004` da `H`. Las 6 unidades coinciden con el oráculo.
- ADR-004 verificado: los 6 truncados ≤ 40, prefijo del original, cortados en frontera.
  Coinciden con la tabla informativa de T0.4.
- RC4 pasa en los 6 (la subárea pertenece al centro en todos).
- `sol-002` es el único que no cruza — exactamente el bloqueo RC1 del oráculo.
- Los derivados de `sol-006` están disponibles: `indicador_iva=None`, `condiciones_pago=None`
  en la solicitud, y el maestro de `100234` ofrece `C1` / `Z030`. Coincide con el oráculo.

**Conclusión: los cimientos sirven. RC1, RC4, I1 y la unidad son construibles hoy.**

---

## Los tres riesgos reportados por los ejecutores

**1 · Autorización de escritura solo en `ruta_salida` → CONFIRMADO como diseño, no como fuga.**

`ruta_salida` es sólido. Rechaza los 4 vectores, incluido uno que los tests no cubren:

```
('..','escape.txt')               RECHAZA
('../../etc/passwd',)             RECHAZA
('/etc/passwd',)                  RECHAZA
('sol-001','..','..','fuera.txt') RECHAZA
symlink dentro de out/ → afuera   RECHAZA  (el .resolve() lo atrapa)
```

Bonus: `limpiar_salida` maneja bien el symlink — borra el enlace, no el contenido del
destino. El testigo en el directorio apuntado sobrevive. La guarda `is_symlink()` funciona.

Pero sí es posible escribir fuera de `out/` **sin pasar por `ruta_salida`**, demostrado:
`escribir_texto(victima/x.txt, …)`, `escribir_json(…)` y `agregar_linea_jsonl(…)` escriben
en cualquier `Path`, incluida la raíz del proyecto (pisé un `pyproject.toml` de sandbox) y
un `fixtures/` escribible. La autorización es disciplina en el sitio de llamada, no un
control. Hoy nadie la salta: el único consumidor es `log.py`, y pasa por `ruta_salida`
(verificado: con `raiz=/tmp/x` el archivo cae en `/tmp/x/out/log.jsonl`). Ver D7.

**2 · `cargar_config` no lee `.env` → CONFIRMADO, y ya está morder.**

```
cwd con .env presente, variables NO exportadas:
  existe ./.env: True
  openai_api_key leída: ''
  cargar_config(requiere_llm=True) LANZA:
    "Falta OPENAI_API_KEY. Cópiala en .env siguiendo .env.example."
```

No hay `python-dotenv` ni declarado ni instalado. El repositorio **ya tiene** un `.env` con
una clave real de 164 caracteres que el backend no puede ver. `.env.example` promete cinco
variables que solo funcionan si se exportan al entorno, y el propio mensaje de error manda
al operador a un archivo que el código ignora. Ver D3. Impacto en Fase 4: directo — T4.6
`/api/health` y T4.2 no arrancarán por una causa que el mensaje de error atribuye mal.

**3 · `normalizar_nombre` elimina `sa`/`sas`/`ltda` en cualquier posición → RIESGO REAL,
IMPACTO CERO sobre el maestro actual.**

Los 5 proveedores normalizan sin colisión (5 valores únicos de 5), y el resultado es
**idéntico** al de una variante que solo quite el sufijo al final. Los 6
`proveedor_nombre` de las solicitudes también. El cruce de `sol-006` funciona.

Donde sí muerde, con nombres sintéticos:

```
'Sas Colombia'              -> 'colombia'
'Grupo SA Internacional'    -> 'grupo internacional'
'Ltda Express'              -> 'express'
'Sanitarios SA de Colombia' -> 'sanitarios de colombia'
'J. S. A. Ramírez e Hijos'  -> 'j ramirez e hijos'
'Sociedad Anonima Andina'   -> 'andina'
```

Veredicto: **no es defecto**, es una limitación documentable. Con 5 proveedores no hay
riesgo; con un maestro real de miles sí podría colisionar. Va a `SOLUCION.md` como supuesto
declarado, no a la lista de defectos.

---

## DEFECTOS

**D1 · `app/core/modelos.py:75-81` + `tests/test_modelos.py` · mayor**
`Aprobacion` repite el patrón de `Correo`: `extra="forbid"` con nombres de campo que
coinciden con las claves del `aprobacion.json` real, y cero tests contra el fixture.
Esperado: T0.6 §Casos de prueba — *"Los modelos de entrada deben validarse contra los 6
fixtures reales, no contra diccionarios inventados"*. `Aprobacion` está declarada bajo
`── Entrada ──` en la misma especificación.
Obtenido: 6 de 6 fixtures fallan en parseo directo y en la variante enriquecida. Solo pasa
la construcción campo por campo.
Reproducir: `PYTHONPATH=<raiz> uv run python /tmp/qa_f0/modelos_vs_fixtures.py` (bloque 4).
Debe cerrarse **antes de T2.3 `oc_leer_paquete`**. Igual que con `Correo`, lo correcto no
es necesariamente cambiar el `extra=`, sino añadir el test de regresión contra los 6
`aprobacion.json` que fije la forma de construcción. Extensible a `Cotizacion`, `Factura`
y `Paquete`, sin test propio.

**D2 · `app/core/config.py:20-28` · mayor (C7 latente)**
`Config` es un `BaseModel` normal con la clave en claro, así que la expone por cuatro vías:
```
repr(config)            -> clave presente: True
str(config)             -> clave presente: True
config.model_dump()     -> clave presente: True
config.model_dump_json()-> clave presente: True
repr: Config(openai_api_key='sk-CLAVE-FALSA-DE-PRUEBA-QA-0000', openai_model=…)
```
Esperado: C7 — *"Nunca en: el repositorio, el front Streamlit, los logs, `out/`, ni las
respuestas de la API"*. Obtenido: hoy nadie la expone (`resumen_publico` está limpio), pero
un `logging.info(config)`, un `return config.model_dump()` en `/api/health` o un volcado de
depuración la filtra sin que nada lo impida. CLAUDE.md lista C7 entre las reglas *"que se
violan con facilidad"*.
Reproducir: ver el bloque C7 del comando de sondeo; `pydantic.SecretStr` lo cierra en una línea.
Debe cerrarse **antes de T4.6**.

**D3 · `app/core/config.py:74` y `.env.example` · mayor**
El código no lee `.env` pero el mensaje de error y el `.env.example` prometen que sí.
Esperado: que el operador que sigue `.env.example` tenga un backend funcional.
Obtenido: `ValueError: Falta OPENAI_API_KEY. Cópiala en .env siguiendo .env.example.`
**con el `.env` ya en su sitio y con la clave correcta dentro.**
Reproducir: `cd /tmp/qa_f0 && uv run --project <raiz> python -c "from app.core.config import cargar_config; cargar_config()"`
con un `.env` en el cwd.
Tres salidas, todas válidas: agregar `python-dotenv` (exige ADR, porque T0.1 prohíbe
dependencias fuera de la lista), leer `.env` a mano en `cargar_config`, o cambiar el mensaje
y el `README` a `set -a; source .env`. Lo que no es válido es dejar el mensaje mintiendo.
Debe cerrarse **antes de Fase 4**.

**D4 · `app/core/io.py:48-64` · menor**
`leer_json` incumple su propio tipo de retorno `dict[str, Any] | list[Any] | None`:
```
archivo con  42      -> int
archivo con "hola"   -> str
archivo con  true    -> bool
```
La anotación de la línea 61 no valida en ejecución y `mypy` no lo atrapa porque
`json.loads` devuelve `Any` (es justo la deuda que ADR-009 acepta). Riesgo real: un `len()`
o un `x["clave"]` sobre ese retorno da `TypeError` en Fase 2. El camino realista está
protegido porque Pydantic rechaza un escalar, pero el contrato declarado es falso. Un
`isinstance` tras el `loads` lo cierra.

**D5 · `app/core/normalizar.py:90-91` · menor**
`truncar_descripcion` viola el invariante 6 de su propia especificación
(*"el resultado nunca excede `limite` ni termina en espacio"*) en el camino rápido:
```
'Papelería y tóner  '  -> 'Papelería y tóner  '   (termina en espacio)
' '                    -> ' '
```
La especificación se contradice: el paso 1 dice *"si cabe, devolver tal cual"* y el paso 6
prohíbe el espacio final. El código siguió el paso 1. Ningún fixture lo ejercita
(`sol-005`, la única descripción corta, no tiene espacio final), pero la fila I1 del
protocolo de QA exige *"no termina en espacio"* y el test I1 de Fase 3 fallaría ante un
Excel con espacio al final. Un `.rstrip()` en el camino rápido lo cierra.

**D6 · `tests/test_io.py:139-144` · menor (C1)**
`test_escribir_en_fixtures_falla` **intenta una escritura real dentro de `fixtures/`** y se
apoya solo en el `chmod a-w` del sistema de archivos. Hoy no pasa nada. Si alguien hace
`chmod -R u+w fixtures` o corre como root, el test crea `fixtures/no-debe-crearse.txt` y
viola C1 mientras pretende defenderlo. Verificar el modo del directorio (`stat().st_mode`)
afirma lo mismo sin tocar el disco.

**D7 · `app/core/io.py:74-90` · menor**
`escribir_json`, `escribir_texto` y `agregar_linea_jsonl` aceptan cualquier `Path`. T0.3
afirma que `ruta_salida` *"es el único punto del sistema que autoriza escritura, así que no
se puede omitir"* — y sí se puede: se demostró escritura en un directorio hermano, en la
raíz del proyecto y en un `fixtures/` escribible. Hoy inexplotado (único consumidor:
`log.py`, que sí pasa por `ruta_salida`). Las firmas vienen literales de la especificación,
así que el ejecutor cumplió. Cierre sugerido: validar dentro de los tres escritores, o un
test que falle si aparece un `write_text` fuera de `io.py`.

**D8 · `app/core/normalizar.py:43,64` · menor**
Con tipos no-`str` (lo que llega de un JSON crudo sin pasar por Pydantic):
`normalizar_nit(900555111)` → `TypeError`; `normalizar_nombre(900555111)` →
`AttributeError`. Peor: `normalizar_nit(["900555111"])` y `normalizar_nit({"nit":"900"})`
devuelven `None` **silenciosamente**, lo que haría que RC1 crea que no hay NIT y caiga al
cruce por nombre. Mitigado porque `mypy --strict` protege a todo llamador tipado y Pydantic
valida antes. Queda como nota de robustez para Fase 2.

**D9 · `app/core/io.py:55-71` · menor**
`leer_json` y `leer_texto` lanzan subclases de `OSError` que el contrato no menciona:
`IsADirectoryError` sobre un directorio, y `PermissionError` sobre un archivo sin permiso.
T0.3 solo promete `ValueError` para JSON malformado y `None` para inexistente. Una
herramienta de Fase 2 que capture solo `ValueError` deja escapar la excepción → violación
de C3. (`UnicodeDecodeError` sí queda cubierta: hereda de `ValueError`.)

**D10 · `app/core/modelos.py:13-14` · menor (documentación)**
El docstring del módulo dice *"Todos los modelos prohíben campos extra, salvo `Solicitud`"*
y omite `Correo`, que también es `extra="ignore"`. El comentario de la clase sí lo explica
bien. Es el tipo de inexactitud que se nota en una defensa técnica.

---

## COBERTURA — qué quedó sin verificar

**Fuera de alcance de la Fase 0, por construcción:** RC1–RC10 (no existen), las 6
herramientas y el contrato `{ok,…}` real, el adaptador SAP, `demo.py`, el ciclo del agente,
la API y el front. Los invariantes I2 (numeración), I3 (idempotencia), I6 (una fila por
intento en una corrida real), I7 (trazabilidad), I8 (fuzzing de herramientas) e I10
(determinismo de `demo.py`) no son verificables todavía. De I1 solo se validó la regla de
truncado; de I4, I5 e I9 solo la parte que vive en el modelo Pydantic.

**Verificable hoy y no cubierto por los tests:**
- **I5 no lo valida el modelo.** `PosicionOC(numero=7)` y `PosicionOC(numero=-1)` pasan;
  `cantidad=0` y `precio_unitario=-5` también. El invariante *"10, 20, 30…"* queda
  íntegramente a cargo del constructor de payload de T2.6, sin red de seguridad en el tipo.
  La tabla de trazabilidad de T0.6 lo lista como requisito de esa tarea, pero el modelo que
  la propia especificación dicta es solo `numero: int`. Hay que cubrirlo en Fase 2.
- `Veredicto`, `ResultadoValidacion` y `ExcepcionOC`: sin un solo test. `ExcepcionOC` nunca
  se ejercita con contenido, aunque es la estructura que el oráculo exige para `sol-004`.
- `Solicitud.moneda` y `Cotizacion.moneda` son `str` libre. I9 solo se aplica en
  `OrdenCompra.moneda`, que es `Literal["COP","USD"]` — correcto, pero el dato de entrada
  no se valida contra I9 en la frontera.
- `escribir_json` con un objeto no serializable lanza `TypeError`; `agregar_linea_jsonl`
  sobre un padre inexistente lanza `FileNotFoundError`. Sin test, y sin contrato declarado.
- Los casos de borde del oráculo (proveedor `901777888` inactivo, sin cotización, sin
  aprobación, `valor_total` no numérico) solo podrán verificarse en Fase 1 y Fase 2.
- La auditoría con el candidato que exige ADR-001 queda en `specs/qa/auditoria-f0.md`.
