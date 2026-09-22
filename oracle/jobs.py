from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from oracle.clock import Clock, epoch
from oracle.store.db import transaction
from oracle.store.jobs import JobLockRepo, LockRow


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # Access denied means it exists but belongs to someone else (an elevated
            # scheduled tick, say). Only a missing process counts as gone.
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED
        try:
            # OpenProcess still succeeds on an exited process while anyone holds a handle
            # to it (a parent that spawned it, say), so the exit code is the real answer.
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True  # unknown: never steal a lock on a guess
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    # Never os.kill(pid, 0) on Windows: there it terminates the process.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class JobBusy(Exception):
    def __init__(self, job: str, pid: int, host: str, age_s: float) -> None:
        super().__init__(f"{job} is held by pid {pid} on {host} for {age_s:.0f}s")
        self.job = job
        self.pid = pid
        self.host = host
        self.age_s = age_s


@dataclass(frozen=True)
class LockHandle:
    job: str
    token: str
    pid: int


@dataclass
class HeldJob:
    handle: LockHandle
    lost: bool = False


@dataclass(frozen=True)
class JobStatus:
    job: str
    pid: int
    host: str
    age_s: float
    heartbeat_age_s: float
    stale_reason: str | None


class JobLock:
    """Single-flight jobs across processes, surviving a hard kill.

    A holder is stale when its lease lapses without a heartbeat, or, on this host, when
    its process is gone. The pid check makes takeover immediate after a crash; the lease
    covers pid reuse and other hosts.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Clock,
        pid_alive: Callable[[int], bool] = pid_alive,
        pid: int | None = None,
        host: str | None = None,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._pid_alive = pid_alive
        self._pid = os.getpid() if pid is None else pid
        self._host = host or socket.gethostname()
        self._repo = JobLockRepo(conn)

    def acquire(self, job: str, lease_s: float) -> LockHandle:
        with transaction(self._conn):
            # Read under the write lock: BEGIN IMMEDIATE can wait out another process.
            now = epoch(self._clock)
            current = self._repo.get(job)
            if current is not None and self._stale_reason(current, now) is None:
                raise JobBusy(job, current.pid, current.host, now - current.acquired_at)
            token = uuid.uuid4().hex
            self._repo.put(LockRow(job, token, self._pid, self._host, now, now, lease_s))
        return LockHandle(job, token, self._pid)

    def heartbeat(self, handle: LockHandle) -> bool:
        """False means the lock was taken over and the caller should stop."""
        with transaction(self._conn):
            return self._repo.touch(handle.job, handle.token, epoch(self._clock))

    def release(self, handle: LockHandle) -> None:
        with transaction(self._conn):
            self._repo.delete(handle.job, handle.token)

    @asynccontextmanager
    async def holding(self, job: str, lease_s: float) -> AsyncIterator[HeldJob]:
        """Hold a job for as long as the block runs, heartbeating so a long job outlives
        its lease. `lost` turns True if the lock is taken away; long jobs should check it."""
        held = HeldJob(self.acquire(job, lease_s))
        beat = asyncio.create_task(self._keep_alive(held, lease_s))
        try:
            yield held
        finally:
            beat.cancel()
            with suppress(asyncio.CancelledError):
                await beat
            self.release(held.handle)

    async def _keep_alive(self, held: HeldJob, lease_s: float) -> None:
        while True:
            await self._clock.sleep(lease_s / 3)
            if not self.heartbeat(held.handle):
                held.lost = True
                return

    def statuses(self) -> list[JobStatus]:
        now = epoch(self._clock)
        return [
            JobStatus(
                job=r.job,
                pid=r.pid,
                host=r.host,
                age_s=now - r.acquired_at,
                heartbeat_age_s=now - r.heartbeat_at,
                stale_reason=self._stale_reason(r, now),
            )
            for r in self._repo.all()
        ]

    def _stale_reason(self, row: LockRow, now: float) -> str | None:
        if now > row.heartbeat_at + row.lease_s:
            return "lease expired"
        if row.host == self._host and not self._pid_alive(row.pid):
            return "holder process is gone"
        return None
