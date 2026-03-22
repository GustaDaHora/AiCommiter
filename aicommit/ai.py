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

SYSTEM_PROMPT = """You are a senior software engineer writing git commit messages \
for a professional codebase.

Analyze the diff and write a commit message using this format:

  <type>(<scope>): <description>

  [optional body]

TYPES — pick exactly one:
  feat      A new feature or capability
  fix       A bug correction (behavior was wrong, now it is right)
  refactor  Code restructured with no behavior change (rename, extract, simplify)
  test      Adding or updating tests only
  docs      Documentation only (comments, README, docstrings)
  chore     Tooling, dependencies, config files, build scripts
  perf      Performance improvement with no API change
  style     Formatting only (whitespace, semicolons) — zero logic change
  ci        CI/CD pipeline configuration
  build     Build system or compilation changes
  revert    Reverting a previous commit

SCOPE — the affected module, file stem, or layer (examples: git, ui, config, cli, auth).
Omit scope only when the change spans the entire project with no clear focal point.

SUBJECT LINE — strict rules:
  - 72 characters maximum (type + scope + description combined)
  - Imperative mood: "add feature" not "added feature" or "adds feature"
  - No period at the end
  - Lowercase first letter after the colon

BODY — optional, use only when the subject cannot capture the why:
  - Separated from subject by exactly one blank line
  - Hard-wrap at 80 characters
  - 80 words maximum
  - Explain motivation or context, not what the code does (the diff already shows that)

Do NOT invent context absent from the diff (no ticket numbers, no co-authors).
Respond with ONLY the commit message — no explanation, no preamble, no markdown fences."""

# ---------------------------------------------------------------------------
# .gitignore prompt
# ---------------------------------------------------------------------------

GITIGNORE_SYSTEM_PROMPT = """You are an expert software engineer. \
Generate a complete, well-organized .gitignore file.

You will receive the full file tree of the repository and the existing .gitignore (if any).

STEP 1 — Detect the stack from the file tree:
  Languages    *.py -> Python | *.js *.ts -> Node.js | *.rs -> Rust | *.go -> Go
               *.java *.kt -> JVM | *.cs -> .NET | *.rb -> Ruby | *.php -> PHP
  Pkg managers package.json -> npm/yarn/pnpm | pyproject.toml or setup.py -> pip/poetry
               Cargo.toml -> cargo | go.mod -> Go modules | Gemfile -> bundler
               composer.json -> Composer
  Frameworks   next.config.* -> Next.js | nuxt.config.* -> Nuxt | angular.json -> Angular
  Editors      .vscode/ present -> VS Code user | .idea/ present -> JetBrains user
  OS           .DS_Store present -> macOS | Thumbs.db present -> Windows

STEP 2 — Generate ignore patterns only for what you detected:

  Python       __pycache__/, *.py[cod], *$py.class, *.so, *.egg-info/, .eggs/,
               dist/, build/, .venv/, venv/, env/, ENV/, env.bak/, venv.bak/,
               .coverage, .coverage.*, htmlcov/, .pytest_cache/, .mypy_cache/,
               .ruff_cache/, .tox/, .nox/, nosetests.xml, coverage.xml, *.cover,
               .hypothesis/

  Node.js      node_modules/, .npm, .yarn/, .pnp.*, .cache/

  Next.js      .next/, out/

  Nuxt         .nuxt/, dist/

  Java/Kotlin  *.class, *.jar, *.war, *.ear, target/, build/

  Rust         target/   (Cargo.lock is committed for binaries — do NOT ignore it)

  Go           vendor/ only if present in tree; go.sum is committed — do NOT ignore it

  Secrets      .env, .env.local, .env.*.local, *.pem, *.key, secrets.*

  Editors      .vscode/, .idea/, *.swp, *.swo, *~, *.sublime-workspace,
               *.sublime-project

  OS           .DS_Store, .DS_Store?, ._*, .Spotlight-V100, .Trashes,
               ehthumbs.db, Thumbs.db, desktop.ini

  Logs/temp    *.log, *.tmp, *.temp, log/, logs/, tmp/, temp/

LOCK FILE RULE — NEVER ignore these, they are committed by convention:
  poetry.lock, package-lock.json, yarn.lock, pnpm-lock.yaml,
  Cargo.lock (binaries), Gemfile.lock, composer.lock, go.sum

CUSTOM ENTRIES — preserve any entries in the existing .gitignore not already covered
above. Place them under a "# Project-specific" section at the bottom.

OUTPUT:
  - Organize in commented sections: # Python, # Node.js, # Editors, # OS, etc.
  - Include only sections relevant to the detected stack
  - No duplicate patterns across sections
  - Do NOT ignore source code, test files, or tracked project configuration
  - Respond with ONLY the raw .gitignore content — no explanation, no preamble,
    no markdown fences, no code blocks"""

_MAX_SUBJECT_LENGTH = 72
_API_TIMEOUT_SECONDS = 30

FALLBACK_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "qwen/qwen3-coder:free",
    "arcee-ai/trinity-large-preview:free",
    "stepfun/step-3.5-flash:free",
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
    if content.startswith("```"):
        lines = content.splitlines()
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

    The request body includes ``thinking: {"type": "disabled"}`` to suppress
    extended thinking / chain-of-thought on models that support it (Claude 3.7+,
    DeepSeek R1, QwQ, o1/o3, etc.). Thinking tokens add significant latency
    without improving output quality for well-specified structured tasks like
    commit messages and .gitignore generation. Models that do not recognise this
    field ignore it silently — it is safe to send to all models.
    """
    url = f"{config.base_url}{OPENROUTER_CHAT_ENDPOINT}"
    models_to_try = [config.model] + [m for m in FALLBACK_MODELS if m != config.model]
    last_error = "Unknown error"

    for model in models_to_try:
        request_body: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            # Disable extended thinking on models that support it.
            # Safe to include for all models — unsupported models ignore it.
            "thinking": {"type": "disabled"},
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
    api_result = _call_api(GITIGNORE_SYSTEM_PROMPT, user_prompt, config, max_tokens=2000)
    if not api_result.ok:
        return Result(ok=False, error=api_result.error)
    content, model_used = api_result.value  # type: ignore[misc]
    return Result(ok=True, value=_parse_gitignore_content(content, model_used))