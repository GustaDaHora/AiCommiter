"""All terminal UI interactions.

All user-facing output goes through this module via rich and questionary.
No other module may use print() or direct terminal output.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import questionary
from rich.console import Console
from rich.panel import Panel

from aicommit.models import ChangedFile, CommitResult, CommitSuggestion, Config

_console = Console()
_err_console = Console(stderr=True)


def _file_label(f: ChangedFile) -> str:
    """Create a display label for a file in the checkbox selector."""
    stage_tag = "staged" if f.staged else "unstaged"
    return f"[{f.status}]  {PurePosixPath(f.path).name} ({stage_tag})"


class _TreeNode:
    def __init__(self, name: str, full_path: str):
        self.name = name
        self.full_path = full_path
        self.files: list[tuple[int, ChangedFile]] = []
        self.children: dict[str, _TreeNode] = {}

    def add_file(self, parts: tuple[str, ...], file_tuple: tuple[int, ChangedFile]) -> None:
        if not parts:
            self.files.append(file_tuple)
            return
        
        child_name = parts[0]
        if child_name not in self.children:
            child_path = f"{self.full_path}/{child_name}" if self.full_path else child_name
            self.children[child_name] = _TreeNode(child_name, child_path)
            
        self.children[child_name].add_file(parts[1:], file_tuple)

    def count_files(self) -> int:
        return len(self.files) + sum(c.count_files() for c in self.children.values())

    def all_staged(self) -> bool:
        files_staged = all(f.staged for _, f in self.files)
        children_staged = all(c.all_staged() for c in self.children.values())
        return files_staged and (not self.children or children_staged)

def _build_choices(node: _TreeNode, depth: int, choices: list[questionary.Choice]) -> None:
    indent = "  " * depth
    if node.name: # Skip printing the root node itself, just its children
        file_count = node.count_files()
        choices.append(
            questionary.Choice(
                title=f"{indent}📁 {node.name}/ (Select all {file_count} items)",
                value=f"dir:{node.full_path}",
                checked=node.all_staged()
            )
        )
        depth += 1
        indent = "  " * depth

    for child_name in sorted(node.children.keys()):
        _build_choices(node.children[child_name], depth, choices)

    for idx, f in sorted(node.files, key=lambda x: x[1].path):
        choices.append(
            questionary.Choice(
                title=f"{indent}📄 {_file_label(f)}",
                value=f"file:{idx}",
                checked=f.staged
            )
        )


def prompt_file_selection(files: list[ChangedFile]) -> list[ChangedFile]:
    """Show an interactive checkbox list to select files for the commit."""
    root = _TreeNode("", "")
    for i, f in enumerate(files):
        parts = PurePosixPath(f.path).parts[:-1] # Exclude filename
        root.add_file(parts, (i, f))

    choices: list[questionary.Choice] = []
    _build_choices(root, 0, choices)

    selected_values = questionary.checkbox(
        "Select files to include in the commit:",
        choices=choices,
    ).ask()

    if not selected_values:
        return []

    selected_indices = set()
    
    selected_dirs = [v.split(":", 1)[1] for v in selected_values if v.startswith("dir:")]
    
    for value in selected_values:
        if value.startswith("file:"):
            idx = int(value.split(":", 1)[1])
            selected_indices.add(idx)

    for i, f in enumerate(files):
        for d in selected_dirs:
            # Check if file is exactly in the selected dir, or a subdirectory of it
            if f.path.startswith(f"{d}/") or f.path.startswith(f"{d}\\"):
                selected_indices.add(i)

    return [files[i] for i in sorted(selected_indices)]


def prompt_edit_and_confirm(suggestion: CommitSuggestion, config: Config) -> str | None:
    """Display the AI suggestion and prompt the user to accept, edit, or abort.

    Returns the final commit message, or None if the user aborts.
    """
    _console.print()
    _console.print(
        Panel(
            suggestion.message,
            title="Suggested Commit Message",
            subtitle=f"model: {suggestion.model_used}",
            border_style="green",
            padding=(1, 2),
        )
    )
    _console.print()

    action = questionary.select(
        "What would you like to do?",
        choices=[
            questionary.Choice("Accept and commit", value="accept"),
            questionary.Choice("Edit message", value="edit"),
            questionary.Choice("Abort", value="abort"),
        ],
    ).ask()

    if action is None or action == "abort":
        return None

    if action == "edit":
        edited = questionary.text(
            "Edit commit message:",
            default=suggestion.message,
        ).ask()
        if not edited:
            return None
        return str(edited)

    return suggestion.message


def display_error(message: str) -> None:
    """Display an error message to stderr."""
    _err_console.print(f"[bold red]✗[/bold red] {message}")


def display_success(result: CommitResult) -> None:
    """Display a success message after committing."""
    hash_str = result.commit_hash or "unknown"
    _console.print(f"[bold green]✓[/bold green] Committed successfully [{hash_str}]")


def display_spinner_message(message: str) -> None:
    """Display a status message (used for AI generation phase)."""
    _console.print(f"[dim]{message}[/dim]")


def prompt_api_key() -> str | None:
    """Prompt the user interactively for their OpenRouter API key.

    Returns the key as a string, or None if the user cancels.
    """
    _console.print()
    key = questionary.password(
        "Please enter your OpenRouter API key:",
    ).ask()
    return str(key) if key else None


def prompt_continue() -> bool:
    """Prompt the user if they want to continue committing more files.

    Returns True if the user wants to continue, False otherwise.
    """
    _console.print()
    answer = questionary.confirm("Do you want to commit more files?").ask()
    return bool(answer)
