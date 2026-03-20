"""Shared test fixtures for aicommit tests."""

from __future__ import annotations

import pytest

from aicommit.models import ChangedFile, Config


@pytest.fixture
def mock_config() -> Config:
    """Return a Config instance with test defaults."""
    return Config(
        api_key="sk-test-key-12345",
        model="openai/gpt-4o-mini",
        base_url="https://openrouter.ai/api/v1",
        max_diff_lines_per_file=500,
        max_diff_lines_total=2000,
        editor=None,
        enable_logging=0,
    )


@pytest.fixture
def sample_changed_files() -> list[ChangedFile]:
    """Return a list of sample ChangedFile instances for testing."""
    return [
        ChangedFile(path="src/main.py", status="M", staged=True),
        ChangedFile(path="src/utils.py", status="A", staged=False),
        ChangedFile(path="README.md", status="M", staged=True),
    ]
