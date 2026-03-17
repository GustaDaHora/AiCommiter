"""CLI entry point: parses args and orchestrates the pipeline.

This module contains no business logic, git commands, HTTP calls, or UI rendering.
It coordinates the pipeline by calling the appropriate module functions in order.
"""

from __future__ import annotations

import argparse
import os
import sys

from aicommit.ai import suggest_commit_message
from aicommit.config import load_config, set_api_key
from aicommit.exceptions import AiCommitError, ConfigError, MissingApiKeyError
from aicommit.git import detect_changed_files, get_diff_for_files, get_repo_root, stage_and_commit
from aicommit.ui import (
    display_error,
    display_success,
    prompt_api_key,
    prompt_edit_and_confirm,
    prompt_file_selection,
    prompt_continue,
)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="aicommit",
        description="AI-powered git commit message generator",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Show raw git output, API response, and tracebacks",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override AI model for this invocation",
    )
    parser.add_argument(
        "--no-edit",
        action="store_true",
        default=False,
        help="Skip the editor step, commit with AI suggestion as-is",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run the full pipeline but do not execute git commit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the aicommit CLI.

    Returns exit code: 0 for success, 1 for user/expected errors, 2 for internal errors.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    verbose: bool = args.verbose

    try:
        try:
            config = load_config()
        except MissingApiKeyError:
            api_key = prompt_api_key()
            if not api_key:
                display_error("API key is required to use aicommit. Aborting.")
                return 1
            set_api_key(api_key)
            config = load_config()
    except ConfigError as exc:
        display_error(str(exc))
        return 1

    if args.model:
        config.model = args.model

    try:
        cwd = os.getcwd()

        repo_result = get_repo_root(cwd)
        if not repo_result.ok:
            display_error(
                f"Not a git repository: {repo_result.error}\n"
                "  Run this command from inside a git repository."
            )
            return 1
        repo_root = repo_result.value or cwd

        while True:
            files_result = detect_changed_files(repo_root)
            if not files_result.ok:
                display_error(f"Failed to detect changed files: {files_result.error}")
                return 1

            changed_files = files_result.value or []
            if not changed_files:
                display_error("No staged or unstaged changes found. Stage some files first.")
                return 1

            selected_files = prompt_file_selection(changed_files)
            if not selected_files:
                display_error("No files selected. Aborting.")
                return 1

            diff_payload = get_diff_for_files(selected_files, repo_root, config)

            ai_result = suggest_commit_message(diff_payload, config)
            if not ai_result.ok:
                display_error(f"AI suggestion failed: {ai_result.error}")
                return 1

            suggestion = ai_result.value
            assert suggestion is not None

            if args.no_edit:
                final_message = suggestion.message
            else:
                edited_message = prompt_edit_and_confirm(suggestion, config)
                if edited_message is None:
                    display_error("Commit aborted by user.")
                    return 1
                final_message = edited_message

            if args.dry_run:
                # Prompt to continue even if dry run.
                if not prompt_continue():
                    return 0
                continue

            commit_result = stage_and_commit(selected_files, final_message, repo_root)
            if not commit_result.ok:
                display_error(f"Commit failed: {commit_result.error}")
                return 1

            display_success(commit_result)
            
            if not prompt_continue():
                break

        return 0

    except AiCommitError as exc:
        display_error(str(exc))
        if verbose:
            import traceback

            traceback.print_exc()
        return 1
    except Exception as exc:
        display_error(f"An unexpected error occurred: {exc}")
        if verbose:
            import traceback

            traceback.print_exc()
        return 2


def _entry_point() -> None:
    """Console script entry point."""
    sys.exit(main())
