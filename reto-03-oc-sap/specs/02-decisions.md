# Decisiones de arquitectura (ADR)

> Toda desviación de la constitución o del PRD requiere una ADR aquí.
> Las ADR marcadas **[SOLUCION.md]** deben aparecer en el entregable documental.

---

## ADR-001 · Python en lugar de TypeScript **[SOLUCION.md]**

**Estado:** aceptada · decisión explícita del candidato
**Contexto:** el PRD exige TypeScript en seis lugares (§0 Lenguaje, §6.2 contrato de
herramientas, §6.5 estructura, §6.6 `demo.ts`, §8 `zod` obligatorio y *"TypeScript sin
`any`"*, §9.4 bonus `tools/oc.ts`).

**Decisión:** se entrega en Python 3.12.

**Razón:** el PRD también impone *"Debes poder explicar cada línea que entregas"* y la
aprobación depende de una defensa técnica en vivo. El candidato puede auditar, razonar
y defender Python; TypeScript introduciría el riesgo de entregar código correcto que no
puede sustentar. Entre incumplir el lenguaje y arriesgar la defensa, se prioriza la
defensa.

**Costo asumido:** pérdida de los puntos atados literalmente a `zod`, `demo.ts`,
`src/tools/oc.ts` y *"TypeScript sin `any`"*. No es cuantificable porque **el PRD no
incluye la rúbrica que promete en §0** (la §10 es "Riesgos, supuestos y preguntas
abiertas", no una rúbrica).

**Mitigación:** se respeta el 100% del contrato semántico — nombres de herramientas
`oc_*`, forma `{ok, data}` / `{ok, error}`, reglas CA1–CA5, controles RC1–RC10,
separación comportamiento/conocimiento/ejecución — y se declara la desviación de forma
abierta en `SOLUCION.md` con esta tabla de equivalencias:

| PRD | Entregado | Equivalencia |
|---|---|---|
| `zod` | Pydantic v2 | Validación declarativa + JSON Schema para tool-calling |
| `demo.ts` | `demo.py` | Mismo contrato, sin LLM |
| `TypeScript sin any` | `mypy --strict` | Tipado estricto verificable en CI |

**Alternativa descartada:** entregar en TypeScript con un `WALKTHROUGH.md` de apoyo.
Descartada por el riesgo en la defensa oral.

---

## ADR-002 · Sin LangGraph ni LangChain **[SOLUCION.md]**

**Estado:** aceptada
**Contexto:** la intuición inicial era que el reto requería un grafo de estados
complejo. El análisis del flujo real lo desmiente.

**Decisión:** ciclo del agente escrito a mano (~80 líneas) sobre el SDK `openai`
directo. Sin framework de orquestación.

**Razón:**

1. **El PRD pide un adaptador propio.** §6.1: *"Una interfaz propia
   (`enviar(mensajes, herramientas) → respuesta`)... Cambiar de proveedor no debe tocar
   el ciclo del agente."* LangChain envolvería el cliente de OpenAI en su abstracción,
   que habría que envolver otra vez para cumplir el requisito: dos capas donde el PRD
   pide una.
2. **CA1 exige un tope de iteraciones visible.** En LangGraph vive dentro del runtime;
   a mano son tres líneas señalables en la defensa. El PRD califica la comprensión del
   ciclo, y un framework la esconde.
3. **Lo que LangGraph aporta, aquí no se usa.** Checkpointing y persistencia: el PRD
   dice *"Persistencia: No... Sin base de datos"*. Interrupts de human-in-the-loop:
   CA3 es más simple, cada request HTTP **es** un turno, así que "terminar y esperar"
   es el comportamiento natural de un servidor. Branching multi-agente: hay un solo
   agente.
4. **El flujo es lineal con dos compuertas**, y las compuertas viven dentro de
   `oc_validar` (datos), no en la orquestación:
   `leer → validar → [bloqueo? fin] → evidencia → payload → [confirmación? fin de turno] → crear`

**Consecuencia:** cinco dependencias en total (`openai`, `pydantic`, `fastapi`,
`uvicorn`, `streamlit`). §8 exige justificar dependencias; una lista corta se defiende
sola.

**Alternativa descartada:** LangGraph con `interrupt_before` en el nodo de creación.
Técnicamente válido y más vistoso, pero añade una dependencia grande para esconder
justo el mecanismo que se evalúa.

---

## ADR-003 · RC3 se evalúa solo si RC2 pasó

**Estado:** aceptada
**Contexto:** `sol-003` dispara RC2 y RC3 simultáneamente. Aprueba `fvargas@`, que es
aprobador de CC-3030, pero el centro de la solicitud es CC-2020. Además, `74.000.000`
excede el tope de `30.000.000` del único aprobador real de CC-2020 (`rtorres@`).

**Decisión:** RC2 bloquea. RC3 se reporta como `no_evaluable`, no como bloqueo.

**Razón:** RC3 dice textualmente *"`valor_total` ≤ `tope` **del aprobador** para ese
centro"*. Si el aprobador no pertenece al centro, no existe un tope aplicable: evaluarlo
contra el tope de un tercero que no aprobó sería inventar un control. Reportar dos
bloqueos donde hay una sola causa raíz también confunde al usuario sobre qué corregir.

**Nota de negocio:** si el centro hubiera sido CC-3030, `74M ≤ 80M` habría pasado. El
defecto es de **autoridad**, no de monto. Va al análisis de desvío de proceso de
`SOLUCION.md`.

**Excepción al criterio general:** los demás bloqueos **no** hacen corto-circuito; se
reportan todos juntos para que el usuario corrija en una sola pasada.

---

## ADR-004 · Truncado de `descripcion` verificado por regla, no por literal

**Estado:** aceptada
**Contexto:** el payload limita `descripcion` a 40 caracteres (texto breve de SAP).
**Las 6 descripciones de los fixtures exceden el límite** (sol-001 mide 73).

**Decisión:** truncar en frontera de palabra sin exceder 40. La descripción completa se
preserva en `out/<caso>/trazabilidad.json`. Los tests **no** afirman strings literales;
afirman las propiedades: `len ≤ 40`, es prefijo de la original en frontera de palabra,
y no termina en espacio.

**Razón:** escribir a mano los 6 strings esperados introduce errores de conteo humano y
vuelve los tests frágiles ante cualquier cambio del algoritmo. Verificar la regla es más
robusto y expresa mejor la intención.

---

## ADR-005 · Inferencia de `unidad` por sustantivo núcleo del ítem

**Estado:** aceptada
**Contexto:** el payload exige `unidad ∈ {"UN", "H", "MES"}` y **ningún fixture la
trae**. Hay que inferirla.

**Decisión:** se inspecciona el nombre del ítem en `cotizacion.txt` (el texto antes
del primer `|`) y se usa su sustantivo núcleo. `hora`/`horas` → `H`; `mes`/`meses`
como unidad contada → `MES`; en cualquier otro caso → `UN` (default seguro).

**Asimetría deliberada entre las dos rutas** (hallazgo del ejecutor de T0.4):

| Fuente | Regla | Por qué |
|---|---|---|
| `item_cotizacion` | solo la **primera palabra** (el núcleo) | el ítem es un sustantivo con núcleo fiable: `licencia`, `hora`, `puesto`, `diadema` |
| `descripcion` (fallback) | buscar la palabra en **todo** el texto | una descripción libre no tiene núcleo fiable: *"Bolsa de 100 horas…"* empieza por `bolsa` pero la unidad correcta es `H` |

No existe una sola regla que satisfaga ambas rutas: aplicar "núcleo" a la descripción
rompe el caso de la bolsa de horas, y aplicar "contiene" al ítem rompe `sol-001`
(*"Licencia antivirus corporativo 12 meses"* daría `MES` en lugar de `UN`).
La asimetría va comentada en el código.

**Razón:** una búsqueda ingenua de palabras clave falla en `sol-001`, cuya descripción
dice *"vigencia 12 meses"* pero cuya cantidad son 120 licencias → `UN`, no `MES`. El
ítem de la cotización sí es inequívoco: *"Licencia antivirus corporativo 12 meses |
Cantidad: 120"* — el ítem **es** una licencia. En `sol-004`, *"Hora de arquitecto de
nube senior | Cantidad: 100"* — el ítem **es** una hora → `H`.

**Supuesto declarado en `SOLUCION.md`:** en producción la unidad debería venir del
maestro de materiales o del Excel de solicitud, no inferirse del texto.

---

## ADR-006 · `confirmado_por` = identificador de sesión

**Estado:** aceptada
**Contexto:** `excepciones[].confirmado_por` no está definido en el PRD y no hay
autenticación (§3.2: *"No-objetivos: autenticación de usuarios, roles o multiusuario"*).

**Decisión:** se registra el `session_id` del chat.

**Razón:** es la única identidad que existe en el sistema. Inventar un usuario sería
fabricar evidencia de auditoría, justo lo contrario de lo que persigue el control.
En `SOLUCION.md` se señala que en producción debe ser la identidad corporativa (SSO)
de quien confirma, porque es evidencia de auditoría.

---

## ADR-007 · `valor_unitario` se registra con IVA incluido

**Estado:** aceptada
**Contexto:** las cotizaciones dicen *"Precio unitario (IVA incl.)"* y desglosan base
gravable aparte. SAP normalmente recibe la base gravable y calcula el impuesto desde el
`indicador_iva`.

**Decisión:** el payload lleva el `valor_unitario` de la solicitud tal cual (IVA
incluido), que es el valor que el líder aprobó y contra el que corren RC3, RC5 y RC10.

**Razón:** cambiar la base de cálculo rompería la coherencia con el monto aprobado y con
los controles. Los tres controles deben operar sobre la misma magnitud.

**Deuda declarada en `SOLUCION.md`:** una OC real debe llevar base gravable + indicador
de impuesto. El adaptador SAP real necesita un paso de conversión usando
`indicadores-iva.json`, y hay que decidir con contabilidad qué magnitud gobierna el tope
de aprobación.

---

## ADR-008 · Normalización de NIT

**Estado:** aceptada
**Contexto:** los NIT aparecen en tres formatos. Cotización: `900.555.111-2`.
Solicitud: `900555111`. Maestro: `900555111`.

**Decisión:** función `normalizar_nit()` que elimina puntos, espacios y guiones, y
**descarta el dígito de verificación** cuando viene tras un guion. El cruce de RC1 se
hace siempre sobre la forma normalizada.

**Razón:** sin esto, RC1 falla contra la cotización en los 6 casos. Es trabajo real del
control, no un detalle cosmético.

**Riesgo:** un NIT de 10 dígitos sin guion podría confundirse con uno de 9 + dígito de
verificación. Mitigación: solo se descarta el dígito cuando hay separador explícito.

---

## ADR-009 · `disallow_any_explicit` desactivado en mypy **[SOLUCION.md]**

**Estado:** aceptada
**Contexto:** `pyproject.toml` arrancó con `disallow_any_explicit = true` como
equivalente estricto de *"TypeScript sin `any`"* (ADR-001). Resultó incompatible con
Pydantic v2, y el hallazgo es más profundo de lo que parecía: no era solo que las
firmas de `leer_json` usaran `Any`.

**Causa raíz** (aislada con repros mínimos por el ejecutor de T0.6): `pydantic/main.py`
declara `__pydantic_extra__: Dict[str, Any] | None` como campo de `dataclass_transform`.
mypy propaga ese `Any` heredado al `__init__` sintetizado de **toda** subclase y lo
reporta en la línea del `class`. Es decir, con la opción activa **cualquier modelo
Pydantic falla**, incluso `class A(BaseModel): pass`. Se verificó que no es evitable
con `ConfigDict`, `ClassVar[ConfigDict]`, `class X(BaseModel, extra="forbid")` ni un
modelo sin cuerpo.

**Decisión:** desactivar `disallow_any_explicit` globalmente y conservar
`strict = true` más `warn_unreachable = true`.

**Razón:** `strict` ya prohíbe el `Any` **implícito**, que es el peligroso — el que
entra sin que nadie lo escriba. El `Any` explícito que queda vive únicamente en la
frontera de deserialización de JSON (`leer_json`, los registros del log), donde la
forma del dato se desconoce por definición hasta que Pydantic la valida. Prohibirlo ahí
obligaría a un alias recursivo de tipo JSON que añade narrowing en cada consumidor sin
ganar seguridad real, porque la validación la hace Pydantic, no el verificador de tipos.

**Alternativa descartada:** override por módulo (`[[tool.mypy.overrides]]` para
`app.core.modelos`). Descartada porque el problema no es de un módulo: afectaría a toda
tarea futura que defina modelos Pydantic, es decir las herramientas de la Fase 2 y el
payload. Una supresión global explícita y documentada es más honesta que una lista de
excepciones que crece.

**Deuda aceptada:** un `Any` explícito mal puesto en un módulo de modelos ya no lo
atrapa mypy. Mitigación: la revisión de QA incluye `grep -n "Any" app/` y verifica que
cada aparición esté en una frontera de JSON.

**Nota de proceso:** este es el tipo de contradicción que el flujo SDD está diseñado
para exponer. La especificación era imposible de cumplir, y el ejecutor la reportó en
lugar de silenciarla o inventar un arreglo. Vale para `SOLUCION.md` §9.1.8.

---

## ADR-010 · Lectura de `.env` a mano, sin `python-dotenv`

**Estado:** aceptada
**Contexto:** defecto **D3** del informe de QA de la Fase 0. `cargar_config` leía solo
`os.environ`, pero su mensaje de error decía *"Cópiala en `.env` siguiendo
`.env.example`"*. El QA lo demostró mordiendo ya: con un `.env` válido en su sitio y la
clave correcta dentro, `cargar_config(requiere_llm=True)` lanzaba el error diciendo que
faltaba. El mensaje mandaba al operador a un archivo que el código ignoraba.

**Decisión:** leer el `.env` dentro de `cargar_config`, en unas 12 líneas, sin agregar
dependencias. Las variables ya presentes en `os.environ` **tienen precedencia** sobre el
archivo.

**Razón:**

1. `pyproject.toml` fija una lista corta de cinco dependencias que el PRD §8 obliga a
   justificar. Añadir `python-dotenv` por doce líneas de parseo trivial debilita ese
   argumento en la defensa.
2. La precedencia del entorno real sobre el archivo es lo que hace que el mismo código
   sirva en local (donde manda el `.env`) y en el despliegue (donde la plataforma
   inyecta variables y el `.env` no existe). Es el comportamiento que uno quiere, y
   escribirlo a mano lo vuelve explícito en lugar de delegarlo a los defaults de una
   librería.
3. El parseo necesario es mínimo: líneas `CLAVE=valor`, ignorar vacías y comentarios,
   recortar espacios, quitar comillas envolventes. Una línea malformada se ignora sin
   lanzar.

**Alternativas descartadas:**

- **`uvicorn --env-file .env`**: resuelve el servidor pero no `demo.py` ni los scripts,
  y deja el mensaje de error igual de engañoso para quien no arranque por uvicorn.
- **Cambiar el mensaje a `set -a; source .env`**: honesto, pero traslada al operador un
  paso que el código puede hacer, y el PRD §8 pide que un comando levante todo.

**Deuda:** este lector no cubre valores multilínea ni interpolación de variables, que
`python-dotemv` sí. Ningún valor de `.env.example` los necesita. Si algún día se
necesitan, la decisión se revisa.
