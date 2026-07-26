"""
ZenControl library exceptions.

This module defines all custom exceptions used throughout the library.
"""

from typing import Any


class ZenError(Exception):
    """Base exception for zencontrol library / TPI errors."""
    pass


class ZenTimeoutError(ZenError):
    """Raised when a command times out"""
    pass


class ZenResponseError(ZenError):
    """Raised when the controller returns ERROR or an invalid wire response."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        error_code: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.error_code = error_code


class ZenConnectionError(ZenError):
    """Raised when connection to controller fails"""
    pass


class ZenConfigurationError(ZenError):
    """Raised when configuration is invalid"""
    pass
