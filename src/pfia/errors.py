from __future__ import annotations


class PFIAError(Exception):
    """Base application error with HTTP-friendly metadata."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
    ):
        """Initialize a structured PFIA exception.

        Args:
            code: Stable machine-readable error code.
            message: Human-readable explanation of the failure.
            status_code: HTTP status code that should be returned by the API layer.
            retryable: Whether callers may safely retry the failed operation.
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class SessionNotReadyError(PFIAError):
    """Raised when grounded Q&A is requested before a session completes."""

    def __init__(self, message: str = "Session is not ready for grounded Q&A."):
        """Create the canonical session-not-ready error.

        Args:
            message: Override for the default user-facing message.
        """
        super().__init__("SESSION_NOT_READY", message, status_code=409, retryable=False)
