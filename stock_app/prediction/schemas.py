"""Explicit error contract for prediction consumers."""


class PredictionError(ValueError):
    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code = code
        self.status = status
