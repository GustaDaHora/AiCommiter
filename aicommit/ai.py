"""All OpenRouter API interactions.

This module handles building prompts, calling the OpenRouter API via httpx,
and parsing responses into CommitSuggestion and GitignoreSuggestion dataclasses.
No other module may make HTTP requests.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx

from aicommit.models import CommitSuggestion, Config, DiffPayload, GitignoreSuggestion, Result

OPENROUTER_CHAT_ENDPOINT = "/chat/completions"

# ---------------------------------------------------------------------------
# Commit message prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior software engineer writing git commit messages for a professional codebase.

Before writing the message, reason briefly (internally) about:
- What changed: additions, deletions, renames, refactors, or fixes?
- What is the primary intent of the change?
- Which module or component is most affected?

Rules you MUST follow:
1. Follow Conventional Commits (https://www.conventionalcommits.org) strictly.
2. Subject line MUST be 72 characters or fewer.
3. Imperative mood only: "add" not "added" or "adds".
4. Format: <type>(<scope>): <description>
   - scope = the affected module, file stem, or layer (e.g. git, ui, config, auth)
   - Valid types: feat, fix, docs, style, refactor, test, chore, perf, ci, build, revert
   - Use "refactor" when behavior does not change. Use "fix" only for actual bug corrections.
   - Use "chore" for tooling, deps, and config files.
5. Add a body (after a blank line) when the *why* is not obvious from the subject. \
Keep it under 80 words.
6. Do NOT invent context not present in the diff (no ticket numbers, no co-authors).
7. Respond with ONLY the commit message. No explanation, no preamble, no markdown fences."""

# ---------------------------------------------------------------------------
# .gitignore prompt
# ---------------------------------------------------------------------------

GITIGNORE_SYSTEM_PROMPT = """\
You are an expert software engineer and DevOps specialist. Your task is to generate \
a complete, well-organized .gitignore file for a software project.

You will receive:
1. A full list of every file currently present in the repository tree.
2. The existing .gitignore content (may be empty if there is none).

Your job:
- Analyze the file list to identify the project's language(s), framework(s), \
build tools, package managers, editors, and OS.
- Generate a complete .gitignore that covers ALL of the following categories \
(only include sections relevant to what you detected):
    • Language artifacts (*.pyc, __pycache__, *.class, *.o, etc.)
    • Build output directories (dist/, build/, target/, out/, etc.)
    • Dependency directories (node_modules/, .venv/, vendor/, etc.)
    • Package manager lock files that should NOT be committed (only ignore lock files \
that are not conventionally committed — e.g. ignore nothing if the project uses poetry.lock \
or package-lock.json, which ARE committed by convention)
    • Environment and secrets (.env, .env.local, *.pem, *.key, secrets.*)
    • Editor and IDE configs (.idea/, .vscode/, *.swp, .DS_Store, Thumbs.db, etc.)
    • Test and coverage artifacts (.coverage, htmlcov/, .pytest_cache/, etc.)
    • Logs and temporary files (*.log, tmp/, temp/)
    • OS-specific files (.DS_Store, desktop.ini, Thumbs.db)
    • Any project-specific files you can infer from the file list

Rules:
1. Organize the output in clearly commented sections (e.g. # Python, # Node.js, # Editors).
2. Keep each section tight — no duplicate patterns across sections.
3. Preserve any custom entries from the existing .gitignore that are not covered by \
your generated patterns.
4. Do NOT ignore files that are conventionally committed (poetry.lock, package-lock.json, \
Cargo.lock, etc.).
5. Do NOT add patterns that would ignore source code or project files that should be tracked.
6. Respond with ONLY the raw .gitignore content. No explanation, no preamble, \
no markdown fences, no code blocks."""

_MAX_SUBJECT_LENGTH = 72
_API_TIMEOUT_SECONDS = 30

FALLBACK_MODELS = [
    "stepfun/step-3.5-flash:free",
    "arcee-ai/trinity-large-preview:free",
    "openrouter/hunter-alpha",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/healer-alpha",
    "z-ai/glm-4.5-air:free",
]


def _build_user_prompt(diff: DiffPayload) -> str:
    """Build the commit message user prompt from a DiffPayload."""
    file_lines = "\n".join(
        f"  [{f.status}] {'(staged) ' if f.staged else ''}{f.path}"
        for f in diff.files
    )
    return (
        f"Files being committed:\n{file_lines}\n\n"
        f"Git diff:\n\n<diff>\n{diff.diff_text}\n</diff>\n\n"
        f"Write a commit message for these changes."
    )


def _build_gitignore_user_prompt(file_list: list[str], existing_gitignore: str) -> str:
    """Build the .gitignore user prompt."""
    files_block = "\n".join(f"  {p}" for p in file_list)
    existing_block = (
        existing_gitignore.strip()
        if existing_gitignore.strip()
        else "(no existing .gitignore)"
    )
    return (
        f"Current .gitignore content:\n\n"
        f"<gitignore>\n{existing_block}\n</gitignore>\n\n"
        f"All files currently present in the repository:\n\n"
        f"<files>\n{files_block}\n</files>\n\n"
        f"Generate a complete, reorganized .gitignore for this project."
    )


def _parse_commit_message(raw: str, model: str) -> CommitSuggestion:
    """Parse a raw commit message string into a CommitSuggestion."""
    lines = raw.strip().splitlines()
    subject = lines[0] if lines else raw.strip()

    if len(subject) > _MAX_SUBJECT_LENGTH:
        subject = subject[:_MAX_SUBJECT_LENGTH]

    body: str | None = None
    if len(lines) > 2 and lines[1].strip() == "":
        body = "\n".join(lines[2:]).strip() or None

    message = f"{subject}\n\n{body}" if body else subject

    return CommitSuggestion(
        message=message,
        subject=subject,
        body=body,
        model_used=model,
    )


def _parse_gitignore_content(raw: str, model: str) -> GitignoreSuggestion:
    """Parse the raw .gitignore content returned by the AI."""
    content = raw.strip()
    # Strip accidental markdown fences the model may have added
    if content.startswith("```"):
        lines = content.splitlines()
        # Remove first line (```gitignore or ```) and last ``` if present
        inner = lines[1:] if lines[0].startswith("```") else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        content = "\n".join(inner).strip()

    entries = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    return GitignoreSuggestion(content=content, entries=entries, model_used=model)


def _log_api_event(config: Config, event_type: str, data: dict | str) -> None:  # type: ignore[type-arg]
    """Log an API event to a timestamped file in the logs directory."""
    if config.enable_logging != 1:
        return

    from aicommit.config import _get_config_path

    log_dir = _get_config_path().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = log_dir / f"api_{timestamp}_{event_type}.json"

    log_data = {
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        "data": data,
    }

    try:
        log_file.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _call_api(
    system_prompt: str,
    user_prompt: str,
    config: Config,
    max_tokens: int = 300,
) -> Result[tuple[str, str]]:
    """Call the OpenRouter API with fallback models.

    Returns Result with (content, model_used) on success.
    """
    url = f"{config.base_url}{OPENROUTER_CHAT_ENDPOINT}"
    models_to_try = [config.model] + [m for m in FALLBACK_MODELS if m != config.model]
    last_error = "Unknown error"

    for model in models_to_try:
        request_body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }

        _log_api_event(config, "request", {"model": model, "url": url, "payload": request_body})

        try:
            with httpx.Client(timeout=_API_TIMEOUT_SECONDS) as client:
                response = client.post(
                    url,
                    json=request_body,
                    headers={
                        "Authorization": f"Bearer {config.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                _log_api_event(
                    config,
                    "response",
                    {"model": model, "status_code": response.status_code, "body": response.text},
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            last_error = f"Model {model} timed out."
            _log_api_event(config, "error", {"model": model, "error": last_error})
            continue
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                return Result(ok=False, error="API unauthorized (HTTP 401): Check your API key.")
            last_error = (
                f"Model {model} API error "
                f"(HTTP {exc.response.status_code}): {exc.response.text}"
            )
            _log_api_event(config, "error", {"model": model, "error": last_error})
            continue
        except Exception as exc:
            import traceback

            last_error = f"Model {model} API request failed: {exc}"
            _log_api_event(
                config,
                "error",
                {"model": model, "error": last_error, "traceback": traceback.format_exc()},
            )
            continue

        try:
            data = response.json()
            raw_content = data["choices"][0]["message"].get("content")
            content = raw_content.strip() if raw_content is not None else ""
            model_used = data.get("model", model)
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            last_error = f"Model {model} unexpected API response format: {exc}"
            _log_api_event(config, "error", {"model": model, "error": last_error})
            continue

        if not content:
            last_error = f"Model {model} returned empty response."
            continue

        return Result(ok=True, value=(content, model_used))

    return Result(ok=False, error=f"All models failed. Last error: {last_error}")


def suggest_commit_message(diff: DiffPayload, config: Config) -> Result[CommitSuggestion]:
    """Send a diff to OpenRouter and get a suggested commit message."""
    user_prompt = _build_user_prompt(diff)
    api_result = _call_api(SYSTEM_PROMPT, user_prompt, config, max_tokens=300)
    if not api_result.ok:
        return Result(ok=False, error=api_result.error)
    content, model_used = api_result.value  # type: ignore[misc]
    return Result(ok=True, value=_parse_commit_message(content, model_used))


def suggest_gitignore(
    file_list: list[str],
    existing_gitignore: str,
    config: Config,
) -> Result[GitignoreSuggestion]:
    """Send the full file list to OpenRouter and get a reorganized .gitignore."""
    user_prompt = _build_gitignore_user_prompt(file_list, existing_gitignore)
    # .gitignore can be long — allow up to 2000 tokens
    api_result = _call_api(GITIGNORE_SYSTEM_PROMPT, user_prompt, config, max_tokens=2000)
    if not api_result.ok:
        return Result(ok=False, error=api_result.error)
    content, model_used = api_result.value  # type: ignore[misc]
    return Result(ok=True, value=_parse_gitignore_content(content, model_used))
