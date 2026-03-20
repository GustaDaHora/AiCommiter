"""Unit tests for aicommit.ai module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aicommit.ai import suggest_commit_message
from aicommit.models import ChangedFile, Config, DiffPayload


@pytest.fixture
def sample_diff(sample_changed_files: list[ChangedFile]) -> DiffPayload:
    """Return a sample DiffPayload for testing."""
    return DiffPayload(
        files=sample_changed_files,
        diff_text="diff --git a/src/main.py\n+print('hello')\n",
        was_truncated=False,
        total_lines=2,
    )


@pytest.fixture
def mock_httpx_client(mocker: MagicMock) -> MagicMock:
    """Patch httpx.Client in the ai module."""
    mock_client_instance = MagicMock()
    mock_client_cls = mocker.patch("aicommit.ai.httpx.Client")
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_client_instance


def _make_api_response(content: str, status_code: int = 200) -> MagicMock:
    """Create a mock httpx response."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "model": "openai/gpt-4o-mini",
    }
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        response.text = f"Error {status_code}"
    return response


class TestSuggestCommitMessage:
    """Tests for suggest_commit_message()."""

    def test_success_returns_commit_suggestion(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mock_config: Config,
    ) -> None:
        """Happy path: AI returns a valid commit message."""
        mock_httpx_client.post.return_value = _make_api_response(
            "feat(main): add hello world print statement"
        )

        result = suggest_commit_message(sample_diff, mock_config)

        assert result.ok is True
        assert result.value is not None
        assert result.value.subject == "feat(main): add hello world print statement"
        assert result.value.body is None
        assert result.value.model_used == "openai/gpt-4o-mini"

    def test_success_with_body(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mock_config: Config,
    ) -> None:
        """Happy path: AI returns subject + body."""
        message = "feat(main): add feature\n\nThis adds a new feature that prints hello."
        mock_httpx_client.post.return_value = _make_api_response(message)

        result = suggest_commit_message(sample_diff, mock_config)

        assert result.ok is True
        assert result.value is not None
        assert result.value.subject == "feat(main): add feature"
        assert result.value.body == "This adds a new feature that prints hello."

    def test_empty_response_returns_error(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mock_config: Config,
    ) -> None:
        """Error path: AI returns an empty response for all models."""
        mock_httpx_client.post.return_value = _make_api_response("")

        result = suggest_commit_message(sample_diff, mock_config)

        assert result.ok is False
        assert "All models failed" in (result.error or "")
        assert "empty response" in (result.error or "").lower()

    def test_http_error_returns_error(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mock_config: Config,
    ) -> None:
        """Error path: HTTP error from OpenRouter for all models."""
        mock_httpx_client.post.return_value = _make_api_response("", status_code=500)

        result = suggest_commit_message(sample_diff, mock_config)

        assert result.ok is False
        assert "All models failed" in (result.error or "")

    def test_timeout_returns_error(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mock_config: Config,
    ) -> None:
        """Error path: request times out for all models."""
        import httpx

        mock_httpx_client.post.side_effect = httpx.TimeoutException("Connection timed out")

        result = suggest_commit_message(sample_diff, mock_config)

        assert result.ok is False
        assert "All models failed" in (result.error or "")
        assert "timed out" in (result.error or "").lower()

    def test_truncates_long_subject(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mock_config: Config,
    ) -> None:
        """Subject line exceeding 72 chars is truncated."""
        long_subject = "feat: " + "a" * 100
        mock_httpx_client.post.return_value = _make_api_response(long_subject)

        result = suggest_commit_message(sample_diff, mock_config)

        assert result.ok is True
        assert result.value is not None
        assert len(result.value.subject) <= 72

    def test_malformed_json_returns_error(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mock_config: Config,
    ) -> None:
        """Error path: API response has unexpected JSON structure for all models."""
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {"unexpected": "structure"}
        response.text = '{"unexpected": "structure"}'
        mock_httpx_client.post.return_value = response

        result = suggest_commit_message(sample_diff, mock_config)

        assert result.ok is False
        assert "All models failed" in (result.error or "")
        assert "unexpected" in (result.error or "").lower()

    def test_fallback_success(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mock_config: Config,
    ) -> None:
        """Happy path: first model fails, second model succeeds."""
        import httpx

        # First call raises timeout, second call succeeds
        mock_httpx_client.post.side_effect = [
            httpx.TimeoutException("Connection timed out"),
            _make_api_response("feat(main): fallback success"),
        ]

        result = suggest_commit_message(sample_diff, mock_config)

        assert result.ok is True
        assert result.value is not None
        assert result.value.subject == "feat(main): fallback success"
        assert mock_httpx_client.post.call_count == 2

    def test_none_content_returns_error(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mock_config: Config,
    ) -> None:
        """Error path: AI returns None for content (e.g. content policy block)."""
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": None}}],
            "model": "openai/gpt-4o-mini",
        }
        mock_httpx_client.post.return_value = response

        result = suggest_commit_message(sample_diff, mock_config)

        assert result.ok is False
        assert "All models failed" in (result.error or "")
        assert "empty response" in (result.error or "").lower()

class TestApiLogging:
    """Tests for the API logging functionality."""

    def test_logs_created_when_enabled(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mocker: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Logs are written to the config directory when enable_logging is 1."""
        config = Config(
            api_key="sk-test",
            model="openai/gpt-4o-mini",
            base_url="https://openrouter.ai/api/v1",
            max_diff_lines_per_file=500,
            max_diff_lines_total=2000,
            editor=None,
            enable_logging=1,
        )
        
        # Mock _get_config_path in the config module since it is imported inside _log_api_event
        mock_get_path = mocker.patch("aicommit.config._get_config_path")
        mock_get_path.return_value = tmp_path / "config.toml"
        
        mock_httpx_client.post.return_value = _make_api_response("feat: test log")

        suggest_commit_message(sample_diff, config)

        log_dir = tmp_path / "logs"
        assert log_dir.is_dir()
        
        log_files = list(log_dir.glob("*.json"))
        # Should have at least a request and a response log
        assert len(log_files) >= 2
        
        file_types = [f.name for f in log_files]
        assert any("request" in name for name in file_types)
        assert any("response" in name for name in file_types)

    def test_no_logs_when_disabled(
        self,
        mock_httpx_client: MagicMock,
        sample_diff: DiffPayload,
        mocker: MagicMock,
        tmp_path: Path,
    ) -> None:
        """No log files are created when enable_logging is 0."""
        config = Config(
            api_key="sk-test",
            model="openai/gpt-4o-mini",
            base_url="https://openrouter.ai/api/v1",
            max_diff_lines_per_file=500,
            max_diff_lines_total=2000,
            editor=None,
            enable_logging=0,
        )
        
        mock_get_path = mocker.patch("aicommit.config._get_config_path")
        mock_get_path.return_value = tmp_path / "config.toml"
        
        mock_httpx_client.post.return_value = _make_api_response("feat: no log")

        suggest_commit_message(sample_diff, config)

        log_dir = tmp_path / "logs"
        if log_dir.exists():
            assert not list(log_dir.glob("*.json"))
