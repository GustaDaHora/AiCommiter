# agents.md — AI Agent Onboarding Document

# Project: aicommiter

> [!important]
> This document is the **source of truth** for any AI agent working in this project.
> Read it in full before writing any code. Every section contains constraints that are
> non-negotiable. When in doubt, stop and ask the human rather than guess.

---

## 0. How to Use This Document

This file is your persistent memory for this project. You have no context between sessions —
this document compensates for that. It tells you:

- What the project does and how it is structured
- Where every type of code belongs
- What you are explicitly forbidden to do
- How to implement features (TDD workflow)
- Which actions require human approval before execution
- The shape of every important internal data structure and module

**Do not skip sections.** Every section contains constraints that affect your output.

---

## 1. Project Overview

### 1.1 Purpose

`aicommit` is a cross-platform interactive CLI tool that assists developers in writing
high-quality git commit messages. When invoked inside any git repository, it:

1. Detects all staged and unstaged changed files via `git diff`
2. Presents an interactive file selector so the user picks which files to include
3. Sends the combined diff of selected files to an AI model via OpenRouter
4. Receives a commit message suggestion following Conventional Commits best practices
5. Presents the suggestion to the user with an inline editor for modifications
6. Executes `git add <selected files>` + `git commit -m "<message>"` upon confirmation

The tool is a developer productivity utility — not a service, not a web app. It has no
persistent state beyond a user-level config file. It runs entirely in the terminal.

### 1.2 Entry Point

**Global CLI command:** `aicommit`

Registered via `pyproject.toml` as a console script entry point:

```toml
[project.scripts]
aicommit = "aicommit.cli:main"
```

Invoked by the user running `aicommit` from inside any git repository on their machine.
The tool detects the current working directory and runs `git` commands relative to it.

### 1.3 Architectural Pattern

**Pattern:** Single-package Python CLI (Modular Monolith)

The project is a single installable Python package (`aicommit/`) with clearly separated
internal modules. There are no microservices, no background workers, no database, and no
network server. All execution is synchronous and triggered by the user.

The internal architecture follows a **pipeline pattern**:

```
CLI entry → Git module → AI module → UI module → Git module → Exit
```

Each module has a single responsibility and communicates via plain Python dataclasses.
No module imports from another module's internals — only from its public interface.

### 1.4 High-Level Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  User runs: aicommit                                            │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  git.py — detect_changed_files()                                │
│  Runs: git diff --name-only HEAD                                │
│  Runs: git diff --name-only (unstaged)                          │
│  Returns: List[ChangedFile]                                     │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  ui.py — prompt_file_selection()                                │
│  Shows: interactive checkbox list (questionary)                 │
│  Returns: List[ChangedFile] (user-selected subset)              │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  git.py — get_diff_for_files()                                  │
│  Runs: git diff HEAD -- <file> for each selected file           │
│  Returns: DiffPayload (combined diff string + metadata)         │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  ai.py — suggest_commit_message()                               │
│  Sends diff to OpenRouter via httpx                             │
│  Returns: CommitSuggestion                                      │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  ui.py — prompt_edit_and_confirm()                              │
│  Shows suggestion, opens $EDITOR if user wants to edit          │
│  Returns: final commit message string or None (abort)           │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  git.py — stage_and_commit()                                    │
│  Runs: git add <selected files>                                 │
│  Runs: git commit -m "<message>"                                │
│  Returns: CommitResult                                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Tech Stack

> [!warning]
> Always use the exact versions listed here. Never upgrade a dependency without explicit
> human instruction. If a version is missing, ask before assuming.

### 2.1 Languages & Runtimes

| Component | Technology | Version | Notes                                                       |
| --------- | ---------- | ------- | ----------------------------------------------------------- |
| Language  | Python     | 3.11+   | f-strings, `tomllib` (built-in), `match` statements allowed |
| Runtime   | CPython    | 3.11+   | No PyPy, no Cython                                          |

### 2.2 Package & Distribution

| Role            | Technology                           | Version | Notes                              |
| --------------- | ------------------------------------ | ------- | ---------------------------------- |
| Build backend   | `hatchling`                          | latest  | via `pyproject.toml`               |
| Package manager | `pip`                                | bundled | `pip install -e .` for dev install |
| Entry point     | `pyproject.toml` `[project.scripts]` | —       | Registers `aicommit` globally      |

### 2.3 Dependencies

| Role                | Library               | Version  | Purpose                                              |
| ------------------- | --------------------- | -------- | ---------------------------------------------------- |
| Interactive TUI     | `questionary`         | `^2.0`   | Checkbox file selector, confirm prompts, inline edit |
| HTTP client         | `httpx`               | `^0.27`  | Sync HTTP calls to OpenRouter API                    |
| Terminal formatting | `rich`                | `^13.0`  | Panels, spinners, colored diff display               |
| Config file         | `tomllib` (stdlib)    | built-in | Read `~/.config/aicommit/config.toml`                |
| Subprocess          | `subprocess` (stdlib) | built-in | Run all `git` commands                               |

### 2.4 Dev Dependencies

| Role         | Library       | Version | Purpose                                        |
| ------------ | ------------- | ------- | ---------------------------------------------- |
| Test runner  | `pytest`      | `^8.0`  | All tests                                      |
| Mocking      | `pytest-mock` | `^3.14` | Mock subprocess, httpx                         |
| Coverage     | `pytest-cov`  | `^5.0`  | Coverage reporting                             |
| Linter       | `ruff`        | `^0.4`  | Linting + formatting (replaces black + flake8) |
| Type checker | `mypy`        | `^1.10` | Static type checking                           |

### 2.5 External Services & APIs

| Service    | Purpose            | Integration                                                     |
| ---------- | ------------------ | --------------------------------------------------------------- |
| OpenRouter | AI model inference | `httpx` POST to `https://openrouter.ai/api/v1/chat/completions` |

OpenRouter is OpenAI-compatible. The request body follows the OpenAI Chat Completions
format. The model is user-configurable in `config.toml`.

---

## 3. Repository Structure

### 3.1 Directory Map

```
aicommit/
├── pyproject.toml           # Package metadata, dependencies, entry points, tool config
├── README.md                # User-facing documentation
├── agents.md                 # This file — agent onboarding document
├── .env.example             # Template for local secrets (never commit .env)
├── aicommit/                # Main Python package
│   ├── __init__.py          # Package version only — no logic
│   ├── cli.py               # Entry point: parses args, orchestrates the pipeline
│   ├── git.py               # All git subprocess interactions
│   ├── ai.py                # All OpenRouter API interactions
│   ├── ui.py                # All terminal UI interactions (questionary + rich)
│   ├── config.py            # Config file loading and validation
│   ├── models.py            # Dataclasses: ChangedFile, DiffPayload, CommitSuggestion, etc.
│   └── exceptions.py        # Custom exception classes
└── tests/
    ├── conftest.py          # Shared fixtures (mock config, mock subprocess, etc.)
    ├── test_git.py          # Unit tests for git.py
    ├── test_ai.py           # Unit tests for ai.py
    ├── test_ui.py           # Unit tests for ui.py
    ├── test_config.py       # Unit tests for config.py
    └── test_cli.py          # Integration tests for the full pipeline
```

### 3.2 Directory Responsibilities

| Module          | Responsibility                                                                           | What does NOT belong here                              |
| --------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `cli.py`        | Orchestrate the pipeline: call modules in order, handle top-level errors, set exit codes | Business logic, git commands, HTTP calls, UI rendering |
| `git.py`        | All `subprocess` calls to `git`. Parse output into dataclasses.                          | AI logic, UI code, config loading                      |
| `ai.py`         | Build the prompt, call OpenRouter via `httpx`, parse the response.                       | Git commands, UI code, config loading                  |
| `ui.py`         | All `questionary` prompts and `rich` rendering.                                          | Git commands, HTTP calls, file I/O                     |
| `config.py`     | Load and validate `~/.config/aicommit/config.toml`. Return a typed `Config` dataclass.   | Any logic beyond loading and validating                |
| `models.py`     | Pure dataclasses only. No methods with side effects.                                     | Any I/O, subprocess calls, HTTP calls                  |
| `exceptions.py` | Custom exception definitions only.                                                       | Any logic                                              |
| `tests/`        | Tests only. No helper scripts, no fixtures outside `conftest.py`.                        | Application code                                       |

### 3.3 File Naming Conventions

- All Python files: `snake_case.py`
- Test files: `test_<module_name>.py` — always in `tests/`, never co-located
- One module per file — do not put `git.py` and `ai.py` logic in the same file
- Do not create new top-level files without discussing with the human first

---

## 4. Coding Guidelines

### 4.1 Mandatory Patterns

**Single Responsibility per Module**
Each module (`git.py`, `ai.py`, `ui.py`, etc.) does exactly one thing. If a function
in `git.py` starts doing any UI work, that is a violation. Extract it.

**Dataclasses for All Data Transfer**
All data passed between modules must use dataclasses defined in `models.py`. Never pass
raw strings or dicts between module boundaries. This makes refactoring safe and types
checkable.

```python
# CORRECT
from aicommit.models import DiffPayload
def get_diff_for_files(files: list[ChangedFile]) -> DiffPayload: ...

# FORBIDDEN
def get_diff_for_files(files: list[str]) -> str: ...
```

**Type Annotations Everywhere**
Every function must have complete type annotations on parameters and return values.
`mypy` must pass with zero errors. Use `from __future__ import annotations` at the top
of files that need forward references.

**Result Pattern for Expected Failures**
Functions that can fail in expected ways must return a result object, not raise exceptions.
Use a simple `Result` dataclass:

```python
@dataclass
class Result(Generic[T]):
    ok: bool
    value: T | None = None
    error: str | None = None
```

Unexpected failures (bugs, I/O errors) may raise exceptions and be caught at the top
level in `cli.py`.

**Config Loaded Once**
`Config` is loaded once in `cli.py` and passed down as a parameter. No module may call
`config.load()` on its own. This makes testing trivial — inject a mock config.

**All Git Commands via `git.py`**
No module other than `git.py` may call `subprocess`. If you need a git operation, add
a function to `git.py` and call it from there.

### 4.2 Explicitly Forbidden Patterns

> [!warning]
> These are hard prohibitions. If you find yourself about to do any of these, stop and
> tell the human instead of proceeding.

- **`subprocess` calls outside `git.py`** — Only `git.py` runs subprocesses. If `ai.py`
  or `cli.py` needs to run a shell command, add a function to `git.py`.

- **`httpx` calls outside `ai.py`** — Only `ai.py` makes HTTP requests. No other module
  imports or uses `httpx`.

- **Hardcoded API keys, model names, or URLs** — All configurable values come from the
  `Config` dataclass loaded from `config.toml`. The OpenRouter endpoint URL is a constant
  defined in `ai.py` only, not scattered across files.

- **`print()` calls outside `ui.py`** — All terminal output goes through `rich` via
  `ui.py`. No `print()` anywhere else. Use `rich.console.Console` in `ui.py` only.

- **Mutable global state** — No module-level mutable variables. No singletons. Pass
  config and dependencies as function parameters.

- **Silent exception swallowing** — Never write `except Exception: pass`. Always either
  re-raise, return a `Result` with an error, or log and exit with a non-zero code.

- **`os.system()` or `shell=True` in subprocess** — Always use
  `subprocess.run(["git", ...], ...)` with a list of arguments. Never construct shell
  strings. This prevents injection and improves portability.

- **Writing to files outside `~/.config/aicommit/`** — The tool must not write to the
  user's project directory. The only writable path is the user config directory.

### 4.3 Error Handling

Errors fall into three categories:

**1. User errors** — things the user did wrong (not in a git repo, no changes, no API key).
Handle gracefully: print a clear, actionable error message via `ui.py`, exit with code `1`.
Never show a Python traceback to the user.

```
✗ No staged or unstaged changes found. Stage some files first.
✗ OPENROUTER_API_KEY is not set. Run: aicommit config --set api_key=<your_key>
```

**2. External failures** — git command failed, OpenRouter returned an error or timeout.
Handle gracefully: show the error message, suggest a retry, exit with code `1`.
Log the raw error to stderr only in `--verbose` mode.

**3. Internal bugs** — unexpected exceptions. Let them propagate to the top-level handler
in `cli.py`, print a user-friendly message, and show the traceback only with `--verbose`.
Always exit with code `2` for internal errors.

### 4.4 Git Command Execution Policy

All git commands use `subprocess.run()` with these exact settings:

```python
result = subprocess.run(
    ["git", ...args],
    capture_output=True,
    text=True,
    cwd=cwd,          # always pass cwd explicitly — never rely on os.getcwd()
    check=False,      # never use check=True — always handle returncode manually
)
```

Always check `result.returncode`. A non-zero return code is not an exception — it is
a `Result(ok=False, error=result.stderr)`.

### 4.5 Diff Size Limits

Large diffs can exceed AI context windows and increase latency. Apply these limits:

- Per-file diff: truncate at 500 lines, append `[diff truncated at 500 lines]`
- Total combined diff: truncate at 2000 lines, append `[total diff truncated]`
- Binary files: skip entirely, include only the filename in the prompt
- Lock files (`package-lock.json`, `poetry.lock`, `Pipfile.lock`): skip diff content,
  include only `[lock file updated]` in the prompt

These limits are constants defined in `git.py`, not hardcoded inline.

### 4.6 Performance Constraints

- Total time from invocation to first prompt displayed: under 200ms
- AI API call: use a 30-second timeout via `httpx`; surface timeout as a user error
- No async code — the tool is synchronous throughout; complexity of async is not justified
- Do not cache diffs or API responses between runs — always fresh

---

## 5. Configuration

### 5.1 Config File Location

```
~/.config/aicommit/config.toml   (Linux / macOS)
%APPDATA%\aicommit\config.toml   (Windows)
```

Use `platformdirs` (add to dependencies if not present) to resolve the correct path
cross-platform. Never hardcode `~/.config`.

### 5.2 Config Schema

```toml
[api]
api_key = ""                   # Required. OpenRouter API key.
model = "openai/gpt-4o-mini"   # Default model. Any OpenRouter-compatible model ID.
base_url = "https://openrouter.ai/api/v1"  # Override for testing only.

[behaviour]
max_diff_lines_per_file = 500  # Lines per file before truncation.
max_diff_lines_total = 2000    # Total diff lines before truncation.
editor = ""                    # Override $EDITOR for message editing. Empty = use $EDITOR.
```

### 5.3 Environment Variables

```dotenv
OPENROUTER_API_KEY=     # Purpose: OpenRouter authentication | Used by: ai.py via Config | Required: yes
                        # Takes precedence over config.toml api_key if both are set.

EDITOR=                 # Purpose: Editor for commit message editing | Used by: ui.py | Required: no
                        # Standard Unix convention. Fallback: nano on Unix, notepad on Windows.

AICOMMIT_CONFIG=        # Purpose: Override config file path (for testing) | Used by: config.py | Required: no
```

Never read environment variables directly in `ai.py` or `ui.py`. All env vars are
read once in `config.py` and surfaced through the `Config` dataclass.

### 5.4 Config Precedence (highest to lowest)

1. Environment variables (`OPENROUTER_API_KEY` overrides `config.toml` `api_key`)
2. `config.toml` values
3. Defaults defined in `config.py`

---

## 6. Data Models

All dataclasses live in `models.py`. No logic in models — pure data containers only.

### 6.1 `ChangedFile`

**Responsibility:** Represents a single file that has changes in the working tree or index.

```python
@dataclass
class ChangedFile:
    path: str            # Relative path from repo root, e.g. "src/main.py"
    status: str          # Git status code: "M" (modified), "A" (added), "D" (deleted),
                         # "R" (renamed), "?" (untracked)
    staged: bool         # True if already in the index (git add was run)
```

**Constraints:**

- `path` is always a forward-slash path, even on Windows (normalize in `git.py`)
- Never instantiate with `status=""` — always a valid git status letter

### 6.2 `DiffPayload`

**Responsibility:** The combined diff content ready to be sent to the AI, after truncation.

```python
@dataclass
class DiffPayload:
    files: list[ChangedFile]   # The files included in this diff
    diff_text: str             # Combined diff string, already truncated if needed
    was_truncated: bool        # True if any truncation was applied
    total_lines: int           # Total line count of the combined diff
```

### 6.3 `CommitSuggestion`

**Responsibility:** The AI's response, parsed and validated.

```python
@dataclass
class CommitSuggestion:
    message: str               # The full suggested commit message (subject + optional body)
    subject: str               # First line only (max 72 chars)
    body: str | None           # Everything after the blank line, or None
    model_used: str            # Model ID that generated this, for display purposes
```

**Constraints:**

- `subject` must never exceed 72 characters. If the AI returns a longer subject,
  truncate in `ai.py` and log a warning (only in verbose mode).
- `message` = `subject` if no body, else `subject + "\n\n" + body`

### 6.4 `Config`

**Responsibility:** Typed, validated configuration loaded from `config.toml` and env vars.

```python
@dataclass
class Config:
    api_key: str               # Required — error at load time if missing
    model: str                 # Default: "openai/gpt-4o-mini"
    base_url: str              # Default: "https://openrouter.ai/api/v1"
    max_diff_lines_per_file: int  # Default: 500
    max_diff_lines_total: int     # Default: 2000
    editor: str | None         # None = use $EDITOR
```

### 6.5 `CommitResult`

**Responsibility:** Outcome of `git add` + `git commit`.

```python
@dataclass
class CommitResult:
    ok: bool
    commit_hash: str | None    # Short hash if successful, None on failure
    error: str | None          # stderr output if failed
```

---

## 7. AI Prompt Design

### 7.1 System Prompt

The system prompt is a constant string defined in `ai.py`. It must not be configurable
by the user (to ensure consistent quality). The exact prompt:

```
You are an expert software engineer helping write git commit messages.

Rules you MUST follow:
1. Follow the Conventional Commits specification (https://www.conventionalcommits.org).
2. The subject line MUST be 72 characters or fewer.
3. Use the imperative mood: "add feature" not "added feature" or "adds feature".
4. The subject line format: <type>(<optional scope>): <description>
   Valid types: feat, fix, docs, style, refactor, test, chore, perf, ci, build, revert
5. If the changes warrant a body, add one after a blank line. Keep it under 100 words.
6. Do NOT include issue numbers, ticket references, or co-authors unless they appear in the diff.
7. Respond with ONLY the commit message. No explanation, no preamble, no markdown fences.
```

### 7.2 User Prompt Template

```
Here is the git diff for the files being committed:

<diff>
{diff_text}
</diff>

Files changed: {file_list}

Write a commit message for these changes.
```

Where `{file_list}` is a comma-separated list of file paths.

### 7.3 OpenRouter Request Format

```python
{
    "model": config.model,
    "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ],
    "max_tokens": 200,
    "temperature": 0.2     # Low temperature for deterministic, professional output
}
```

### 7.4 Response Parsing

- Extract `response["choices"][0]["message"]["content"]` and strip whitespace.
- If the response is empty or malformed, return `Result(ok=False, error="AI returned empty response")`.
- Do not attempt to re-parse or "fix" the AI's output — surface it as-is to the user for editing.

---

## 8. CLI Interface Reference

### 8.1 Main Command

```
aicommit [OPTIONS]
```

Run from inside any git repository. Starts the interactive pipeline.

**Options:**

| Flag               | Type   | Default     | Description                                           |
| ------------------ | ------ | ----------- | ----------------------------------------------------- |
| `--verbose` / `-v` | flag   | off         | Show raw git output, API response, tracebacks         |
| `--model`          | string | from config | Override model for this invocation only               |
| `--no-edit`        | flag   | off         | Skip the editor step, commit with AI suggestion as-is |
| `--dry-run`        | flag   | off         | Run the full pipeline but do not execute `git commit` |

### 8.2 Config Subcommand

```
aicommit config --set <key>=<value>
aicommit config --get <key>
aicommit config --list
```

Reads and writes `config.toml`. Never opens the file directly — always goes through
`config.py`.

### 8.3 Exit Codes

| Code | Meaning                                                       |
| ---- | ------------------------------------------------------------- |
| `0`  | Success — commit was made                                     |
| `1`  | User error or expected failure (no changes, abort, API error) |
| `2`  | Internal error (unexpected exception)                         |

---

## 9. Testing Strategy

> [!important]
> No implementation code is written before tests exist. This is non-negotiable.
> Every function in every module has a corresponding test.

### 9.1 Test Framework

| Layer         | Tool                             | Location                              |
| ------------- | -------------------------------- | ------------------------------------- |
| Unit tests    | `pytest`                         | `tests/test_<module>.py`              |
| Mocking       | `pytest-mock` (`mocker` fixture) | `tests/conftest.py` + inline          |
| Coverage      | `pytest-cov`                     | run via `pytest --cov=aicommit`       |
| Type checking | `mypy`                           | run via `mypy aicommit/`              |
| Linting       | `ruff`                           | run via `ruff check aicommit/ tests/` |

### 9.2 Coverage Requirements

- Minimum 90% line coverage across the whole package
- `git.py`: 100% — every git command path, including error paths
- `ai.py`: 100% — success, empty response, timeout, HTTP error
- `config.py`: 100% — missing key, env var override, defaults
- `cli.py`: integration tests covering the happy path and each error path

### 9.3 Mandatory TDD Cycle

The agent follows this cycle **for every function, without exception:**

```
┌─────────────────────────────────────────────────────────────┐
│  PHASE 1 — MOCKS & INTERFACES                               │
│  Define the function signature and dataclass types first.   │
│  Write mock implementations if needed for other modules.    │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  PHASE 2 — RED                                              │
│  Write the test. Run pytest. It MUST fail.                  │
│  If it passes before implementation — the test is wrong.    │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  PHASE 3 — GREEN                                            │
│  Write the minimum code to make the test pass.              │
│  No extra logic. No refactoring yet.                        │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  PHASE 4 — REFACTOR                                         │
│  Clean up. Apply Section 4 guidelines. Run pytest again.    │
│  All tests must still pass. Run mypy and ruff too.          │
└─────────────────────────────────────────────────────────────┘
```

### 9.4 What to Mock

| Dependency            | Mock approach                                                                                                 |
| --------------------- | ------------------------------------------------------------------------------------------------------------- |
| `subprocess.run`      | `mocker.patch("aicommit.git.subprocess.run")` — return a `MagicMock` with `.returncode`, `.stdout`, `.stderr` |
| `httpx.Client.post`   | `mocker.patch("aicommit.ai.httpx.Client.post")` — return a mock response                                      |
| `questionary` prompts | `mocker.patch("aicommit.ui.questionary.checkbox")` etc.                                                       |
| Config file on disk   | Inject a `Config` dataclass directly — never read from disk in tests                                          |
| `$EDITOR`             | Set `config.editor = "echo"` in tests — don't open a real editor                                              |

Never mock `models.py` dataclasses — use real instances.

### 9.5 Test File Conventions

```python
# tests/test_git.py — example structure

def test_detect_changed_files_returns_modified_files(mocker):
    """Happy path: git diff returns two modified files."""
    ...

def test_detect_changed_files_returns_empty_when_no_changes(mocker):
    """Edge case: no changes present."""
    ...

def test_detect_changed_files_raises_when_not_a_git_repo(mocker):
    """Error path: subprocess returns non-zero because not a repo."""
    ...
```

One test function per behaviour. Test names describe the scenario, not just the function.

---

## 10. Development Workflow

### 10.1 Setting Up Locally

```bash
git clone <repo>
cd aicommit
pip install -e ".[dev]"     # installs aicommit + all dev dependencies
aicommit --help             # verify the entry point works
pytest                      # run all tests
mypy aicommit/              # type check
ruff check aicommit/ tests/ # lint
```

### 10.2 Branch Strategy

| Branch        | Purpose                                           |
| ------------- | ------------------------------------------------- |
| `main`        | Stable, releasable code. Direct pushes forbidden. |
| `dev`         | Active development. PRs merge here first.         |
| `feat/<name>` | Feature branches off `dev`.                       |
| `fix/<name>`  | Bug fix branches off `main` or `dev`.             |

### 10.3 Commit Conventions

This project uses **Conventional Commits** (it would be embarrassing not to).

```
feat(git): add support for untracked files in file selector
fix(ai): truncate subject line when AI exceeds 72 chars
test(config): add test for env var override precedence
docs: update README with Windows installation steps
chore: bump questionary to 2.1.0
```

### 10.4 Before Every Commit

Run this sequence. All must pass before committing:

```bash
pytest --cov=aicommit --cov-fail-under=90
mypy aicommit/
ruff check aicommit/ tests/
ruff format aicommit/ tests/
```

---

## 11. Installation & Distribution

### 11.1 User Installation

```bash
pip install aicommit          # from PyPI (future)
pip install -e .              # from source, dev mode
```

After install, `aicommit` is available globally in the user's terminal.

### 11.2 `pyproject.toml` Key Sections

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "aicommit"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "questionary>=2.0",
    "httpx>=0.27",
    "rich>=13.0",
    "platformdirs>=4.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.14",
    "pytest-cov>=5.0",
    "mypy>=1.10",
    "ruff>=0.4",
]

[project.scripts]
aicommit = "aicommit.cli:main"
```

### 11.3 Cross-Platform Considerations

| Concern            | Solution                                                                 |
| ------------------ | ------------------------------------------------------------------------ |
| Config file path   | `platformdirs.user_config_dir("aicommit")`                               |
| Editor invocation  | `subprocess.run([editor, tmpfile])` — works on all platforms             |
| Path separators    | Normalize to `/` using `pathlib.Path(...).as_posix()`                    |
| `git` availability | Check with `shutil.which("git")` at startup; show clear error if missing |
| Terminal colors    | `rich` handles this — no manual ANSI codes anywhere                      |

---

## 12. Human-in-the-Loop: Actions Requiring Approval

> [!danger]
> The following actions **must never be executed autonomously by the AI agent** developing
> this project. Stop, describe the intent, and wait for explicit confirmation.

- **Running `git commit`** in any context outside of `tests/` with `--dry-run`
- **Modifying `pyproject.toml`** dependencies — always confirm version choices
- **Adding new dependencies** — justify necessity and check for lighter alternatives first
- **Changing the system prompt** in `ai.py` — this affects all users' output quality
- **Changing the config file schema** — breaking changes require migration logic
- **Publishing to PyPI** — never, under any circumstances, without explicit instruction
- **Deleting or renaming modules** — ripple effects across imports must be reviewed

---

## 13. Agent Protocols

### 13.1 When You Are Unsure

If you are unsure about any of the following, stop and ask:

- Whether a new helper function belongs in an existing module or a new one
- Whether a third-party library is already a dependency (check `pyproject.toml`)
- Whether a git command behaves identically on Windows/macOS/Linux
- What the user's preferred editor UX should be for any new interactive prompt
- Whether a change to the AI prompt requires a new test or just updating an existing one

**Do not guess. Do not assume. Ask.**

### 13.2 Scope Discipline

- Implement exactly what was asked. Nothing more.
- If you notice an unrelated bug or improvement while implementing, mention it in a
  comment at the end of your response — do not fix it silently.
- Never change the system prompt as a side effect of another task.
- Never add a new dependency as a side effect — always ask first.

### 13.3 When You Make a Mistake

1. Do not silently fix it
2. Explain what went wrong and which assumption was incorrect
3. Identify which section of this document was unclear or missing
4. Propose an amendment to add to this `agents.md`
5. Wait for confirmation before applying fix + document update

### 13.4 Context Refresh

Re-read this document at the start of every session. Pay particular attention to:

- Section 4.2 (forbidden patterns) — these are the most commonly violated
- Section 7.1 (system prompt) — do not modify without instruction
- Section 12 (human-in-the-loop) — do not bypass these gates

---

## 14. Observability

Since this is a CLI tool (not a service), observability is minimal by design.

### 14.1 Verbose Mode

All diagnostic output is gated behind `--verbose` / `-v`. In verbose mode, `cli.py`
sets a global `VERBOSE: bool = True` flag (module-level constant, set once at startup)
and each module checks it before printing debug info via `ui.py`.

Output in verbose mode:

- Raw `git diff` output before truncation
- The exact prompt sent to OpenRouter (with API key redacted)
- The raw API response
- Python traceback on unhandled exceptions

### 14.2 Stderr vs Stdout

- Normal output (prompts, suggestions, success messages): stdout via `rich.Console()`
- Errors and warnings: stderr via `rich.Console(stderr=True)`
- This allows `aicommit 2>/dev/null` to suppress errors cleanly in scripts

---

## 15. Security

### 15.1 API Key Handling

- The OpenRouter API key is never logged, never printed, never included in error messages.
- In verbose mode, the prompt sent to the API is shown but the `Authorization` header is
  redacted: `Authorization: Bearer sk-or-****`
- The config file `config.toml` should be created with `600` permissions on Unix:
  `os.chmod(config_path, 0o600)` after first write.

### 15.2 Subprocess Safety

- All subprocess calls use list arguments, never `shell=True`
- User-provided data (file paths from git output) is never interpolated into shell strings
- Diff content is sent to the AI API only — never executed or eval'd

### 15.3 Dependency Security

Run `pip-audit` before any release:

```bash
pip install pip-audit
pip-audit
```

Block releases if any high-severity CVEs are found in dependencies.

---

## 16. Known Issues & Technical Debt

- **[DEBT-001]** Windows editor invocation has not been tested end-to-end. The
  `subprocess.run([editor, tmpfile])` approach may behave unexpectedly with paths
  containing spaces on Windows. Do not touch until a Windows test environment is available.

- **[DEBT-002]** Renamed files (`git status` shows `R`) produce two entries in some git
  versions (old path + new path). The current `detect_changed_files()` implementation
  does not handle this case. Do not fix until a test case is written for it.

- **[DEBT-003]** No retry logic for OpenRouter API calls. A single timeout or 503 kills
  the session. Acceptable for v0.1, but should be addressed before v1.0.

---

## 17. Glossary

| Term                 | Definition                                                                                        |
| -------------------- | ------------------------------------------------------------------------------------------------- |
| Staged file          | A file added to the git index via `git add`. Will be included in the next commit.                 |
| Unstaged file        | A file with changes in the working tree but not yet `git add`-ed.                                 |
| Diff                 | The line-by-line difference between the current file and the last commit.                         |
| Subject line         | The first line of a git commit message. Must be ≤72 chars.                                        |
| Commit body          | Everything after the blank line following the subject. Optional.                                  |
| Conventional Commits | A specification for commit message format: `<type>(<scope>): <description>`                       |
| OpenRouter           | An API aggregator that provides access to many AI models via a single OpenAI-compatible endpoint. |
| `agents.md`          | This document. The agent's persistent memory for this project.                                    |
