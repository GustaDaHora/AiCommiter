"""Pure dataclasses for data transfer between modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Result(Generic[T]):
    """Generic result wrapper for operations that can fail in expected ways."""

    ok: bool
    value: T | None = None
    error: str | None = None


@dataclass
class ChangedFile:
    """A single file with changes in the working tree or index."""

    path: str
    status: str
    staged: bool


@dataclass
class DiffPayload:
    """Combined diff content ready to send to the AI, after truncation."""

    files: list[ChangedFile]
    diff_text: str
    was_truncated: bool
    total_lines: int


@dataclass
class CommitSuggestion:
    """AI-generated commit message, parsed and validated."""

    message: str
    subject: str
    body: str | None
    model_used: str


@dataclass
class Config:
    """Typed, validated configuration from config.toml and environment variables."""

    api_key: str
    model: str
    base_url: str
    max_diff_lines_per_file: int
    max_diff_lines_total: int
    editor: str | None
    enable_logging: int


@dataclass
class CommitResult:
    """Outcome of git add + git commit."""

    ok: bool
    commit_hash: str | None
    error: str | None


@dataclass
class GitignoreSuggestion:
    """AI-generated .gitignore content, fully reorganized."""

    content: str
    # Flat list of patterns added/kept, for display purposes
    entries: list[str]
    model_used: str
