"""Unit tests for aicommit.git module."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from aicommit.git import (
    detect_changed_files,
    get_diff_for_files,
    get_repo_root,
    list_all_files,
    read_gitignore,
    stage_and_commit,
    write_gitignore,
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
        """Happy path: detects both staged and unstaged modified files."""
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


class TestListAllFiles:
    """Tests for list_all_files().

    The function combines three sources:
    - git ls-files --cached           (tracked files)
    - git ls-files --others           (untracked, NO .gitignore filter)
    - os.walk over the filesystem     (catches everything else)

    Heavy directories are collapsed to a placeholder ("node_modules/").
    """

    def test_returns_sorted_union_of_all_sources(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """Happy path: git sources + filesystem walk all merged and sorted."""
        # Filesystem: two real files
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")
        (tmp_path / "README.md").write_text("")
        # git .git dir should be skipped automatically
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("")

        mock_subprocess.side_effect = [
            # git ls-files --cached
            MagicMock(returncode=0, stdout="src/main.py\nREADME.md\n", stderr=""),
            # git ls-files --others --no-exclude-standard
            MagicMock(returncode=0, stdout=".env\n", stderr=""),
        ]

        # .env does not exist on disk but git reports it — still included
        result = list_all_files(str(tmp_path))

        assert result.ok is True
        files = result.value
        assert files is not None
        assert "src/main.py" in files
        assert "README.md" in files
        assert ".env" in files
        assert files == sorted(set(files))  # sorted, no dupes

    def test_does_not_use_exclude_standard_for_untracked(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """git ls-files --others is called WITHOUT --exclude-standard.

        --exclude-standard would apply the current .gitignore as a filter,
        hiding already-ignored files from the AI — exactly the opposite of
        what we need. The fix is to omit the flag entirely: git ls-files
        --others (no flag) returns all untracked files with no filtering.
        """
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        list_all_files(str(tmp_path))

        calls = [call[0][0] for call in mock_subprocess.call_args_list]
        others_call = next(c for c in calls if "--others" in c)
        assert "--exclude-standard" not in others_call

    def test_git_ignored_files_visible_via_no_exclude_standard(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """Files already in .gitignore appear in the result (no filter applied)."""
        (tmp_path / ".env").write_text("SECRET=abc")
        (tmp_path / ".gitignore").write_text(".env\n")

        mock_subprocess.side_effect = [
            # .env is NOT tracked (not in --cached)
            MagicMock(returncode=0, stdout=".gitignore\n", stderr=""),
            # but --no-exclude-standard makes git report it as untracked
            MagicMock(returncode=0, stdout=".env\n", stderr=""),
        ]

        result = list_all_files(str(tmp_path))

        assert result.ok is True
        assert result.value is not None
        assert ".env" in result.value

    def test_collapses_heavy_directories_to_placeholder(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """node_modules/ content is not listed — only the directory placeholder."""
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "lodash").mkdir()
        (nm / "lodash" / "index.js").write_text("")
        (nm / "react").mkdir()
        (nm / "react" / "index.js").write_text("")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")

        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="src/main.py\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        result = list_all_files(str(tmp_path))

        assert result.ok is True
        files = result.value
        assert files is not None
        assert "node_modules/" in files
        assert not any("lodash" in f for f in files)
        assert not any("react" in f for f in files)

    def test_collapses_venv_directory(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """Virtual environment directories are collapsed to a placeholder."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("")
        (venv / "lib").mkdir()

        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        result = list_all_files(str(tmp_path))

        assert result.ok is True
        files = result.value
        assert files is not None
        assert ".venv/" in files
        assert not any("pyvenv.cfg" in f for f in files)

    def test_skips_git_directory_entirely(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """.git/ directory and its contents never appear in results."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main")
        (tmp_path / "README.md").write_text("")

        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="README.md\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        result = list_all_files(str(tmp_path))

        assert result.ok is True
        files = result.value
        assert files is not None
        assert not any(".git" in f for f in files)

    def test_deduplicates_files_across_all_sources(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """A file reported by multiple sources appears exactly once."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")

        mock_subprocess.side_effect = [
            # same file in both git sources
            MagicMock(returncode=0, stdout="src/main.py\n", stderr=""),
            MagicMock(returncode=0, stdout="src/main.py\n", stderr=""),
        ]

        result = list_all_files(str(tmp_path))

        assert result.ok is True
        files = result.value
        assert files is not None
        assert files.count("src/main.py") == 1

    def test_returns_empty_list_for_empty_repo(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """Edge case: no files at all."""
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        result = list_all_files(str(tmp_path))

        assert result.ok is True
        assert result.value == []

    def test_returns_error_when_cached_command_fails(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """Error path: git ls-files --cached returns non-zero."""
        mock_subprocess.side_effect = [
            MagicMock(returncode=128, stdout="", stderr="fatal: not a git repository"),
        ]

        result = list_all_files(str(tmp_path))

        assert result.ok is False
        assert "not a git repository" in (result.error or "")

    def test_returns_error_when_others_command_fails(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """Error path: git ls-files --others returns non-zero."""
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="src/main.py\n", stderr=""),
            MagicMock(returncode=128, stdout="", stderr="fatal: not a git repository"),
        ]

        result = list_all_files(str(tmp_path))

        assert result.ok is False
        assert result.error is not None

    def test_strips_whitespace_from_git_paths(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """Paths with leading/trailing whitespace from git output are stripped."""
        (tmp_path / "README.md").write_text("")

        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="  src/main.py  \n  README.md\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        result = list_all_files(str(tmp_path))

        assert result.ok is True
        assert result.value is not None
        assert "src/main.py" in result.value
        assert "README.md" in result.value
        assert not any(p.startswith(" ") or p.endswith(" ") for p in result.value)

    def test_result_is_sorted(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """Output list is always sorted alphabetically."""
        (tmp_path / "z_last.py").write_text("")
        (tmp_path / "a_first.py").write_text("")

        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="z_last.py\na_first.py\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        result = list_all_files(str(tmp_path))

        assert result.ok is True
        files = result.value
        assert files is not None
        assert files == sorted(files)


class TestReadGitignore:
    """Tests for read_gitignore()."""

    def test_reads_existing_gitignore(self, tmp_path: Path) -> None:
        """Happy path: reads the .gitignore file content."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n__pycache__/\n", encoding="utf-8")

        content = read_gitignore(str(tmp_path))

        assert "*.pyc" in content
        assert "__pycache__/" in content

    def test_returns_empty_string_when_file_missing(self, tmp_path: Path) -> None:
        """Edge case: no .gitignore file — returns empty string without error."""
        content = read_gitignore(str(tmp_path))

        assert content == ""


class TestWriteGitignore:
    """Tests for write_gitignore()."""

    def test_creates_new_gitignore(self, tmp_path: Path) -> None:
        """Happy path: creates .gitignore when it does not exist."""
        result = write_gitignore(str(tmp_path), "*.pyc\n.venv/\n")

        assert result.ok is True
        written = (tmp_path / ".gitignore").read_text(encoding="utf-8")
        assert "*.pyc" in written
        assert ".venv/" in written

    def test_overwrites_existing_gitignore(self, tmp_path: Path) -> None:
        """Overwrites an existing .gitignore with new content."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("old content\n", encoding="utf-8")

        result = write_gitignore(str(tmp_path), "new content\n")

        assert result.ok is True
        written = gitignore.read_text(encoding="utf-8")
        assert "new content" in written
        assert "old content" not in written

    def test_returns_error_on_permission_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error path: write fails due to OS error."""
        def _raise(*args: object, **kwargs: object) -> None:
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "write_text", _raise)

        result = write_gitignore(str(tmp_path), "*.pyc\n")

        assert result.ok is False
        assert result.error is not None


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