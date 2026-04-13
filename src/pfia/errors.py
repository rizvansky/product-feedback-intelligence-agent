from __future__ import annotations


class PFIAError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class SessionNotReadyError(PFIAError):
    def __init__(self, message: str = "Session is not ready for grounded Q&A."):
        super().__init__("SESSION_NOT_READY", message, status_code=409, retryable=False)
