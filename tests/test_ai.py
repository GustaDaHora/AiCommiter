"""Unit tests for aicommit.ai module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx

from aicommit.ai import (
    FALLBACK_MODELS,
    suggest_commit_message,
    suggest_gitignore,
)
from aicommit.models import ChangedFile, Config, DiffPayload


class TestSuggestCommitMessage:
    """Tests for suggest_commit_message()."""

    def test_success(self, mocker: MagicMock, mock_config: Config) -> None:
        """Happy path: suggest_commit_message returns suggestion on successful API call."""
        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "feat(git): add support for untracked files\n\n"
                            "This is the body detailing why."
                        )
                    }
                }
            ],
            "model": "openai/gpt-4o-mini"
        }
        mock_client.post.return_value = mock_response

        diff = DiffPayload(
            files=[ChangedFile(path="src/main.py", status="M", staged=True)],
            diff_text="some diff content",
            was_truncated=False,
            total_lines=10,
        )

        result = suggest_commit_message(diff, mock_config)

        assert result.ok is True
        assert result.value is not None
        assert result.value.subject == "feat(git): add support for untracked files"
        assert result.value.body == "This is the body detailing why."
        assert result.value.message == (
            "feat(git): add support for untracked files\n\n"
            "This is the body detailing why."
        )
        assert result.value.model_used == "openai/gpt-4o-mini"

    def test_truncate_subject(self, mocker: MagicMock, mock_config: Config) -> None:
        """Subject line is truncated to 72 characters if it exceeds the limit."""
        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        long_subject = "feat(git): " + "a" * 80
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": long_subject
                    }
                }
            ],
            "model": "openai/gpt-4o-mini"
        }
        mock_client.post.return_value = mock_response

        diff = DiffPayload(
            files=[ChangedFile(path="src/main.py", status="M", staged=True)],
            diff_text="some diff content",
            was_truncated=False,
            total_lines=10,
        )

        result = suggest_commit_message(diff, mock_config)

        assert result.ok is True
        assert result.value is not None
        assert len(result.value.subject) == 72
        assert result.value.subject == long_subject[:72]

    def test_unauthorized(self, mocker: MagicMock, mock_config: Config) -> None:
        """HTTP 401 raises an unauthorized error."""
        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        response_401 = mocker.MagicMock()
        response_401.status_code = 401
        response_401.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="Unauthorized",
            request=mocker.MagicMock(),
            response=response_401
        )
        mock_client.post.return_value = response_401

        diff = DiffPayload(
            files=[ChangedFile(path="src/main.py", status="M", staged=True)],
            diff_text="some diff",
            was_truncated=False,
            total_lines=1,
        )

        result = suggest_commit_message(diff, mock_config)

        assert result.ok is False
        assert "API unauthorized (HTTP 401)" in (result.error or "")

    def test_other_http_errors_tries_fallback(
        self, mocker: MagicMock, mock_config: Config
    ) -> None:
        """HTTP 500 or other errors makes it fallback to other models."""
        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        response_500 = mocker.MagicMock()
        response_500.status_code = 500
        response_500.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="Internal Server Error",
            request=mocker.MagicMock(),
            response=response_500
        )

        mock_response_success = mocker.MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "feat: fallback success"
                    }
                }
            ],
            "model": FALLBACK_MODELS[0]
        }

        mock_client.post.side_effect = [
            response_500,
            mock_response_success
        ]

        diff = DiffPayload(
            files=[ChangedFile(path="src/main.py", status="M", staged=True)],
            diff_text="some diff",
            was_truncated=False,
            total_lines=1,
        )

        result = suggest_commit_message(diff, mock_config)

        assert result.ok is True
        assert result.value is not None
        assert result.value.subject == "feat: fallback success"

    def test_unexpected_api_response_tries_fallback(
        self, mocker: MagicMock, mock_config: Config
    ) -> None:
        """Malformed or unexpected API JSON format is handled and falls back."""
        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        mock_response_bad = mocker.MagicMock()
        mock_response_bad.status_code = 200
        mock_response_bad.json.return_value = {
            "choices": []  # empty choices list, throws IndexError
        }

        mock_response_success = mocker.MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "feat: fallback success"
                    }
                }
            ],
            "model": FALLBACK_MODELS[0]
        }

        mock_client.post.side_effect = [
            mock_response_bad,
            mock_response_success
        ]

        diff = DiffPayload(
            files=[ChangedFile(path="src/main.py", status="M", staged=True)],
            diff_text="some diff",
            was_truncated=False,
            total_lines=1,
        )

        result = suggest_commit_message(diff, mock_config)

        assert result.ok is True
        assert result.value is not None
        assert result.value.subject == "feat: fallback success"

    def test_fallback_models(self, mocker: MagicMock, mock_config: Config) -> None:
        """If the primary model fails/timeouts, it falls back to other models."""
        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        # Let the first call (primary model) raise a timeout
        # Let the second call (first fallback model) return 200 success
        mock_response_success = mocker.MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "feat: fallback success"
                    }
                }
            ],
            "model": FALLBACK_MODELS[0]
        }

        mock_client.post.side_effect = [
            httpx.TimeoutException("Timeout"),
            mock_response_success
        ]

        diff = DiffPayload(
            files=[ChangedFile(path="src/main.py", status="M", staged=True)],
            diff_text="some diff",
            was_truncated=False,
            total_lines=1,
        )

        result = suggest_commit_message(diff, mock_config)

        assert result.ok is True
        assert result.value is not None
        assert result.value.subject == "feat: fallback success"
        assert result.value.model_used == FALLBACK_MODELS[0]
        # mock_client.post should have been called twice
        assert mock_client.post.call_count == 2

    def test_all_models_fail(self, mocker: MagicMock, mock_config: Config) -> None:
        """If all models fail, suggest_commit_message returns a failure result."""
        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        mock_client.post.side_effect = httpx.TimeoutException("Timeout")

        diff = DiffPayload(
            files=[ChangedFile(path="src/main.py", status="M", staged=True)],
            diff_text="some diff",
            was_truncated=False,
            total_lines=1,
        )

        result = suggest_commit_message(diff, mock_config)

        assert result.ok is False
        assert "All models failed" in (result.error or "")

    def test_logging_enabled(self, mocker: MagicMock, mock_config: Config, tmp_path: Path) -> None:
        """If logging is enabled, requests, responses, and errors are written to files."""
        mock_config.enable_logging = 1
        mocker.patch("aicommit.config._get_config_path", return_value=tmp_path / "config.toml")

        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"dummy": "json"}'
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "feat: log check"
                    }
                }
            ],
            "model": "openai/gpt-4o-mini"
        }
        mock_client.post.return_value = mock_response

        diff = DiffPayload(
            files=[],
            diff_text="",
            was_truncated=False,
            total_lines=0,
        )

        result = suggest_commit_message(diff, mock_config)
        assert result.ok is True

        log_dir = tmp_path / "logs"
        assert log_dir.is_dir()
        log_files = list(log_dir.glob("*.json"))
        assert len(log_files) >= 2 # request + response should be logged

    def test_logging_swallows_exceptions(
        self, mocker: MagicMock, mock_config: Config, tmp_path: Path
    ) -> None:
        """If logging throws an exception, it is swallowed without breaking the application."""
        mock_config.enable_logging = 1
        mocker.patch("aicommit.config._get_config_path", return_value=tmp_path / "config.toml")
        mocker.patch("pathlib.Path.write_text", side_effect=OSError("Write failed"))

        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"dummy": "json"}'
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "feat: log fail check"
                    }
                }
            ],
            "model": "openai/gpt-4o-mini"
        }
        mock_client.post.return_value = mock_response

        diff = DiffPayload(
            files=[],
            diff_text="",
            was_truncated=False,
            total_lines=0,
        )

        result = suggest_commit_message(diff, mock_config)
        assert result.ok is True


class TestSuggestGitignore:
    """Tests for suggest_gitignore()."""

    def test_success(self, mocker: MagicMock, mock_config: Config) -> None:
        """Happy path: returns gitignore suggestion with stripped code fences and comments."""
        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        raw_content = "```gitignore\n# Python\n*.pyc\n__pycache__/\n```"
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": raw_content
                    }
                }
            ],
            "model": "openai/gpt-4o-mini"
        }
        mock_client.post.return_value = mock_response

        result = suggest_gitignore(["src/main.py"], "", mock_config)

        assert result.ok is True
        assert result.value is not None
        # Fences should be stripped
        assert "```" not in result.value.content
        assert "*.pyc" in result.value.content
        # Entries should not include comments or empty lines
        assert "*.pyc" in result.value.entries
        assert "__pycache__/" in result.value.entries
        assert "# Python" not in result.value.entries

    def test_empty_response(self, mocker: MagicMock, mock_config: Config) -> None:
        """If AI returns empty content, try the fallback or fail if all fail."""
        mock_client = mocker.MagicMock()
        mocker.patch("httpx.Client", return_value=mock_client)
        mock_client.__enter__.return_value = mock_client

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": ""
                    }
                }
            ],
            "model": "openai/gpt-4o-mini"
        }
        mock_client.post.return_value = mock_response

        result = suggest_gitignore(["src/main.py"], "", mock_config)

        assert result.ok is False
        assert "All models failed" in (result.error or "")
