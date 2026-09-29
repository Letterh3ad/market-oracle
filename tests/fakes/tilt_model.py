"""A scripted model for the regime-ai tilt: answers with a fixed reply (or raises), recording
every prompt, so no test ever reaches a real model."""

from __future__ import annotations


class TiltModel:
    name = "fake"
    model = "tilt-1"
    reply_tokens = 512

    def __init__(self, reply: object = None, error: Exception | None = None) -> None:
        self.answer, self.error = reply, error
        self.asked: list[tuple[list[dict], dict]] = []
        self.fell_back_to: set[str] = set()

    async def reply(self, messages: list[dict], schema: dict) -> object:
        self.asked.append((messages, schema))
        if self.error is not None:
            raise self.error
        return self.answer


STAND_IN = {"hold": 1.0, "reason": "test stand-in: every weight kept"}
"""What every test's configured tilt model answers (conftest): a real model is never reached."""
