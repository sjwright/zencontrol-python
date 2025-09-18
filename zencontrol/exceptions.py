"""
ZenControl library exceptions.

This module defines all custom exceptions used throughout the library.
"""


class ZenError(Exception):
    """Base exception for Zen protocol errors"""
    pass


class ZenTimeoutError(ZenError):
    """Raised when a command times out"""
    pass


class ZenResponseError(ZenError):
    """Raised when receiving an invalid response"""
    pass


class ZenConnectionError(ZenError):
    """Raised when connection to controller fails"""
    pass


class ZenConfigurationError(ZenError):
    """Raised when configuration is invalid"""
    pass
