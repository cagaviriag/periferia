# Auditoría de la Fase 0 con el candidato

> Insumo de la defensa técnica ante Periferia (exigido por ADR-001). Lenguaje llano:
> qué hace cada módulo, por qué existe y qué regla del PRD cumple.
> Si no puedes explicar una de estas piezas en voz alta, vuelve aquí antes de la sesión.

## La idea de la fase en una frase

La Fase 0 no resuelve ninguna regla de negocio. Resuelve el problema anterior: **los datos
de los fixtures no están en la forma que necesita SAP**, y hay cuatro garantías que todo el
resto del sistema va a dar por sentadas (no escribir donde no se debe, no filtrar la clave,
dejar rastro de todo, y tipos que no mienten). Si estos cimientos fallan, los 10 controles
de la Fase 1 fallan por una razón tonta que no tiene nada que ver con el negocio.

---

## `app/core/config.py` — de dónde salen las credenciales y los topes

**Qué hace.** Lee cinco variables de entorno (clave de OpenAI, modelo, tope de iteraciones,
tope de tokens por sesión, timeout) y las deja en un objeto `Config`. Calcula también la
raíz del proyecto a partir de su propia ubicación en disco, nunca con una ruta escrita a
mano, para que el proyecto se pueda mover de carpeta sin tocar código.

**Por qué existe.** Para que haya **un solo** punto en todo el sistema que toque el entorno.
Si mañana hay que cambiar de proveedor de LLM o subir el tope de iteraciones, se cambia
aquí y en ningún otro lado.

**Qué regla cumple.** C7 (la clave solo vive como variable de entorno del backend) y C6 /
CA1 (los topes de iteraciones y de costo son configurables, no constantes enterradas en el
código).

**Las dos decisiones defendibles.**
1. Es **la única función del proyecto autorizada a lanzar una excepción**. Puede, porque
   corre al arrancar el proceso, no dentro de una herramienta. Si falta la clave quieres
   enterarte al segundo cero, no a mitad de una conversación.
2. `requiere_llm=False` existe para que `demo.py` y los tests corran **sin ninguna clave**.
   El PRD §6.6 lo exige textualmente, y es la razón por la que el plan pone `demo.py` antes
   del LLM: hay un entregable verificable sin gastar un token.
3. `resumen_publico()` es la única forma autorizada de contar algo sobre la configuración
   hacia afuera. Devuelve proveedor, modelo y tope de iteraciones. **Nunca la clave.** Eso
   es lo que va a consumir `/api/health` en la Fase 4.

**Lo que hay que decir si preguntan.** El módulo **no lee el archivo `.env`** — solo lee
variables ya exportadas en el entorno. Es una brecha conocida y está en el informe de QA
como defecto D3, porque el mensaje de error le dice al operador que copie la clave en
`.env`, y eso hoy no basta. Hay que resolverlo antes de la Fase 4.

---

## `app/core/io.py` — la puerta única de lectura y escritura

**Qué hace.** Todo lo que el sistema lee de `fixtures/` y todo lo que escribe en `out/` pasa
por aquí. Construye rutas, lee JSON y texto, escribe JSON y texto, agrega líneas a un
archivo `.jsonl`, y vacía `out/` al inicio de una corrida.

**Por qué existe.** Dos razones. La primera es la garantía de que **nada se escribe fuera de
`out/`**: `ruta_salida()` es el portero. Toma los pedazos de la ruta, los resuelve, y
comprueba que el resultado siga estando dentro de `out/`. Si no, lanza `RutaInvalidaError`.
Eso bloquea los tres vectores clásicos: `..`, `..` anidado y rutas absolutas. QA verificó
además un cuarto que los tests no cubrían — un enlace simbólico dentro de `out/` apuntando
afuera — y también lo rechaza, porque el `.resolve()` lo desenmascara antes de comparar.
La segunda razón es el determinismo: `limpiar_salida()` vacía `out/` sin borrar la carpeta y
sin usar `rmtree` sobre la raíz del proyecto.

**Qué regla cumple.** C1 (`fixtures/` es inmutable, toda escritura va a `out/`), C9
(`out/` se limpia al inicio para que dos corridas den lo mismo) y el requisito de seguridad
del PRD §8: este módulo **no ejecuta ni un comando de shell**, solo primitivas de `pathlib`
y `json`.

**La decisión defendible.** `leer_json` **sí lanza** cuando el JSON está malformado, y el
mensaje incluye el nombre del archivo. Eso parece contradecir C3 ("nada lanza") y no lo
hace: la regla C3 aplica a las **herramientas**, que son la frontera con el agente. Aquí
abajo es mejor fallar fuerte y que la herramienta de la Fase 2 traduzca eso a
`{"ok": false, "error": "…"}` con una sugerencia de qué pedirle al solicitante (HU-6).
Repartir el `try/except` por todos lados escondería el origen del problema.

**Lo que hay que decir si preguntan.** Los tres escritores (`escribir_json`,
`escribir_texto`, `agregar_linea_jsonl`) aceptan cualquier ruta: la autorización vive solo
en `ruta_salida`, así que es disciplina del llamador, no un control. Hoy nadie la salta,
pero es una puerta que conviene cerrar (defecto D7).

---

## `app/core/normalizar.py` — las cuatro funciones que hacen que RC1 funcione

**Qué hace.** Cuatro funciones puras: reciben texto, devuelven texto. Sin leer archivos, sin
estado global, sin excepciones.

1. **`normalizar_nit`** — el NIT aparece en tres formatos distintos: `900.555.111-2` en la
   cotización, `900555111` en la solicitud y en el maestro. Quita puntos, espacios y
   guiones, y descarta el dígito de verificación **solo cuando viene detrás de un guion**.
2. **`normalizar_nombre`** — pasa a minúsculas, quita acentos, quita puntuación, colapsa
   espacios y elimina los sufijos societarios (`S.A.S.`, `S.A.`, `Ltda`).
3. **`truncar_descripcion`** — recorta a 40 caracteres sin partir una palabra por la mitad.
4. **`inferir_unidad`** — decide si la unidad de SAP es `UN`, `H` o `MES`.

**Por qué existe cada una.**
- Sin `normalizar_nit`, **RC1 falla en los 6 casos**: el NIT de la cotización nunca cruza
  contra el maestro. No es cosmética, es trabajo del control (ADR-008).
- Sin `normalizar_nombre`, **`sol-006` no se puede resolver**: no trae NIT, así que el único
  camino al proveedor es el nombre, y `"TecnoSuministros S.A.S."` tiene que coincidir con lo
  que diga `proveedores.json`. QA lo verificó leyendo el fixture, no de memoria.
- Sin `truncar_descripcion`, el payload es inválido: SAP limita ese campo a 40 caracteres y
  **5 de las 6 descripciones lo exceden** (la de `sol-001` mide 73). La descripción completa
  no se pierde: va a `trazabilidad.json` (ADR-004).
- Sin `inferir_unidad`, no hay unidad: **ningún fixture la trae** y el payload la exige
  (ADR-005).

**Qué regla cumple.** PRD §7.3 (RC1 cruza por NIT, o por nombre si falta el NIT), PRD §7.4
e invariante I1 (el límite de 40 caracteres), y C9 (son funciones puras, así que ninguna
regla de control depende del azar ni del LLM).

**La decisión que más vale explicar: la asimetría de `inferir_unidad` (ADR-005).**
Las dos rutas usan reglas distintas **a propósito**. Si hay ítem de cotización, se mira
**solo la primera palabra**, porque el ítem es un sustantivo con núcleo fiable. Si no hay
ítem, se busca la palabra de unidad **en todo** el texto de la descripción. El caso que lo
decide es `sol-001`: la cotización dice *"Licencia antivirus corporativo 12 meses"*. Una
búsqueda ingenua de "meses" daría `MES`, y estaría mal: son **120 licencias**, no 120 meses;
"12 meses" es la vigencia, no la unidad contada. El ítem **es** una licencia → `UN`. Pero la
regla inversa también falla: la descripción de `sol-004` es *"Bolsa de 100 horas de
arquitectura"*, empieza por "bolsa" y su unidad correcta es `H`. **No existe una sola regla
que satisfaga las dos rutas**, y por eso la asimetría va comentada en el código.
En producción esto debería venir del maestro de materiales, no inferirse del texto: es un
supuesto declarado, no una pretensión de que esté bien resuelto.

**Lo que hay que decir si preguntan.** Los sufijos societarios se eliminan como palabra
suelta en **cualquier** posición, no solo al final. Con los 5 proveedores de este maestro da
exactamente el mismo resultado que quitarlos solo al final, y no hay ninguna colisión — QA
lo comprobó ejecutándolo. Con un maestro real de miles de proveedores podría morder:
`"Grupo SA Internacional"` queda como `"grupo internacional"`. Es una limitación conocida,
no un defecto de esta fase.

---

## `app/core/log.py` — el rastro que pide auditoría

**Qué hace.** Dos registros distintos, para dos lectores distintos.
- `registrar_herramienta()` escribe una línea en `out/log.jsonl` por cada llamada a
  herramienta: cuándo, cuál, qué caso, si salió bien y un resumen corto. Es la bitácora
  técnica.
- `registrar_control()` agrega una fila a `out/control.csv` por cada **intento** de orden de
  compra, sea exitoso, bloqueado o pendiente de confirmación. Es el log que consume el área
  de auditoría.

**Por qué existe.** Porque el PRD lo exige por separado: CA4 pide la bitácora de llamadas, y
HU-5 pide el log de control con **columnas textuales**:
`solicitud_id, resultado, numero_oc, retroactiva, bloqueos, confirmaciones, ts`. El
encabezado está escrito literal en el código y hay un test que compara la primera línea del
archivo carácter por carácter contra esa cadena.

**Qué regla cumple.** C8 (trazabilidad total), invariante I6 (una fila **por intento**, no
una por orden creada), y C3.

**Las dos decisiones defendibles.**
1. **Ninguna de las dos funciones lanza nunca.** Están envueltas en `try/except` que se
   traga el fallo. La justificación es que la bitácora es observabilidad, no parte del
   resultado: si el log falla, se pierde esa línea pero la herramienta responde igual. Un
   fallo de escritura no puede tumbar una orden de compra. QA lo verificó con raíces
   imposibles (`/`, `/dev/null`, `/etc/passwd`): ninguna propagó.
2. **Se usa el módulo `csv` de la librería estándar, no `",".join(...)`.** Porque el detalle
   de un bloqueo puede contener comas — por ejemplo `"RC5: 25.000.000, no 26.500.000"` — y
   eso rompería el conteo de columnas al releer el archivo. Hay un test dedicado a esto y es
   el que mejor explica la decisión: comprueba que al releer con `csv.reader` la fila sigue
   teniendo exactamente 7 columnas.

**Lo que hay que decir si preguntan.** Es el único lugar del proyecto con una fuente de
no-determinismo (`datetime.now`), y solo alimenta el campo `ts`. C9 e I10 exceptúan
explícitamente los timestamps, y la comprobación de determinismo de la Fase 3 compara dos
corridas ignorando ese campo.

---

## `app/core/modelos.py` — los tipos del dominio, en lugar de `zod`

**Qué hace.** Define 20 clases Pydantic en tres grupos: lo que entra (`Solicitud`, `Correo`,
`Cotizacion`, `Aprobacion`, `Factura`, `Paquete`), los maestros (`Proveedor`, `CentroCosto`,
`IndicadorIva`, `CondicionPago`) y lo que sale (`OrdenCompra` y sus partes, más
`ResultadoValidacion`). Y dos funciones de una línea, `ok()` y `error()`.

**Por qué existe.** Es **la pieza que sustituye a `zod`** del PRD (ADR-001). Pydantic hace
dos trabajos a la vez: valida los datos en tiempo de ejecución y genera el JSON Schema que
el tool-calling del modelo necesita para saber qué argumentos aceptan las herramientas.

**Qué regla cumple.** PRD §7.4 (el payload de la OC validado con esquema), HU-3,
invariantes I1 (el campo `descripcion` tiene un máximo declarado de 40 caracteres, así que
una posición inválida se rechaza sola), I4 (`sociedad` y `organizacion_compras` son
constantes `"1000"` por defecto) e I9 (`moneda` solo admite `COP` o `USD`).

**Las tres decisiones defendibles.**
1. **`ok()` y `error()` devuelven un *string*, no un diccionario.** El PRD §6.2 exige que
   una herramienta entregue JSON serializado. Devolver un dict sería un defecto. Usan
   `ensure_ascii=False` para que `"Papelería"` se lea tal cual en el log y en el chat, en
   lugar de salir como `Paperíería`.
2. **El dinero es `int`, nunca `float`.** Son pesos colombianos sin decimales, y los flotantes
   acumulan error. El único `float` de todo el dominio es la *tasa* de IVA, que no es dinero.
3. **Las fechas se quedan como texto.** No se convierten a `datetime` aquí: comparar fechas
   es trabajo de los controles RC8 y RC9, no de los tipos.

**El hallazgo que hay que saber contar.** Casi todos los modelos están en `extra="forbid"`:
rechazan cualquier campo que no declaren. Es la postura estricta correcta... salvo cuando el
modelo es una **vista parcial** de un fixture más grande. `correo.json` trae ocho campos
(`para`, `cuerpo`, `adjuntos`, `nota_fixture`…) y el modelo declara los cuatro que llegan al
paquete normalizado del PRD §7.2. Con `extra="forbid"` el parseo de los 6 fixtures reales
fallaba, y el fallo habría aparecido recién en la Fase 2. Se corrigió a `extra="ignore"` con
un test de regresión contra los 6 `correo.json`.

**QA encontró que `Aprobacion` tiene exactamente el mismo problema latente** (defecto D1).
El `aprobacion.json` real trae `de, para, cc, fecha, asunto, cuerpo`, y el modelo declara
`de, fecha, aprobado, texto`. Los dos primeros nombres coinciden con las claves del fixture,
lo que invita a parsearlo directo — y con `extra="forbid"` eso falla en los 6 casos. Funciona
si se construye campo por campo, que es la intención del diseño (`aprobado` y `texto` son
valores *derivados*: `aprobado` sale de buscar la palabra "Aprobado" en el cuerpo). Falta el
test que fije esa forma de construcción antes de empezar `oc_leer_paquete`.

**La lección de proceso que vale para `SOLUCION.md`.** `disallow_any_explicit` se desactivó
(ADR-009) porque resultó **imposible de cumplir con Pydantic**: la clase base de la librería
declara un `Any` que mypy propaga al constructor de toda subclase, así que incluso un modelo
vacío falla. El ejecutor aisló la causa con repros mínimos y lo reportó en lugar de
silenciarlo o inventar un parche. `strict = true` sigue prohibiendo el `Any` **implícito**,
que es el peligroso. QA verificó la mitigación acordada: el único `Any` explícito que queda
está en `io.py`, en la frontera donde el JSON todavía no tiene esquema.

---

## Lo que esta fase deja listo, demostrado ejecutando

QA corrió un script que carga los 4 maestros y los 6 casos usando **solo** funciones de la
Fase 0, y confirmó que ya se puede: cruzar el proveedor por NIT normalizado en 5 casos y por
nombre en `sol-006`; detectar que `sol-002` **no existe** en el maestro (el bloqueo RC1 del
oráculo); comprobar que la subárea pertenece al centro de costo en los 6; truncar las 6
descripciones dentro del límite de SAP; e inferir la unidad correcta en los 6, incluido el
`UN` de `sol-001` y el `H` de `sol-004`. Los cimientos sirven: RC1, RC4, I1 y la unidad son
construibles hoy.

**Las tres cosas honestas que hay que mencionar sin que las pregunten:** el `.env` no se lee
todavía, la `Config` expone la clave si alguien la imprime, y falta el test de `Aprobacion`
contra su fixture. Las tres están en `specs/qa/informe-f0.md` con su severidad y su plazo.
