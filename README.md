# Periferia IT Group — Prueba técnica de IA

Solución al **Reto 03 · Agente conversacional de Órdenes de Compra SAP**.

## Contenido

| Carpeta | Qué es |
|---|---|
| [`reto-03-oc-sap/`](reto-03-oc-sap/) | La solución completa: especificaciones, agente, herramientas y front |

Los instructivos en PDF y la carpeta original descomprimida del reto no se versionan
(ver [`.gitignore`](.gitignore)). Los fixtures que la solución necesita para ejecutarse
están en `reto-03-oc-sap/fixtures/`.

## El reto en una frase

Automatizar la preparación y creación de órdenes de compra: leer el paquete de compra
que llega por correo (solicitud + cotización + aprobación del líder), validarlo contra
10 controles de negocio y los maestros corporativos, y crear la OC en un SAP simulado —
devolviendo al humano las excepciones en lugar de forzarlas.

## Arranque rápido

```bash
cd reto-03-oc-sap
uv sync
python demo.py          # procesa los 6 casos SIN consumir modelo de lenguaje
pytest -v               # suite de pruebas
```

`demo.py` no requiere clave de ningún proveedor: demuestra que las reglas de control
son deterministas y verificables sin LLM.

## Metodología

Desarrollado con **SDD (Spec-Driven Development)**: las especificaciones mandan sobre
el código. El criterio de aceptación está escrito antes de implementar, en
[`reto-03-oc-sap/specs/01-oracle.md`](reto-03-oc-sap/specs/01-oracle.md), y un
subagente de QA valida contra él ejecutando los comandos reales.

Documentos rectores en [`reto-03-oc-sap/specs/`](reto-03-oc-sap/specs/):

- `00-constitution.md` — reglas no negociables del proyecto
- `01-oracle.md` — matriz de resultados esperados de los 6 casos + invariantes
- `02-decisions.md` — registro de decisiones de arquitectura (ADR)
- `03-plan.md` — plan de 6 fases con gates de verificación

## Stack

Python 3.12 · Pydantic v2 · SDK de OpenAI · FastAPI · Streamlit · pytest · mypy

Decisiones de stack documentadas y justificadas en `specs/02-decisions.md`.
