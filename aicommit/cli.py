"""CLI entry point: parses args and orchestrates the pipeline.

This module contains no business logic, git commands, HTTP calls, or UI rendering.
It coordinates the pipeline by calling the appropriate module functions in order.
"""

from __future__ import annotations

import argparse
import os
import sys

from aicommit.ai import suggest_commit_message, suggest_gitignore
from aicommit.config import load_config, set_api_key
from aicommit.exceptions import AiCommitError, ConfigError, MissingApiKeyError
from aicommit.git import (
    detect_changed_files,
    get_diff_for_files,
    get_repo_root,
    list_all_files,
    read_gitignore,
    stage_and_commit,
    write_gitignore,
)
from aicommit.ui import (
    display_error,
    display_gitignore_success,
    display_gitignore_suggestion,
    display_success,
    prompt_api_key,
    prompt_continue,
    prompt_edit_and_confirm,
    prompt_file_selection,
    display_spinner_message,
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

    subparsers = parser.add_subparsers(dest="subcommand")

    # -----------------------------------------------------------------
    # aicommit gitignore
    # -----------------------------------------------------------------
    gitignore_parser = subparsers.add_parser(
        "gitignore",
        help="Generate or regenerate .gitignore using AI analysis of your project files",
    )
    gitignore_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Show raw API response and tracebacks",
    )
    gitignore_parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override AI model for this invocation",
    )
    gitignore_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show the suggested .gitignore without writing the file",
    )

    # -----------------------------------------------------------------
    # Default: commit flow (no subcommand)
    # -----------------------------------------------------------------
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


def _load_config_with_prompt(verbose: bool) -> tuple[object, int] | tuple[object, None]:
    """Load config, prompting for API key if missing. Returns (config, None) or (None, exit_code)."""
    try:
        try:
            config = load_config()
        except MissingApiKeyError:
            api_key = prompt_api_key()
            if not api_key:
                display_error("API key is required to use aicommit. Aborting.")
                return None, 1
            set_api_key(api_key)
            config = load_config()
        return config, None
    except ConfigError as exc:
        display_error(str(exc))
        return None, 1


def _run_gitignore(args: argparse.Namespace, verbose: bool) -> int:
    """Run the `aicommit gitignore` subcommand pipeline.

    Flow:
    1. Resolve repo root
    2. List all files in the project
    3. Read existing .gitignore (if any)
    4. Send to AI for analysis
    5. Display suggestion and confirm
    6. Write .gitignore (unless --dry-run)

    Note: `verbose` is passed explicitly from main() so both the parent flag
    (`aicommit --verbose gitignore`) and the subcommand flag
    (`aicommit gitignore --verbose`) are honoured.
    """
    config, err = _load_config_with_prompt(verbose)
    if err is not None:
        return err  # type: ignore[return-value]

    from aicommit.models import Config as ConfigType
    assert isinstance(config, ConfigType)

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

        files_result = list_all_files(repo_root)
        if not files_result.ok:
            display_error(f"Failed to list project files: {files_result.error}")
            return 1

        file_list = files_result.value or []
        if not file_list:
            display_error("No files found in the repository.")
            return 1

        existing_gitignore = read_gitignore(repo_root)
        has_existing = bool(existing_gitignore.strip())

        display_spinner_message(
            f"Analyzing {len(file_list)} files with AI… this may take a moment."
        )

        ai_result = suggest_gitignore(file_list, existing_gitignore, config)
        if not ai_result.ok:
            display_error(f"AI suggestion failed: {ai_result.error}")
            return 1

        suggestion = ai_result.value
        assert suggestion is not None

        confirmed = display_gitignore_suggestion(suggestion, has_existing)

        if not confirmed:
            display_error("Aborted. .gitignore was not modified.")
            return 1

        if args.dry_run:
            display_spinner_message("Dry run — .gitignore was NOT written.")
            return 0

        write_result = write_gitignore(repo_root, suggestion.content)
        if not write_result.ok:
            display_error(f"Failed to write .gitignore: {write_result.error}")
            return 1

        import os as _os
        gitignore_path = _os.path.join(repo_root, ".gitignore")
        display_gitignore_success(gitignore_path)
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


def _run_commit(args: argparse.Namespace, verbose: bool) -> int:
    """Run the default commit pipeline."""
    config, err = _load_config_with_prompt(verbose)
    if err is not None:
        return err  # type: ignore[return-value]

    from aicommit.models import Config as ConfigType
    assert isinstance(config, ConfigType)

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


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the aicommit CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    verbose: bool = args.verbose

    if args.subcommand == "gitignore":
        # honour both `aicommit --verbose gitignore` and `aicommit gitignore --verbose`
        sub_verbose = verbose or getattr(args, "verbose", False)
        return _run_gitignore(args, sub_verbose)

    return _run_commit(args, verbose)


def _entry_point() -> None:
    """Console script entry point."""
    sys.exit(main())