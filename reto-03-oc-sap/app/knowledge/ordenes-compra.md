Este es el conocimiento del proceso de compras de Periferia IT Group. Explica **por qué**
existe cada control, para que puedas decirle a la persona qué significa un bloqueo y qué
hacer con él, en lugar de solo leerle un código.

Las **cifras concretas de un caso** (montos, topes, fechas, porcentajes) salen siempre de
las herramientas, nunca de este documento. Aquí está la regla; allá está el dato.

---

## El proceso tal como ocurre hoy

Una compra nace en un correo. El solicitante manda a Compras un paquete con cuatro
piezas, y casi nunca llegan las cuatro completas:

| Pieza | Qué es | Qué aporta |
|---|---|---|
| **Correo** | El hilo donde se pidió la compra | La referencia del caso y quién lo pidió |
| **Solicitud** | El formato con el detalle | Proveedor, descripción, cantidad, valor, centro de costo, subárea |
| **Cotización** | El documento del proveedor | El precio que el proveedor sostiene, con IVA incluido |
| **Aprobación** | El correo del líder del área | Quién autorizó, cuándo y por cuánto |

Con eso, un analista hacía a mano lo que ahora hace el agente: verificar el proveedor en
el maestro de SAP, comprobar que quien aprobó podía aprobar ese monto, cuadrar el valor
de la solicitud contra la cotización, completar los códigos que el solicitante dejó en
blanco, dejar la evidencia de la aprobación archivada y crear la orden.

El proceso toma horas y los errores que produce son caros: una OC con el indicador de IVA
equivocado se descubre en la factura, y una compra aprobada por quien no tenía tope se
descubre en auditoría.

---

## Bloqueo y confirmación no son lo mismo

Es la distinción más importante del proceso, y la que la persona necesita entender:

- **Bloqueo**: la orden **no se puede crear**. Falta información o la información
  contradice una política de la compañía. Confirmar no lo resuelve; hay que corregir el
  dato o conseguir el documento. Son RC1, RC2, RC3, RC4 y RC10.
- **Confirmación**: la orden **se puede crear**, pero alguien tiene que asumir una
  decisión que el sistema no puede tomar solo. Queda registrada como excepción dentro de
  la OC, con el nombre de quien confirmó. Son RC5, RC6, RC8 y RC9.
- **Derivado**: un dato que faltaba y el sistema completó desde el maestro del proveedor.
  Se informa siempre. Es RC7.

Una excepción confirmada no desaparece: viaja dentro de la orden de compra y queda en el
archivo de control. Es, literalmente, la firma de quien dijo "procede".

---

## Los diez controles

### RC1 · El proveedor debe existir y estar activo — **bloqueo**

Se busca en el maestro de proveedores por NIT, y si la solicitud no trae NIT, por nombre
normalizado. Si no está, o está inactivo, no hay código SAP con el que crear la orden.

*Por qué bloquea:* una OC se emite contra un proveedor registrado, con sus datos
tributarios y bancarios validados. Un proveedor que no está en el maestro no está
contratado: crear la orden sería comprometer a la compañía con un tercero sin verificar.
*Qué hacer:* pedir a Compras que radique la creación del proveedor en SAP, o corregir el
NIT si fue un error de digitación.

### RC2 · La aprobación debe existir, decir "Aprobado" y venir de un aprobador del centro de costo — **bloqueo**

El correo de aprobación tiene que estar en el paquete, su cuerpo tiene que aprobar de
forma explícita, y quien lo firma tiene que figurar como aprobador de ese centro de costo
en el maestro.

*Por qué bloquea:* es el control de autoridad. Un visto bueno de alguien que no responde
por ese presupuesto no es una aprobación, es una opinión.
*Qué hacer:* pedir al solicitante el correo de aprobación del líder correcto del centro
de costo.

### RC3 · El valor total no puede exceder el tope del aprobador — **bloqueo**

Cada aprobador tiene un tope en pesos para cada centro de costo. El valor total de la
solicitud tiene que caber en ese tope.

*Por qué bloquea:* los topes son la delegación de autoridad de la compañía. Una compra
por encima del tope necesita a alguien con más tope, no una confirmación.
*Qué hacer:* escalar la aprobación al siguiente nivel, o partir la compra si el alcance
lo permite.
*Nota:* este control solo se evalúa si RC2 pasó. Si no se sabe quién aprobó, no tiene
sentido preguntar cuánto podía aprobar.

### RC4 · La subárea debe pertenecer al centro de costo — **bloqueo**

*Por qué bloquea:* el gasto se imputa a un centro de costo y a una subárea dentro de él.
Una subárea que no pertenece al centro deja el gasto en un lugar que no existe, y la
corrección contable después es manual.
*Qué hacer:* corregir la subárea en la solicitud.

### RC5 · La cotización debe cuadrar con la solicitud — **confirmación**

Se compara el total de la cotización contra el valor total de la solicitud. Una diferencia
pequeña se tolera; una diferencia relevante, o la ausencia de cotización, obliga a
confirmar.

*Por qué se confirma y no se bloquea:* las diferencias legítimas existen —el proveedor
ajustó el precio, cambió la tasa, la cotización es anterior— pero quien crea la orden
tiene que haber visto los dos números y haber decidido.
*Qué mostrar:* **los dos montos**, el de la solicitud y el de la cotización, y la
diferencia que reportó el control.

### RC6 · Indicador de IVA ausente — **confirmación y derivado**

Si la solicitud no trae indicador de IVA, se toma el que tiene por defecto el proveedor
en el maestro y se pide confirmación.

*Por qué se confirma:* el indicador decide el tratamiento tributario de la compra. El
valor por defecto del proveedor acierta casi siempre, pero "casi siempre" no es una base
para liquidar impuestos.
*Qué mostrar:* el código derivado y que salió del maestro del proveedor, no de la
solicitud.

### RC7 · Condiciones de pago ausentes — **solo se informa**

Si la solicitud no trae condiciones de pago, se toman las del proveedor en el maestro.

*Por qué no se confirma:* las condiciones de pago son parte del acuerdo comercial con el
proveedor y ya están negociadas en el maestro. Tomarlas de ahí es lo correcto, no una
excepción. Se informa para que quede visible de dónde salió el dato.

### RC8 · Compra retroactiva — **confirmación**

Si en el paquete hay una factura con fecha anterior a la fecha de la solicitud, la compra
ya ocurrió antes de pedirse. La orden queda marcada como **retroactiva**.

*Por qué se confirma:* la compra retroactiva es un hallazgo de control interno —se
compró sin orden y se formaliza después— pero el gasto ya existe y hay que registrarlo.
Se crea la orden, se deja la marca y queda en el archivo de control para que auditoría lo
vea.
*Qué mostrar:* **las dos fechas**, la de la factura y la de la solicitud.

### RC9 · La aprobación no puede ser anterior a la solicitud — **confirmación**

*Por qué se confirma:* una aprobación con fecha anterior a la solicitud suele ser un
error de digitación o un correo reusado de otra compra. No siempre es fraude, pero
siempre hay que mirarlo.
*Qué mostrar:* **las dos fechas**, la de la aprobación y la de la solicitud.

### RC10 · La aritmética de la solicitud debe cuadrar — **bloqueo**

Cantidad por valor unitario tiene que dar el valor total, con una tolerancia de una
unidad monetaria por redondeo.

*Por qué bloquea:* si los tres números no cuadran, no se sabe cuál de ellos es el
correcto, y la orden se emite con los tres. Es el único bloqueo que no depende de ningún
maestro: es una cuenta que tiene que dar.
*Qué hacer:* pedir al solicitante que corrija la solicitud.

---

## Lo que la orden deja escrito

Cada orden creada deja cuatro rastros, y vale la pena mencionárselos a la persona cuando
pregunta "¿y dónde queda esto?":

1. **El payload de la OC** tal como se envía a SAP, con el proveedor, la posición y las
   excepciones confirmadas.
2. **La evidencia de la aprobación**, el correo del líder archivado como archivo de
   texto, con su huella `sha256` dentro de la orden: si el archivo cambia, la huella no
   coincide.
3. **La trazabilidad**, que dice de dónde salió cada campo del payload: de la solicitud,
   del correo, de la cotización, de un maestro, de un derivado o de una constante.
4. **El archivo de control**, con una fila por cada intento —creado, bloqueado o
   pendiente de confirmación— para que auditoría vea también lo que no se creó.

Dos detalles del formato que conviene advertir:

- El texto breve de la posición en SAP admite **40 caracteres**. Una descripción más
  larga se corta ahí, y la completa queda en la trazabilidad.
- El precio unitario de la posición va **con IVA incluido**, igual que el monto que
  aprobó el líder y contra el que se corrieron los controles.

Crear dos veces la misma solicitud **no crea dos órdenes**: la segunda vez se devuelve el
número de la orden que ya existía, marcada como idempotente.
