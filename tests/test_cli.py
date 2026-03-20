"""Integration tests for the full CLI pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aicommit.cli import main
from aicommit.models import (
    ChangedFile,
    CommitResult,
    CommitSuggestion,
    Config,
    DiffPayload,
    Result,
)


@pytest.fixture
def patch_all(mocker: MagicMock, mock_config: Config) -> dict[str, MagicMock]:
    """Patch all external dependencies for CLI integration tests."""
    changed_files = [
        ChangedFile(path="src/main.py", status="M", staged=True),
    ]
    diff_payload = DiffPayload(
        files=changed_files,
        diff_text="diff --git a/src/main.py\n+hello\n",
        was_truncated=False,
        total_lines=2,
    )
    suggestion = CommitSuggestion(
        message="feat: add feature",
        subject="feat: add feature",
        body=None,
        model_used="openai/gpt-4o-mini",
    )
    commit_result = CommitResult(ok=True, commit_hash="abc1234", error=None)

    patches = {
        "load_config": mocker.patch("aicommit.cli.load_config", return_value=mock_config),
        "get_repo_root": mocker.patch(
            "aicommit.cli.get_repo_root",
            return_value=Result(ok=True, value="/repo"),
        ),
        "detect_changed_files": mocker.patch(
            "aicommit.cli.detect_changed_files",
            return_value=Result(ok=True, value=changed_files),
        ),
        "prompt_file_selection": mocker.patch(
            "aicommit.cli.prompt_file_selection",
            return_value=changed_files,
        ),
        "get_diff_for_files": mocker.patch(
            "aicommit.cli.get_diff_for_files",
            return_value=diff_payload,
        ),
        "suggest_commit_message": mocker.patch(
            "aicommit.cli.suggest_commit_message",
            return_value=Result(ok=True, value=suggestion),
        ),
        "prompt_edit_and_confirm": mocker.patch(
            "aicommit.cli.prompt_edit_and_confirm",
            return_value="feat: add feature",
        ),
        "stage_and_commit": mocker.patch(
            "aicommit.cli.stage_and_commit",
            return_value=commit_result,
        ),
        "display_success": mocker.patch("aicommit.cli.display_success"),
        "display_error": mocker.patch("aicommit.cli.display_error"),
        "prompt_continue": mocker.patch("aicommit.cli.prompt_continue", return_value=False),
    }
    return patches


class TestMainHappyPath:
    """Tests for the happy path through the CLI pipeline."""

    def test_full_pipeline_success(self, patch_all: dict[str, MagicMock]) -> None:
        """Happy path: full pipeline from detect to commit."""
        exit_code = main([])

        assert exit_code == 0
        patch_all["load_config"].assert_called_once()
        patch_all["get_repo_root"].assert_called_once()
        patch_all["detect_changed_files"].assert_called_once()
        patch_all["prompt_file_selection"].assert_called_once()
        patch_all["get_diff_for_files"].assert_called_once()
        patch_all["suggest_commit_message"].assert_called_once()
        patch_all["prompt_edit_and_confirm"].assert_called_once()
        patch_all["stage_and_commit"].assert_called_once()
        patch_all["display_success"].assert_called_once()
        patch_all["prompt_continue"].assert_called_once()

    def test_pipeline_loops_on_continue(self, patch_all: dict[str, MagicMock]) -> None:
        """Pipeline loops if user chooses to continue."""
        patch_all["prompt_continue"].side_effect = [True, False]

        exit_code = main([])

        assert exit_code == 0
        assert patch_all["detect_changed_files"].call_count == 2
        assert patch_all["prompt_file_selection"].call_count == 2

    def test_dry_run_skips_commit(self, patch_all: dict[str, MagicMock]) -> None:
        """--dry-run runs the pipeline but skips the actual commit."""
        exit_code = main(["--dry-run"])

        assert exit_code == 0
        patch_all["stage_and_commit"].assert_not_called()


class TestMainErrorPaths:
    """Tests for error paths in the CLI pipeline."""

    def test_not_a_git_repo(self, patch_all: dict[str, MagicMock]) -> None:
        """Error: not a git repo → exit 1."""
        patch_all["get_repo_root"].return_value = Result(
            ok=False, error="fatal: not a git repository"
        )

        exit_code = main([])

        assert exit_code == 1
        patch_all["display_error"].assert_called_once()

    def test_no_changes(self, patch_all: dict[str, MagicMock]) -> None:
        """Error: no changes in the repo → exit 1."""
        patch_all["detect_changed_files"].return_value = Result(ok=True, value=[])

        exit_code = main([])

        assert exit_code == 1

    def test_no_files_selected(self, patch_all: dict[str, MagicMock]) -> None:
        """Error: user selected no files → exit 1."""
        patch_all["prompt_file_selection"].return_value = []

        exit_code = main([])

        assert exit_code == 1

    def test_ai_failure(self, patch_all: dict[str, MagicMock]) -> None:
        """Error: AI API returns an error → exit 1."""
        patch_all["suggest_commit_message"].return_value = Result(ok=False, error="API timeout")

        exit_code = main([])

        assert exit_code == 1
        patch_all["display_error"].assert_called_once()

    def test_user_abort(self, patch_all: dict[str, MagicMock]) -> None:
        """User aborts at the confirm step → exit 1."""
        patch_all["prompt_edit_and_confirm"].return_value = None

        exit_code = main([])

        assert exit_code == 1

    def test_commit_failure(self, patch_all: dict[str, MagicMock]) -> None:
        """Error: git commit fails → exit 1."""
        patch_all["stage_and_commit"].return_value = CommitResult(
            ok=False, commit_hash=None, error="nothing to commit"
        )

        exit_code = main([])

        assert exit_code == 1

    def test_config_error_exits_1(self, patch_all: dict[str, MagicMock]) -> None:
        """Error: config loading fails → exit 1."""
        from aicommit.exceptions import ConfigError

        patch_all["load_config"].side_effect = ConfigError("API key not found")

        exit_code = main([])

        assert exit_code == 1


class TestMainFlags:
    """Tests for CLI flags."""

    def test_verbose_flag_does_not_crash(self, patch_all: dict[str, MagicMock]) -> None:
        """--verbose flag runs without crashing."""
        exit_code = main(["--verbose"])

        assert exit_code == 0

    def test_model_override(self, patch_all: dict[str, MagicMock]) -> None:
        """--model flag overrides config model."""
        exit_code = main(["--model", "anthropic/claude-3"])

        assert exit_code == 0

    def test_no_edit_skips_edit_prompt(self, patch_all: dict[str, MagicMock]) -> None:
        """--no-edit skips the edit+confirm prompt and uses AI suggestion directly."""
        exit_code = main(["--no-edit"])

        assert exit_code == 0
        patch_all["prompt_edit_and_confirm"].assert_not_called()
