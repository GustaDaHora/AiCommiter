"""Config file loading and validation.

Reads ~/.config/aicommit/config.toml (or platform-appropriate path),
merges with environment variables, and returns a typed Config dataclass.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from platformdirs import user_config_dir

from aicommit.exceptions import ConfigError, MissingApiKeyError
from aicommit.models import Config

_DEFAULTS = {
    "model": "openai/gpt-4o-mini",
    "base_url": "https://openrouter.ai/api/v1",
    "max_diff_lines_per_file": 500,
    "max_diff_lines_total": 2000,
}


def _get_config_path() -> Path:
    """Resolve the path to config.toml, honoring AICOMMIT_CONFIG env var."""
    if "AICOMMIT_CONFIG" in os.environ:
        return Path(os.environ["AICOMMIT_CONFIG"])
    config_dir = user_config_dir("aicommit", appauthor=False)
    return Path(config_dir) / "config.toml"


def _load_toml(path: Path) -> dict[str, object]:
    """Load and parse a TOML file. Returns empty dict if file doesn't exist."""
    if not path.is_file():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid config file at {path}: {exc}") from exc


def load_config() -> Config:
    """Load configuration from TOML file and environment variables.

    Precedence (highest to lowest):
        1. Environment variables
        2. config.toml values
        3. Defaults
    """
    path = _get_config_path()
    data = _load_toml(path)

    api_section = data.get("api", {})
    if not isinstance(api_section, dict):
        api_section = {}

    behaviour_section = data.get("behaviour", {})
    if not isinstance(behaviour_section, dict):
        behaviour_section = {}

    env_api_key = os.environ.get("OPENROUTER_API_KEY", "")
    file_api_key = str(api_section.get("api_key", ""))

    api_key = env_api_key or file_api_key
    if not api_key:
        raise MissingApiKeyError(
            "API key not found. Set OPENROUTER_API_KEY environment variable "
            "or add api_key to your config file."
        )

    model = str(api_section.get("model", _DEFAULTS["model"]))
    base_url = str(api_section.get("base_url", _DEFAULTS["base_url"]))

    max_diff_lines_per_file = int(
        str(behaviour_section.get("max_diff_lines_per_file", _DEFAULTS["max_diff_lines_per_file"]))
    )
    max_diff_lines_total = int(
        str(behaviour_section.get("max_diff_lines_total", _DEFAULTS["max_diff_lines_total"]))
    )

    editor_raw = str(behaviour_section.get("editor", ""))
    editor = editor_raw if editor_raw else None

    return Config(
        api_key=api_key,
        model=model,
        base_url=base_url,
        max_diff_lines_per_file=max_diff_lines_per_file,
        max_diff_lines_total=max_diff_lines_total,
        editor=editor,
    )


def set_api_key(api_key: str) -> None:
    """Save the OpenRouter API key to the local config file."""
    path = _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.is_file():
        content = f'[api]\napi_key = "{api_key}"\n'
        path.write_text(content, encoding="utf-8")
        return

    content = path.read_text(encoding="utf-8")

    import re

    if re.search(r"^api_key\s*=", content, re.MULTILINE):
        new_content = re.sub(
            r'^(api_key\s*=\s*).*$',
            f'\\1"{api_key}"',
            content,
            flags=re.MULTILINE,
        )
        path.write_text(new_content, encoding="utf-8")
        return

    if "[api]" in content:
        new_content = content.replace("[api]", f'[api]\napi_key = "{api_key}"', 1)
        path.write_text(new_content, encoding="utf-8")
        return

    if not content.endswith("\n"):
        content += "\n"
    content += f'\n[api]\napi_key = "{api_key}"\n'
    path.write_text(content, encoding="utf-8")
