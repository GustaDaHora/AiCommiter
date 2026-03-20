"""All OpenRouter API interactions.

This module handles building prompts, calling the OpenRouter API via httpx,
and parsing responses into CommitSuggestion dataclasses.
No other module may make HTTP requests.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx

from aicommit.models import CommitSuggestion, Config, DiffPayload, Result

OPENROUTER_CHAT_ENDPOINT = "/chat/completions"

SYSTEM_PROMPT = """\
You are an expert software engineer helping write git commit messages.

Rules you MUST follow:
1. Follow the Conventional Commits specification (https://www.conventionalcommits.org).
2. The subject line MUST be 72 characters or fewer.
3. Use the imperative mood: "add feature" not "added feature" or "adds feature".
4. The subject line format: <type>(<optional scope>): <description>
   Valid types: feat, fix, docs, style, refactor, test, chore, perf, ci, build, revert
5. If the changes warrant a body, add one after a blank line. Keep it under 100 words.
6. Do NOT include issue numbers, ticket references, or co-authors unless they appear in the diff.
7. Respond with ONLY the commit message. No explanation, no preamble, no markdown fences."""

_MAX_SUBJECT_LENGTH = 72
_API_TIMEOUT_SECONDS = 30


def _build_user_prompt(diff: DiffPayload) -> str:
    """Build the user prompt from a DiffPayload."""
    file_list = ", ".join(f.path for f in diff.files)
    return (
        f"Here is the git diff for the files being committed:\n\n"
        f"<diff>\n{diff.diff_text}\n</diff>\n\n"
        f"Files changed: {file_list}\n\n"
        f"Write a commit message for these changes."
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

    if body:
        message = f"{subject}\n\n{body}"
    else:
        message = subject

    return CommitSuggestion(
        message=message,
        subject=subject,
        body=body,
        model_used=model,
    )


FALLBACK_MODELS = [
    "stepfun/step-3.5-flash:free",
    "arcee-ai/trinity-large-preview:free",
    "openrouter/hunter-alpha",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/healer-alpha",
    "z-ai/glm-4.5-air:free",
]


def _log_api_event(config: Config, event_type: str, data: dict | str) -> None:
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
        # Silently fail logging if it fails
        pass


def suggest_commit_message(diff: DiffPayload, config: Config) -> Result[CommitSuggestion]:
    """Send a diff to OpenRouter and get a suggested commit message, trying fallbacks if needed."""
    user_prompt = _build_user_prompt(diff)
    url = f"{config.base_url}{OPENROUTER_CHAT_ENDPOINT}"

    models_to_try = [config.model] + [m for m in FALLBACK_MODELS if m != config.model]

    last_error = "Unknown error"

    for model in models_to_try:
        request_body = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 200,
            "temperature": 0.2,
        }

        _log_api_event(config, "request", {
            "model": model,
            "url": url,
            "payload": request_body
        })

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
                
                _log_api_event(config, "response", {
                    "model": model,
                    "status_code": response.status_code,
                    "body": response.text
                })

                response.raise_for_status()
        except httpx.TimeoutException:
            last_error = f"Model {model} timed out."
            _log_api_event(config, "error", {"model": model, "error": last_error})
            continue
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                _log_api_event(config, "error", {"model": model, "error": "Unauthorized"})
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
            _log_api_event(config, "error", {
                "model": model, 
                "error": last_error,
                "traceback": traceback.format_exc()
            })
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

        suggestion = _parse_commit_message(content, model_used)
        return Result(ok=True, value=suggestion)

    return Result(ok=False, error=f"All models failed. Last error: {last_error}")
