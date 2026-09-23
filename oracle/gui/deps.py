from __future__ import annotations

from fastapi import Request

from oracle.services.activity_service import JobRunner
from oracle.services.app_service import AppControl
from oracle.services.context import AppContext
from oracle.services.schedule_service import Scheduler


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


def get_control(request: Request) -> AppControl:
    return request.app.state.control


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler
