"""Custom exception classes for aicommit."""


class AiCommitError(Exception):
    """Base exception for all aicommit errors."""


class ConfigError(AiCommitError):
    """Raised when configuration loading or validation fails."""


class MissingApiKeyError(ConfigError):
    """Raised specifically when the OpenRouter API key is not found."""


class GitError(AiCommitError):
    """Raised when a git operation fails unexpectedly."""


class AIError(AiCommitError):
    """Raised when the AI API call fails unexpectedly."""
