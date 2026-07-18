from __future__ import annotations


class DevboxError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


class CommandError(DevboxError):
    pass


class GitHubApiError(DevboxError):
    def __init__(self, message: str, status_code: int | None = None, exit_code: int = 1):
        super().__init__(message, exit_code=exit_code)
        self.status_code = status_code

