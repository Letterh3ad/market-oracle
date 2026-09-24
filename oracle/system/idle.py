from __future__ import annotations

import ctypes
import sys

__all__ = ["idle_seconds"]


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float | None:
    """How long since the last keyboard or mouse input on this PC; None where it cannot tell."""
    if sys.platform != "win32":
        return None
    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    kernel = ctypes.windll.kernel32
    kernel.GetTickCount64.restype = ctypes.c_uint64
    # dwTime is the 32-bit tick count, which wraps every 49.7 days: compare in 32 bits.
    return ((kernel.GetTickCount64() - info.dwTime) & 0xFFFFFFFF) / 1000.0
