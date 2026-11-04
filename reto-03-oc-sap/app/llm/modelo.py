"""El único archivo que elige proveedor de LLM (PRD §6.1).

Lo que antes eran `app/llm/adapter.py` (la interfaz propia) y
`app/llm/openai_adapter.py` (su implementación) son ahora `BaseChatModel` y
`ChatOpenAI`: la interfaz la pone LangChain y la implementación, su paquete de
proveedor. De los 301 líneas que ocupaban las dos sobrevive esta fábrica, porque
la elección tiene que seguir estando en **un** punto: la importan `app/api.py` y
`web/app.py`, y ningún otro módulo menciona OpenAI.

Cambiar de proveedor es cambiar la línea del `return`.
"""

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.core.config import Config

# Temperatura 0: el reto se califica por determinismo (C9). El azar del modelo no
# cambia ningún valor —los producen las herramientas— pero sí cambiaría el orden
# de las llamadas, y con él la bitácora que se audita.
TEMPERATURA = 0.0

PROVEEDOR = "openai"


def crear_modelo(config: Config) -> BaseChatModel:
    """El modelo del proceso, con el timeout y el determinismo que exige el reto.

    C7: este es el único punto del sistema que desenvuelve la credencial.
    `max_retries=0` porque el reintento lo decide el usuario (ver CA5 en
    `app/core/guardas.py`), no una capa invisible que multiplica el gasto.
    """
    return ChatOpenAI(
        model=config.openai_model,
        api_key=config.openai_api_key,
        timeout=float(config.llm_timeout_segundos),
        max_retries=0,
        temperature=TEMPERATURA,
    )
