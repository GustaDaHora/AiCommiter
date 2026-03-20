"""Unit tests for aicommit.git module."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest

from aicommit.git import (
    detect_changed_files,
    get_diff_for_files,
    get_repo_root,
    stage_and_commit,
)
from aicommit.models import ChangedFile, Config


@pytest.fixture
def mock_subprocess(mocker: MagicMock) -> MagicMock:
    """Patch subprocess.run in the git module."""
    return cast(MagicMock, mocker.patch("aicommit.git.subprocess.run"))


class TestGetRepoRoot:
    """Tests for get_repo_root()."""

    def test_returns_repo_root_path(self, mock_subprocess: MagicMock) -> None:
        """Happy path: git rev-parse returns the repo root."""
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="/home/user/myrepo\n",
            stderr="",
        )
        result = get_repo_root("/home/user/myrepo/src")

        assert result.ok is True
        assert result.value == "/home/user/myrepo"

    def test_returns_error_when_not_a_repo(self, mock_subprocess: MagicMock) -> None:
        """Error path: not a git repository."""
        mock_subprocess.return_value = MagicMock(
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        )
        result = get_repo_root("/tmp/not-a-repo")

        assert result.ok is False
        assert "not a git repository" in (result.error or "")


class TestDetectChangedFiles:
    """Tests for detect_changed_files()."""

    def test_returns_staged_and_unstaged_files(self, mock_subprocess: MagicMock) -> None:
        """Happy path: detects both staged and unstaged modified files.

        git status --porcelain format: XY filename
        X = staged status, Y = unstaged status
        """
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="M  src/main.py\nA  src/new.py\n M src/utils.py\n?? untracked.txt\n",
            stderr="",
        )
        result = detect_changed_files("/repo")

        assert result.ok is True
        files = result.value
        assert files is not None

        staged = [f for f in files if f.staged]
        unstaged = [f for f in files if not f.staged]
        assert len(staged) == 2
        assert len(unstaged) == 2
        assert any(f.path == "src/main.py" and f.status == "M" for f in staged)
        assert any(f.path == "src/new.py" and f.status == "A" for f in staged)
        assert any(f.path == "src/utils.py" and f.status == "M" for f in unstaged)
        assert any(f.path == "untracked.txt" and f.status == "?" for f in unstaged)

    def test_returns_empty_when_no_changes(self, mock_subprocess: MagicMock) -> None:
        """Edge case: no changes in the working tree or index."""
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = detect_changed_files("/repo")

        assert result.ok is True
        assert result.value == []

    def test_returns_error_when_not_a_git_repo(self, mock_subprocess: MagicMock) -> None:
        """Error path: subprocess returns non-zero because not a repo."""
        mock_subprocess.return_value = MagicMock(
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        )
        result = detect_changed_files("/not-a-repo")

        assert result.ok is False
        assert result.error is not None

    def test_handles_both_staged_and_unstaged_on_same_file(
        self, mock_subprocess: MagicMock
    ) -> None:
        """A file can be both staged AND have unstaged changes (MM)."""
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="MM src/both.py\n",
            stderr="",
        )
        result = detect_changed_files("/repo")

        assert result.ok is True
        files = result.value
        assert files is not None
        assert len(files) == 2
        assert any(f.staged and f.path == "src/both.py" for f in files)
        assert any(not f.staged and f.path == "src/both.py" for f in files)


class TestGetDiffForFiles:
    """Tests for get_diff_for_files()."""

    def test_returns_combined_diff(self, mock_subprocess: MagicMock, mock_config: Config) -> None:
        """Happy path: returns combined diff for selected files."""
        files = [
            ChangedFile(path="src/main.py", status="M", staged=True),
            ChangedFile(path="src/utils.py", status="M", staged=False),
        ]
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="diff --git a/src/main.py\n+hello\n", stderr=""),
            MagicMock(returncode=0, stdout="diff --git a/src/utils.py\n+world\n", stderr=""),
        ]

        payload = get_diff_for_files(files, "/repo", mock_config)

        assert len(payload.files) == 2
        assert "hello" in payload.diff_text
        assert "world" in payload.diff_text
        assert payload.was_truncated is False

    def test_uses_cached_diff_for_staged_files(
        self, mock_subprocess: MagicMock, mock_config: Config
    ) -> None:
        """Staged files use git diff --cached to get the indexed diff."""
        files = [ChangedFile(path="staged.py", status="M", staged=True)]
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="diff content\n", stderr="")

        get_diff_for_files(files, "/repo", mock_config)

        call_args = mock_subprocess.call_args[0][0]
        assert "--cached" in call_args

    def test_uses_regular_diff_for_unstaged_files(
        self, mock_subprocess: MagicMock, mock_config: Config
    ) -> None:
        """Unstaged files use git diff (no --cached) for working tree diff."""
        files = [ChangedFile(path="unstaged.py", status="M", staged=False)]
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="diff content\n", stderr="")

        get_diff_for_files(files, "/repo", mock_config)

        call_args = mock_subprocess.call_args[0][0]
        assert "--cached" not in call_args

    def test_truncates_per_file_diff(
        self, mock_subprocess: MagicMock, mock_config: Config
    ) -> None:
        """Truncation: per-file diff exceeding max_diff_lines_per_file is truncated."""
        mock_config.max_diff_lines_per_file = 5
        files = [ChangedFile(path="big.py", status="M", staged=True)]
        long_diff = "\n".join([f"+line {i}" for i in range(20)])
        mock_subprocess.return_value = MagicMock(returncode=0, stdout=long_diff, stderr="")

        payload = get_diff_for_files(files, "/repo", mock_config)

        assert payload.was_truncated is True
        assert "[diff truncated" in payload.diff_text

    def test_truncates_total_diff(self, mock_subprocess: MagicMock, mock_config: Config) -> None:
        """Truncation: total combined diff exceeding max_diff_lines_total is truncated."""
        mock_config.max_diff_lines_total = 10
        mock_config.max_diff_lines_per_file = 500
        files = [
            ChangedFile(path="a.py", status="M", staged=True),
            ChangedFile(path="b.py", status="M", staged=True),
        ]
        diff_a = "\n".join([f"+line {i}" for i in range(8)])
        diff_b = "\n".join([f"+line {i}" for i in range(8)])
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout=diff_a, stderr=""),
            MagicMock(returncode=0, stdout=diff_b, stderr=""),
        ]

        payload = get_diff_for_files(files, "/repo", mock_config)

        assert payload.was_truncated is True
        assert "[total diff truncated]" in payload.diff_text

    def test_skips_binary_files(self, mock_subprocess: MagicMock, mock_config: Config) -> None:
        """Binary files: diff content is skipped, name is included."""
        files = [ChangedFile(path="image.png", status="M", staged=True)]
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="Binary files a/image.png and b/image.png differ\n",
            stderr="",
        )

        payload = get_diff_for_files(files, "/repo", mock_config)

        assert "[binary file]" in payload.diff_text
        assert payload.was_truncated is False

    def test_skips_lock_file_content(
        self, mock_subprocess: MagicMock, mock_config: Config
    ) -> None:
        """Lock files: diff content is replaced with placeholder."""
        files = [ChangedFile(path="package-lock.json", status="M", staged=True)]

        payload = get_diff_for_files(files, "/repo", mock_config)

        assert "[lock file updated]" in payload.diff_text
        mock_subprocess.assert_not_called()


class TestStageAndCommit:
    """Tests for stage_and_commit()."""

    def test_success_path(self, mock_subprocess: MagicMock) -> None:
        """Happy path: git add + git commit succeed."""
        files = [ChangedFile(path="src/main.py", status="M", staged=True)]
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(
                returncode=0,
                stdout="[main abc1234] feat: add feature\n 1 file changed\n",
                stderr="",
            ),
        ]

        result = stage_and_commit(files, "feat: add feature", "/repo")

        assert result.ok is True
        assert result.commit_hash is not None

    def test_failure_on_git_add(self, mock_subprocess: MagicMock) -> None:
        """Error path: git add fails."""
        files = [ChangedFile(path="src/main.py", status="M", staged=True)]
        mock_subprocess.return_value = MagicMock(
            returncode=1, stdout="", stderr="error: pathspec 'src/main.py' did not match"
        )

        result = stage_and_commit(files, "feat: test", "/repo")

        assert result.ok is False
        assert result.error is not None

    def test_failure_on_commit(self, mock_subprocess: MagicMock) -> None:
        """Error path: git commit fails."""
        files = [ChangedFile(path="src/main.py", status="M", staged=True)]
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="nothing to commit"),
        ]

        result = stage_and_commit(files, "feat: nothing", "/repo")

        assert result.ok is False
        assert result.error is not None
