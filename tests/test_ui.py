"""Unit tests for aicommit.ui module."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest

from aicommit.models import ChangedFile, CommitResult, CommitSuggestion, Config
from aicommit.ui import (
    display_error,
    display_success,
    prompt_edit_and_confirm,
    prompt_file_selection,
)


@pytest.fixture
def mock_questionary(mocker: MagicMock) -> MagicMock:
    """Patch questionary in the ui module."""
    return cast(MagicMock, mocker.patch("aicommit.ui.questionary"))


@pytest.fixture
def mock_console(mocker: MagicMock) -> MagicMock:
    """Patch the Console instance in the ui module."""
    return cast(MagicMock, mocker.patch("aicommit.ui._console"))


@pytest.fixture
def mock_err_console(mocker: MagicMock) -> MagicMock:
    """Patch the stderr Console instance in the ui module."""
    return cast(MagicMock, mocker.patch("aicommit.ui._err_console"))


class TestPromptFileSelection:
    """Tests for prompt_file_selection()."""

    def test_returns_selected_files(
        self,
        mock_questionary: MagicMock,
        sample_changed_files: list[ChangedFile],
    ) -> None:
        """Happy path: user selects a subset of files."""
        # files are: [src/main.py, src/utils.py, README.md]
        # src/main.py is index 0, README.md is index 2
        selected_values = ["file:0", "file:2"]
        mock_questionary.checkbox.return_value.ask.return_value = selected_values

        result = prompt_file_selection(sample_changed_files)

        assert len(result) == 2
        assert all(f.path in ["src/main.py", "README.md"] for f in result)

    def test_folder_selection_includes_all_children(
        self,
        mock_questionary: MagicMock,
        sample_changed_files: list[ChangedFile],
    ) -> None:
        """User selects a folder, all child files are included recursively."""
        # user selects the src directory, this implies dir:src 
        mock_questionary.checkbox.return_value.ask.return_value = ["dir:src"]

        result = prompt_file_selection(sample_changed_files)

        assert len(result) == 2
        assert all(f.path in ["src/main.py", "src/utils.py"] for f in result)

    def test_returns_empty_when_user_selects_none(
        self,
        mock_questionary: MagicMock,
        sample_changed_files: list[ChangedFile],
    ) -> None:
        """Edge case: user deselects all files."""
        mock_questionary.checkbox.return_value.ask.return_value = []

        result = prompt_file_selection(sample_changed_files)

        assert result == []

    def test_returns_empty_when_user_cancels(
        self,
        mock_questionary: MagicMock,
        sample_changed_files: list[ChangedFile],
    ) -> None:
        """Edge case: user presses Ctrl+C (questionary returns None)."""
        mock_questionary.checkbox.return_value.ask.return_value = None

        result = prompt_file_selection(sample_changed_files)

        assert result == []


class TestPromptEditAndConfirm:
    """Tests for prompt_edit_and_confirm()."""

    def test_user_accepts_suggestion(
        self,
        mock_questionary: MagicMock,
        mock_console: MagicMock,
        mock_config: Config,
    ) -> None:
        """Happy path: user confirms the suggestion without editing."""
        suggestion = CommitSuggestion(
            message="feat: add feature",
            subject="feat: add feature",
            body=None,
            model_used="openai/gpt-4o-mini",
        )
        mock_questionary.select.return_value.ask.return_value = "accept"

        result = prompt_edit_and_confirm(suggestion, mock_config)

        assert result == "feat: add feature"

    def test_user_aborts(
        self,
        mock_questionary: MagicMock,
        mock_console: MagicMock,
        mock_config: Config,
    ) -> None:
        """User chooses to abort the commit."""
        suggestion = CommitSuggestion(
            message="feat: add feature",
            subject="feat: add feature",
            body=None,
            model_used="openai/gpt-4o-mini",
        )
        mock_questionary.select.return_value.ask.return_value = "abort"

        result = prompt_edit_and_confirm(suggestion, mock_config)

        assert result is None

    def test_user_edits_message(
        self,
        mock_questionary: MagicMock,
        mock_console: MagicMock,
        mock_config: Config,
    ) -> None:
        """User chooses to edit the commit message."""
        suggestion = CommitSuggestion(
            message="feat: add feature",
            subject="feat: add feature",
            body=None,
            model_used="openai/gpt-4o-mini",
        )
        mock_questionary.select.return_value.ask.return_value = "edit"
        mock_questionary.text.return_value.ask.return_value = "fix: corrected feature"

        result = prompt_edit_and_confirm(suggestion, mock_config)

        assert result == "fix: corrected feature"

    def test_user_cancels_returns_none(
        self,
        mock_questionary: MagicMock,
        mock_console: MagicMock,
        mock_config: Config,
    ) -> None:
        """User presses Ctrl+C returns None."""
        suggestion = CommitSuggestion(
            message="feat: add feature",
            subject="feat: add feature",
            body=None,
            model_used="openai/gpt-4o-mini",
        )
        mock_questionary.select.return_value.ask.return_value = None

        result = prompt_edit_and_confirm(suggestion, mock_config)

        assert result is None


class TestDisplayError:
    """Tests for display_error()."""

    def test_does_not_crash(self, mock_err_console: MagicMock) -> None:
        """display_error renders without crashing."""
        display_error("Something went wrong")
        mock_err_console.print.assert_called_once()

    def test_includes_message(self, mock_err_console: MagicMock) -> None:
        """Error message is included in the output."""
        display_error("test error message")
        call_args = str(mock_err_console.print.call_args)
        assert "test error message" in call_args


class TestDisplaySuccess:
    """Tests for display_success()."""

    def test_does_not_crash(self, mock_console: MagicMock) -> None:
        """display_success renders without crashing."""
        result = CommitResult(ok=True, commit_hash="abc1234", error=None)
        display_success(result)
        mock_console.print.assert_called()

class TestPromptContinue:
    """Tests for prompt_continue()."""

    def test_user_confirms_continue(
        self,
        mock_questionary: MagicMock,
        mock_console: MagicMock,
    ) -> None:
        """User confirms they want to continue."""
        mock_questionary.confirm.return_value.ask.return_value = True

        from aicommit.ui import prompt_continue
        result = prompt_continue()

        assert result is True

    def test_user_declines_continue(
        self,
        mock_questionary: MagicMock,
        mock_console: MagicMock,
    ) -> None:
        """User declines they want to continue."""
        mock_questionary.confirm.return_value.ask.return_value = False

        from aicommit.ui import prompt_continue
        result = prompt_continue()

        assert result is False
