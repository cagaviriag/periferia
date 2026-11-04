# Despliegue en Streamlit Community Cloud

El front y el ciclo del agente corren en **un solo proceso**: `web/app.py` llama a
`app/core/agente.py` en proceso. Streamlit Community Cloud despliega un servicio por
repositorio, gratis, y un único proceso significa un único punto de falla. El PRD §9.3
descuenta **−10** si la URL no está viva durante la defensa, así que el despliegue más
simple es el correcto.

> El front conmuta a HTTP solo si existe la variable `API_URL`. En este despliegue **no
> se define**, y por eso no hace falta publicar la API de FastAPI por separado.

---

## Pasos

1. **Sube el código a GitHub.** El repositorio es `cagaviriag/periferia` y el proyecto
   vive en la carpeta `reto-03-oc-sap/`. Verifica antes de subir que `.env`, `out/` y
   `.venv/` están ignorados (ya lo están en `.gitignore`).

   ```bash
   git add reto-03-oc-sap && git commit -m "Reto 03 · front y despliegue" && git push
   ```

2. **Entra a [share.streamlit.io](https://share.streamlit.io)** con la cuenta de GitHub
   dueña del repositorio y pulsa **Create app → Deploy a public app from GitHub**.

3. **Rellena el formulario exactamente así:**

   | Campo | Valor |
   |---|---|
   | Repository | `cagaviriag/periferia` |
   | Branch | `main` |
   | Main file path | `reto-03-oc-sap/web/app.py` |
   | App URL | `oc-sap-periferia` (o el nombre que prefieras) |
   | Python version (**Advanced settings**) | `3.12` |

4. **Pega los secretos** en **Advanced settings → Secrets** (o, ya desplegada, en
   **⋮ → Settings → Secrets**). Es formato TOML de Streamlit, una variable por línea:

   ```toml
   OPENAI_API_KEY = "sk-..."
   OPENAI_MODEL = "gpt-4o-mini"
   MAX_ITERACIONES = "25"
   MAX_TOKENS_SESION = "100000"
   LLM_TIMEOUT_SEGUNDOS = "60"
   ```

   > ⚠️ **La clave va solo aquí.** Nunca en el repositorio, ni en `.streamlit/config.toml`,
   > ni en un `secrets.toml` versionado, ni en el código. `web/app.py` la copia al entorno
   > del proceso (`_propagar_secretos`) y `app/core/config.py` la guarda en un `SecretStr`
   > para que no pueda imprimirse (C7 · PRD §8 Seguridad). Si se filtra alguna vez,
   > revócala en la consola del proveedor y pega una nueva: no hay nada que limpiar en el
   > repositorio porque nunca estuvo ahí.

5. **Pulsa Deploy.** El primer arranque instala `requirements.txt` y tarda 2–4 minutos.
   Sigue el log desde **Manage app** (abajo a la derecha).

6. **Si el log falla con `ModuleNotFoundError`**, Streamlit no encontró el archivo de
   dependencias: busca `requirements.txt` junto al archivo de arranque o en la raíz del
   repositorio, y aquí vive en `reto-03-oc-sap/`. Cópialo a la raíz y vuelve a desplegar:

   ```bash
   cp reto-03-oc-sap/requirements.txt requirements.txt && git add requirements.txt \
     && git commit -m "requirements en la raíz para Streamlit Cloud" && git push
   ```

7. **Verifica la URL** antes de la defensa:
   - La barra lateral muestra **Proveedor**, **Modelo** y "Clave del modelo configurada".
     Si dice que falta `OPENAI_API_KEY`, revisa el paso 4.
   - Pulsa el atajo **sol-004 · cotización ≠ solicitud (§11)**: deben aparecer las
     llamadas a herramientas, la tabla de la OC, los dos montos (25.000.000 vs
     26.500.000, desviación 6,00 %) y el bloque de confirmación resaltado.
   - Pulsa **✅ Confirmar**: el agente debe responder con el número de OC.

8. **Pega la URL pública en el `README.md`** (sección "Enlace público") y confirma que
   responde desde una ventana de incógnito, sin sesión de GitHub.

---

## ⚠️ Antes de la defensa: despierta la app

Community Cloud **suspende las aplicaciones inactivas**. El primer arranque instala
dependencias y levanta el contenedor: medido entre 30 segundos y 2 minutos. Ya
despierta, responde en **menos de 2 segundos**.

Eso es un riesgo concreto: si el evaluador abre la URL en frío, hay un silencio
incómodo de minuto y medio delante de todos.

**Rutina recomendada:**

1. **15 minutos antes**, abre la URL y espera a que cargue del todo.
2. Pulsa el atajo de **sol-001** y deja que termine. Eso calienta además el cliente de
   OpenAI y la lectura de los fixtures, no solo el contenedor.
3. **Deja la pestaña abierta** durante la sesión. Mientras haya una conexión activa la
   app no se duerme.
4. Si entre el calentamiento y la demo pasan más de 30 minutos, repite el paso 2.

Si aun así te toca arrancar en frío delante del evaluador, aprovecha la espera para
mostrar `demo.py` en la terminal: **no requiere clave ni red**, procesa los 6 casos en
menos de un segundo y es el entregable que demuestra el determinismo.

---

## Mantenimiento durante la defensa

| Situación | Qué hacer |
|---|---|
| La app está "dormida" | Ver la rutina de calentamiento de arriba. |
| Cambiaste código | `git push`: Streamlit redespliega solo. |
| Cambiaste secretos | Guardar los secretos reinicia la app; no hace falta redesplegar. |
| `out/` en el servidor | El disco de Community Cloud es efímero: las OC creadas se pierden al reiniciar, y la numeración vuelve a `4500000001`. Es aceptable para la demo y está dicho en `SOLUCION.md`. |
| Todo falla | Plan B del PRD §9.3: correrlo en local durante la defensa, con penalización de −10. `uv run streamlit run web/app.py`. |
