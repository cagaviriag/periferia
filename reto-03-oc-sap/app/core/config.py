"""Configuración del proceso, leída del entorno y del `.env` de la raíz.

Único punto que lee variables de entorno. Falla temprano y con mensaje accionable
si falta algo. La clave del LLM vive solo aquí y nunca sale del backend
(C7 · PRD §8 Seguridad).

El `.env` de la raíz se lee a mano, sin `python-dotenv` (ADR-010): el mensaje de
error manda al operador a ese archivo, así que el código tiene que verlo.
"""

import os
from pathlib import Path

from pydantic import BaseModel, SecretStr

# Valores por defecto (T0.2 §Comportamiento 1 · C6 · PRD §8 Costo).
MODELO_POR_DEFECTO = "gpt-4o-mini"
MAX_ITERACIONES_POR_DEFECTO = 25
MAX_TOKENS_SESION_POR_DEFECTO = 100000
LLM_TIMEOUT_POR_DEFECTO = 60


class Config(BaseModel):
    """Configuración del proceso, leída del entorno."""

    # C7 · PRD §8: `SecretStr` es la red de seguridad de la credencial. Con un
    # `str` en claro, `repr(config)`, `str(config)`, `model_dump()` y
    # `model_dump_json()` la imprimían, así que un `logging.info(config)` o un
    # `return config.model_dump()` en /api/health la filtraba. Envuelta, las
    # cuatro vías muestran `**********`.
    #
    # Quien necesite el valor real debe pedirlo explícitamente con
    # `config.openai_api_key.get_secret_value()`. Hoy no hay ningún consumidor:
    # el primero será el adaptador LLM de la Fase 4 (T4.2).
    openai_api_key: SecretStr
    openai_model: str
    max_iteraciones: int
    max_tokens_sesion: int
    llm_timeout_segundos: int
    raiz: Path  # raíz del proyecto; todas las rutas se resuelven desde aquí


def _raiz_del_proyecto() -> Path:
    """Devuelve el directorio que contiene `app/`.

    Se resuelve desde `__file__` y nunca se escribe a mano: el proyecto debe
    poder moverse de carpeta sin tocar código (T0.2 §Comportamiento 2).
    """
    # app/core/config.py → parents[0]=app/core, parents[1]=app, parents[2]=raíz.
    return Path(__file__).resolve().parents[2]


def _quitar_comillas(valor: str) -> str:
    """Quita las comillas envolventes de un valor del `.env`, si las trae."""
    for comilla in ('"', "'"):
        if len(valor) >= 2 and valor.startswith(comilla) and valor.endswith(comilla):
            return valor[1:-1]
    return valor


def _leer_dotenv(raiz: Path) -> dict[str, str]:
    """Lee `raiz/.env` y devuelve sus pares `CLAVE=valor` (ADR-010).

    Se parsea a mano, sin `python-dotenv`: T0.1 fija la lista de dependencias de
    `pyproject.toml` y sumar una exige otra ADR. Nunca lanza por contenido: un
    `.env` ausente devuelve `{}` y una línea que no tenga `=` se ignora.
    """
    archivo = raiz / ".env"
    if not archivo.is_file():
        return {}

    valores: dict[str, str] = {}
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        limpia = linea.strip()
        if limpia == "" or limpia.startswith("#") or "=" not in limpia:
            continue
        nombre, _, valor = limpia.partition("=")
        valores[nombre.strip()] = _quitar_comillas(valor.strip())
    return valores


def _leer_variable(nombre: str, dotenv: dict[str, str]) -> str:
    """Lee una variable del entorno, con el `.env` como respaldo (ADR-010).

    `os.environ` tiene precedencia: un despliegue que inyecta variables manda
    sobre el archivo de desarrollo. Un valor exportado pero vacío cuenta como
    ausente, igual que antes de ADR-010.
    """
    desde_entorno = os.environ.get(nombre, "").strip()
    if desde_entorno != "":
        return desde_entorno
    return dotenv.get(nombre, "").strip()


def _leer_entero(nombre: str, por_defecto: int, dotenv: dict[str, str]) -> int:
    """Lee una variable numérica del entorno o del `.env`.

    Lanza ValueError diciendo cuál variable falló y qué valor recibió
    (T0.2 §Comportamiento 4).
    """
    crudo = _leer_variable(nombre, dotenv)
    if crudo == "":
        return por_defecto
    try:
        return int(crudo)
    except ValueError as error:
        raise ValueError(
            f"{nombre} debe ser un número entero. Recibí: {crudo!r}."
        ) from error


def cargar_config(requiere_llm: bool = True) -> Config:
    """Lee el entorno y devuelve la configuración.

    `requiere_llm=False` permite a demo.py y a los tests correr SIN clave
    (PRD §6.6: demo.py debe funcionar sin clave de ningún proveedor).
    En ese caso `openai_api_key` queda como un SecretStr vacío.

    Las variables se buscan primero en `os.environ` y luego en el `.env` de la
    raíz (ADR-010), así el operador que siguió `.env.example` tiene un backend
    funcional y el mensaje de error deja de mentir.

    Lanza ValueError con mensaje accionable si requiere_llm=True y falta la clave.
    Esta es la ÚNICA función del proyecto que puede lanzar: corre al arrancar,
    no dentro de una herramienta.
    """
    raiz = _raiz_del_proyecto()
    dotenv = _leer_dotenv(raiz)

    clave = _leer_variable("OPENAI_API_KEY", dotenv)

    # C7 · PRD §8: la clave solo existe en el entorno o en el .env del backend.
    # Se valida antes que nada para fallar al arrancar, no en medio de un turno.
    if requiere_llm and clave == "":
        raise ValueError("Falta OPENAI_API_KEY. Cópiala en .env siguiendo .env.example.")

    modelo = _leer_variable("OPENAI_MODEL", dotenv) or MODELO_POR_DEFECTO

    return Config(
        openai_api_key=SecretStr(clave),
        openai_model=modelo,
        max_iteraciones=_leer_entero("MAX_ITERACIONES", MAX_ITERACIONES_POR_DEFECTO, dotenv),
        max_tokens_sesion=_leer_entero(
            "MAX_TOKENS_SESION", MAX_TOKENS_SESION_POR_DEFECTO, dotenv
        ),
        llm_timeout_segundos=_leer_entero(
            "LLM_TIMEOUT_SEGUNDOS", LLM_TIMEOUT_POR_DEFECTO, dotenv
        ),
        raiz=raiz,
    )


def resumen_publico(config: Config) -> dict[str, str | int]:
    """Datos seguros para exponer en /api/health. NUNCA incluye la clave."""
    # C7 · PRD §8: /api/health reporta proveedor y modelo, jamás la credencial.
    return {
        "provider": "openai",
        "model": config.openai_model,
        "max_iteraciones": config.max_iteraciones,
    }
