"""Unit tests for aicommit.config module."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicommit.config import load_config, set_api_key
from aicommit.exceptions import ConfigError, MissingApiKeyError
from aicommit.models import Config


def _write_toml(path: Path, content: str) -> None:
    """Helper to write a TOML config file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestLoadConfig:
    """Tests for load_config()."""

    def test_load_valid_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: valid TOML with all keys set."""
        config_file = tmp_path / "config.toml"
        _write_toml(
            config_file,
            """\
[api]
api_key = "sk-test-123"
model = "anthropic/claude-3"
base_url = "https://example.com/api/v1"

[behaviour]
max_diff_lines_per_file = 300
max_diff_lines_total = 1500
editor = "vim"
enable_logging = 1
""",
        )
        monkeypatch.setenv("AICOMMIT_CONFIG", str(config_file))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        config = load_config()

        assert isinstance(config, Config)
        assert config.api_key == "sk-test-123"
        assert config.model == "anthropic/claude-3"
        assert config.base_url == "https://example.com/api/v1"
        assert config.max_diff_lines_per_file == 300
        assert config.max_diff_lines_total == 1500
        assert config.editor == "vim"
        assert config.enable_logging == 1

    def test_env_var_overrides_api_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENROUTER_API_KEY env var takes precedence over config.toml api_key."""
        config_file = tmp_path / "config.toml"
        _write_toml(
            config_file,
            """\
[api]
api_key = "sk-from-file"
""",
        )
        monkeypatch.setenv("AICOMMIT_CONFIG", str(config_file))
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env")

        config = load_config()

        assert config.api_key == "sk-from-env"

    def test_defaults_applied_when_keys_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defaults are used when optional keys are missing from TOML."""
        config_file = tmp_path / "config.toml"
        _write_toml(
            config_file,
            """\
[api]
api_key = "sk-test-key"
""",
        )
        monkeypatch.setenv("AICOMMIT_CONFIG", str(config_file))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        config = load_config()

        assert config.model == "openai/gpt-4o-mini"
        assert config.base_url == "https://openrouter.ai/api/v1"
        assert config.max_diff_lines_per_file == 500
        assert config.max_diff_lines_total == 2000
        assert config.editor is None
        assert config.enable_logging == 0

    def test_missing_api_key_raises_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ConfigError raised when no API key is found anywhere."""
        config_file = tmp_path / "config.toml"
        _write_toml(
            config_file,
            """\
[api]
model = "openai/gpt-4o-mini"
""",
        )
        monkeypatch.setenv("AICOMMIT_CONFIG", str(config_file))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        with pytest.raises(ConfigError, match="API key"):
            load_config()

    def test_aicommit_config_env_var_overrides_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AICOMMIT_CONFIG env var overrides the default config path."""
        custom_path = tmp_path / "custom" / "config.toml"
        _write_toml(
            custom_path,
            """\
[api]
api_key = "sk-custom-path"
""",
        )
        monkeypatch.setenv("AICOMMIT_CONFIG", str(custom_path))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        config = load_config()

        assert config.api_key == "sk-custom-path"

    def test_missing_config_file_uses_defaults_with_env_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When config file doesn't exist but API key is in env, defaults are used."""
        nonexistent = tmp_path / "nonexistent" / "config.toml"
        monkeypatch.setenv("AICOMMIT_CONFIG", str(nonexistent))
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env-only")

        config = load_config()

        assert config.api_key == "sk-env-only"
        assert config.model == "openai/gpt-4o-mini"

    def test_missing_config_file_and_no_env_key_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ConfigError when config file doesn't exist AND no env var set."""
        nonexistent = tmp_path / "nonexistent" / "config.toml"
        monkeypatch.setenv("AICOMMIT_CONFIG", str(nonexistent))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        with pytest.raises(MissingApiKeyError, match="API key"):
            load_config()

    def test_invalid_toml_raises_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ConfigError raised for malformed TOML."""
        config_file = tmp_path / "config.toml"
        _write_toml(config_file, "this is not valid toml [[[")
        monkeypatch.setenv("AICOMMIT_CONFIG", str(config_file))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        with pytest.raises(ConfigError, match="[Pp]arse|[Ii]nvalid"):
            load_config()

    def test_empty_api_key_in_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MissingApiKeyError when api_key is an empty string and no env var."""
        config_file = tmp_path / "config.toml"
        _write_toml(
            config_file,
            """\
[api]
api_key = ""
""",
        )
        monkeypatch.setenv("AICOMMIT_CONFIG", str(config_file))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        with pytest.raises(MissingApiKeyError, match="API key"):
            load_config()

    def test_empty_editor_becomes_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty editor string in config is treated as None (use $EDITOR)."""
        config_file = tmp_path / "config.toml"
        _write_toml(
            config_file,
            """\
[api]
api_key = "sk-test"

[behaviour]
editor = ""
""",
        )
        monkeypatch.setenv("AICOMMIT_CONFIG", str(config_file))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        config = load_config()
        assert config.editor is None


class TestSetApiKey:
    """Tests for set_api_key()."""

    def test_creates_new_file_if_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If config.toml does not exist, it is created with the API key."""
        config_file = tmp_path / "config.toml"
        monkeypatch.setenv("AICOMMIT_CONFIG", str(config_file))

        set_api_key("sk-new-key")

        content = config_file.read_text(encoding="utf-8")
        assert "[api]" in content
        assert 'api_key = "sk-new-key"' in content

    def test_appends_to_existing_file_without_api_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If config.toml exists but has no [api] section, appends it."""
        config_file = tmp_path / "config.toml"
        _write_toml(
            config_file,
            """\
[behaviour]
editor = "vim"
""",
        )
        monkeypatch.setenv("AICOMMIT_CONFIG", str(config_file))

        set_api_key("sk-appended")

        content = config_file.read_text(encoding="utf-8")
        assert 'editor = "vim"' in content
        assert "[api]" in content
        assert 'api_key = "sk-appended"' in content

    def test_replaces_existing_api_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If config.toml exists and has [api] api_key, replaces it."""
        config_file = tmp_path / "config.toml"
        _write_toml(
            config_file,
            """\
# some comment
[api]
api_key = "sk-old-key"
model = "anthropic/claude-3"
""",
        )
        monkeypatch.setenv("AICOMMIT_CONFIG", str(config_file))

        set_api_key("sk-replaced")

        content = config_file.read_text(encoding="utf-8")
        assert "# some comment" in content
        assert 'api_key = "sk-old-key"' not in content
        assert 'api_key = "sk-replaced"' in content
        assert 'model = "anthropic/claude-3"' in content
