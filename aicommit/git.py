"""All git subprocess interactions.

Every subprocess call in the project goes through this module.
No other module may import subprocess.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import PurePosixPath

from aicommit.models import ChangedFile, CommitResult, Config, DiffPayload, Result

LOCK_FILES = frozenset(
    {
        "package-lock.json",
        "poetry.lock",
        "Pipfile.lock",
        "yarn.lock",
        "pnpm-lock.yaml",
        "composer.lock",
        "Gemfile.lock",
        "Cargo.lock",
    }
)


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def get_repo_root(cwd: str) -> Result[str]:
    """Get the root directory of the git repository."""
    proc = _run_git(["rev-parse", "--show-toplevel"], cwd)
    if proc.returncode != 0:
        return Result(ok=False, error=proc.stderr.strip())
    return Result(ok=True, value=proc.stdout.strip())


def _parse_status_line(line: str, staged: bool) -> ChangedFile | None:
    """Parse a single line from git status --porcelain output."""
    if len(line) < 4:
        return None

    if staged:
        status_char = line[0]
    else:
        status_char = line[1]

    if status_char == " ":
        return None

    filepath = line[3:].strip()
    if not filepath:
        return None

    filepath = filepath.replace("\\", "/")

    return ChangedFile(path=filepath, status=status_char, staged=staged)


def detect_changed_files(cwd: str) -> Result[list[ChangedFile]]:
    """Detect all staged and unstaged changed files in the repository.

    Uses git status --porcelain for reliable, parseable output.
    """
    proc = _run_git(["status", "--porcelain"], cwd)
    if proc.returncode != 0:
        return Result(ok=False, error=proc.stderr.strip())

    files: list[ChangedFile] = []
    
    untracked_dirs = []

    for line in proc.stdout.splitlines():
        if not line:
            continue

        # Untracked (??) and ignored (!!) are not staged — treat as unstaged only
        if line.startswith("??") or line.startswith("!!"):
            filepath = line[3:].strip().replace("\\", "/")
            if filepath:
                if filepath.endswith("/"):
                    # It's an untracked directory, we need to list its files
                    untracked_dirs.append(filepath)
                else:
                    files.append(ChangedFile(path=filepath, status="?", staged=False))
            continue

        staged_file = _parse_status_line(line, staged=True)
        if staged_file is not None:
            files.append(staged_file)

        unstaged_file = _parse_status_line(line, staged=False)
        if unstaged_file is not None:
            files.append(unstaged_file)

    # For any untracked directories, use git ls-files --others to recursively find files
    if untracked_dirs:
        # We can run ls-files for the specific directories to avoid scanning the whole repo
        ls_proc = _run_git(["ls-files", "--others", "--exclude-standard", "--", *untracked_dirs], cwd)
        if ls_proc.returncode == 0:
            for line in ls_proc.stdout.splitlines():
                filepath = line.strip().replace("\\", "/")
                if filepath:
                    files.append(ChangedFile(path=filepath, status="?", staged=False))

    return Result(ok=True, value=files)


def _is_binary_diff(diff_text: str) -> bool:
    """Check if a diff output indicates a binary file."""
    return "Binary files" in diff_text and "differ" in diff_text


def _is_lock_file(filepath: str) -> bool:
    """Check if a file is a lock file that should have its diff skipped."""
    filename = PurePosixPath(filepath).name
    return filename in LOCK_FILES


def _truncate_diff(diff_text: str, max_lines: int) -> tuple[str, bool]:
    """Truncate diff to max_lines, returning (text, was_truncated)."""
    lines = diff_text.splitlines()
    if len(lines) <= max_lines:
        return diff_text, False
    truncated = "\n".join(lines[:max_lines])
    truncated += f"\n[diff truncated at {max_lines} lines]"
    return truncated, True


def get_diff_for_files(files: list[ChangedFile], cwd: str, config: Config) -> DiffPayload:
    """Get the combined diff for selected files, with truncation applied."""
    diff_parts: list[str] = []
    was_truncated = False
    total_lines = 0

    for file in files:
        if _is_lock_file(file.path):
            diff_parts.append(f"--- {file.path}\n[lock file updated]")
            total_lines += 2
            continue

        if file.staged:
            proc = _run_git(["diff", "--cached", "--", file.path], cwd)
        else:
            proc = _run_git(["diff", "--", file.path], cwd)

        raw_diff = proc.stdout

        if _is_binary_diff(raw_diff):
            diff_parts.append(f"--- {file.path}\n[binary file]")
            total_lines += 2
            continue

        truncated_diff, file_truncated = _truncate_diff(raw_diff, config.max_diff_lines_per_file)
        if file_truncated:
            was_truncated = True

        diff_parts.append(truncated_diff)
        total_lines += len(truncated_diff.splitlines())

    combined = "\n\n".join(diff_parts)

    if total_lines > config.max_diff_lines_total:
        combined_lines = combined.splitlines()
        combined = "\n".join(combined_lines[: config.max_diff_lines_total])
        combined += "\n[total diff truncated]"
        was_truncated = True
        total_lines = config.max_diff_lines_total

    return DiffPayload(
        files=files,
        diff_text=combined,
        was_truncated=was_truncated,
        total_lines=total_lines,
    )


def _extract_commit_hash(stdout: str) -> str | None:
    """Extract the short commit hash from git commit output."""
    match = re.search(r"\[[\w/.-]+ ([a-f0-9]+)\]", stdout)
    return match.group(1) if match else None


def stage_and_commit(files: list[ChangedFile], message: str, cwd: str) -> CommitResult:
    """Stage the selected files and create a commit."""
    file_paths = [f.path for f in files]
    add_proc = _run_git(["add", "--", *file_paths], cwd)
    if add_proc.returncode != 0:
        return CommitResult(ok=False, commit_hash=None, error=add_proc.stderr.strip())

    commit_proc = _run_git(["commit", "-m", message], cwd)
    if commit_proc.returncode != 0:
        return CommitResult(ok=False, commit_hash=None, error=commit_proc.stderr.strip())

    commit_hash = _extract_commit_hash(commit_proc.stdout)
    return CommitResult(ok=True, commit_hash=commit_hash, error=None)
