class CircuitOpen(Exception):
    def __init__(self, source: str, reason: str, retry_at: float) -> None:
        super().__init__(f"{source}: circuit open ({reason})")
        self.source = source
        self.reason = reason
        self.retry_at = retry_at


class RequestFailed(Exception):
    def __init__(self, source: str, status: int | None, detail: str) -> None:
        super().__init__(f"{source}: request failed ({detail})")
        self.source = source
        self.status = status
