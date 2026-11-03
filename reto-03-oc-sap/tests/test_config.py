"""Tests de T0.2 · Configuración."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from app.core import config as modulo_config
from app.core.config import Config, cargar_config, resumen_publico

VARIABLES = [
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "MAX_ITERACIONES",
    "MAX_TOKENS_SESION",
    "LLM_TIMEOUT_SEGUNDOS",
]

CLAVE_DE_PRUEBA = "clave-secreta-de-prueba-no-real"


@pytest.fixture
def sin_variables(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Entorno sin ninguna variable del proyecto, con la raíz real.

    `monkeypatch` revierte los cambios al terminar el test: no se toca el
    entorno real del proceso.
    """
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return monkeypatch


@pytest.fixture
def entorno_limpio(
    sin_variables: pytest.MonkeyPatch, tmp_path: Path
) -> pytest.MonkeyPatch:
    """Entorno sin variables y con la raíz apuntada a un directorio temporal.

    Desde ADR-010 `cargar_config` lee el `.env` de la raíz, y el repositorio
    tiene uno con una clave real. Apuntar la raíz a `tmp_path` hace que estos
    tests no dependan de un archivo que no está versionado.
    """
    sin_variables.setattr(modulo_config, "_raiz_del_proyecto", lambda: tmp_path)
    return sin_variables


def escribir_dotenv(raiz: Path, contenido: str) -> None:
    """Crea un `.env` en una raíz temporal. Nunca toca el `.env` real."""
    (raiz / ".env").write_text(contenido, encoding="utf-8")


def test_sin_clave_con_llm_requerido_lanza(entorno_limpio: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        cargar_config(requiere_llm=True)


def test_sin_clave_sin_llm_requerido_funciona(entorno_limpio: pytest.MonkeyPatch) -> None:
    # PRD §6.6: demo.py corre sin clave de ningún proveedor.
    config = cargar_config(requiere_llm=False)
    assert config.openai_api_key.get_secret_value() == ""


def test_entorno_vacio_usa_defaults(entorno_limpio: pytest.MonkeyPatch) -> None:
    config = cargar_config(requiere_llm=False)
    assert config.openai_model == "gpt-4o-mini"
    assert config.max_iteraciones == 25
    assert config.max_tokens_sesion == 100000
    assert config.llm_timeout_segundos == 60


def test_max_iteraciones_no_numerico_lanza(entorno_limpio: pytest.MonkeyPatch) -> None:
    entorno_limpio.setenv("MAX_ITERACIONES", "abc")
    with pytest.raises(ValueError) as capturado:
        cargar_config(requiere_llm=False)
    mensaje = str(capturado.value)
    assert "MAX_ITERACIONES" in mensaje
    assert "abc" in mensaje


def test_resumen_publico_no_expone_la_clave(entorno_limpio: pytest.MonkeyPatch) -> None:
    # Verificación de C7: la clave no puede aparecer en ningún valor del resumen.
    entorno_limpio.setenv("OPENAI_API_KEY", CLAVE_DE_PRUEBA)

    config = cargar_config(requiere_llm=True)
    resumen = resumen_publico(config)

    assert config.openai_api_key.get_secret_value() == CLAVE_DE_PRUEBA
    for valor in resumen.values():
        assert CLAVE_DE_PRUEBA not in str(valor)
    assert resumen["provider"] == "openai"


def test_raiz_contiene_fixtures(sin_variables: pytest.MonkeyPatch) -> None:
    config = cargar_config(requiere_llm=False)
    assert (config.raiz / "fixtures").exists()
    assert (config.raiz / "app").is_dir()


# --- D2 · C7: la clave no se filtra por ninguna de las cuatro vías --------------
#
# Antes de SecretStr, `Config` era un BaseModel con la clave en claro y la
# exponía por repr(), str(), model_dump() y model_dump_json(). Nadie la filtraba
# todavía, pero un `logging.info(config)` o un `return config.model_dump()` en
# /api/health lo habría hecho. Estos tests fijan el cierre.


def config_con_clave(raiz: Path) -> Config:
    """Config construida a mano, para no depender del entorno del proceso."""
    return Config(
        openai_api_key=SecretStr(CLAVE_DE_PRUEBA),
        openai_model="gpt-4o-mini",
        max_iteraciones=25,
        max_tokens_sesion=100000,
        llm_timeout_segundos=60,
        raiz=raiz,
    )


def test_repr_no_expone_la_clave(tmp_path: Path) -> None:
    assert CLAVE_DE_PRUEBA not in repr(config_con_clave(tmp_path))


def test_str_no_expone_la_clave(tmp_path: Path) -> None:
    assert CLAVE_DE_PRUEBA not in str(config_con_clave(tmp_path))


def test_model_dump_no_expone_la_clave(tmp_path: Path) -> None:
    volcado = config_con_clave(tmp_path).model_dump()

    assert CLAVE_DE_PRUEBA not in str(volcado)
    # El campo sigue existiendo: lo que cambia es que su valor queda enmascarado.
    assert "openai_api_key" in volcado


def test_model_dump_json_no_expone_la_clave(tmp_path: Path) -> None:
    assert CLAVE_DE_PRUEBA not in config_con_clave(tmp_path).model_dump_json()


def test_get_secret_value_es_la_unica_via_al_valor(tmp_path: Path) -> None:
    # La vía explícita que usará el adaptador LLM de la Fase 4.
    assert config_con_clave(tmp_path).openai_api_key.get_secret_value() == CLAVE_DE_PRUEBA


# --- D3 · ADR-010: cargar_config lee el .env de la raíz -------------------------


def test_dotenv_aporta_la_clave_con_el_entorno_limpio(
    entorno_limpio: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # El defecto D3: el mensaje de error mandaba al operador a un .env que el
    # código no leía, con la clave correcta ya dentro.
    escribir_dotenv(tmp_path, f"OPENAI_API_KEY={CLAVE_DE_PRUEBA}\n")

    config = cargar_config(requiere_llm=True)

    assert config.openai_api_key.get_secret_value() == CLAVE_DE_PRUEBA


def test_entorno_gana_sobre_el_dotenv(
    entorno_limpio: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ADR-010: el entorno real manda, para que un despliegue con variables
    # inyectadas no quede pisado por un archivo de desarrollo.
    entorno_limpio.setenv("OPENAI_API_KEY", "clave-exportada")
    escribir_dotenv(tmp_path, "OPENAI_API_KEY=clave-del-archivo\n")

    config = cargar_config(requiere_llm=True)

    assert config.openai_api_key.get_secret_value() == "clave-exportada"


def test_sin_dotenv_se_comporta_como_antes(entorno_limpio: pytest.MonkeyPatch) -> None:
    assert not (cargar_config(requiere_llm=False).raiz / ".env").exists()
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        cargar_config(requiere_llm=True)


def test_dotenv_aporta_los_enteros_y_el_modelo(
    entorno_limpio: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    escribir_dotenv(
        tmp_path,
        "OPENAI_MODEL=gpt-4o\nMAX_ITERACIONES=7\nMAX_TOKENS_SESION=500\n"
        "LLM_TIMEOUT_SEGUNDOS=11\n",
    )

    config = cargar_config(requiere_llm=False)

    assert config.openai_model == "gpt-4o"
    assert config.max_iteraciones == 7
    assert config.max_tokens_sesion == 500
    assert config.llm_timeout_segundos == 11


def test_dotenv_ignora_comentarios_vacias_y_lineas_sin_igual(
    entorno_limpio: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # La función no lanza por un .env malformado: lo que no entiende lo ignora.
    escribir_dotenv(
        tmp_path,
        "\n"
        "# OPENAI_API_KEY=clave-comentada\n"
        "esta linea no tiene igual\n"
        "   \n"
        f"  OPENAI_API_KEY =  {CLAVE_DE_PRUEBA}  \n",
    )

    config = cargar_config(requiere_llm=True)

    assert config.openai_api_key.get_secret_value() == CLAVE_DE_PRUEBA


@pytest.mark.parametrize("comilla", ['"', "'"])
def test_dotenv_quita_las_comillas_envolventes(
    entorno_limpio: pytest.MonkeyPatch, tmp_path: Path, comilla: str
) -> None:
    escribir_dotenv(tmp_path, f"OPENAI_API_KEY={comilla}{CLAVE_DE_PRUEBA}{comilla}\n")

    config = cargar_config(requiere_llm=True)

    assert config.openai_api_key.get_secret_value() == CLAVE_DE_PRUEBA


def test_dotenv_conserva_el_signo_igual_del_valor(
    entorno_limpio: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Una clave puede traer `=` (padding base64): solo se parte en el primero.
    escribir_dotenv(tmp_path, "OPENAI_API_KEY=clave-abc=def==\n")

    config = cargar_config(requiere_llm=True)

    assert config.openai_api_key.get_secret_value() == "clave-abc=def=="
