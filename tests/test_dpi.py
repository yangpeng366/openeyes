"""The DPI probe must pass pointer-sized context handles whole.

Measured on Windows 11 (10.0.26340): ``GetAwarenessFromDpiAwarenessContext``
does not reliably write its out parameter, while
``AreDpiAwarenessContextsEqual`` answers correctly for every predefined
context.  Both paths need the same prerequisite, which is what these tests
pin: declared prototypes.  ctypes defaults an undeclared return type to
``c_int`` and an undeclared argument to a 32-bit int, so a 64-bit
``DPI_AWARENESS_CONTEXT`` is truncated - the reason an aware process used to
be reported as ``unaware``.
"""
from __future__ import annotations

import ctypes
import types

from openeyes.core import dpi

#: A context handle that only exists above the 32-bit boundary.
HANDLE = 0x000001F31A2B4C50


class _Function:
    """A ctypes-shaped function that records the prototype it was given."""

    def __init__(self, impl) -> None:
        self.impl = impl
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self.impl(self, *args)


def _fake_user32(awareness_handle: int = dpi._handle(-4)):
    """A user32 whose ``GetThreadDpiAwarenessContext`` honours its restype."""

    def get_context(fn: _Function) -> int:
        if fn.restype is ctypes.c_void_p:
            return HANDLE
        # What an undeclared restype does: keep the low 32 bits, signed.
        low = HANDLE & 0xFFFFFFFF
        return low - (1 << 32) if low >= (1 << 31) else low

    def contexts_equal(fn: _Function, first, second) -> int:
        if fn.argtypes is None:
            # Undeclared arguments are passed as C ints: the high half is gone.
            return 0
        if not isinstance(first, ctypes.c_void_p) or not isinstance(second,
                                                                    ctypes.c_void_p):
            return 0
        return 1 if (first.value == HANDLE
                     and second.value == awareness_handle) else 0

    def get_awareness(fn: _Function, ctx, out) -> int:
        return 0

    return types.SimpleNamespace(
        GetThreadDpiAwarenessContext=_Function(get_context),
        AreDpiAwarenessContextsEqual=_Function(contexts_equal),
        GetAwarenessFromDpiAwarenessContext=_Function(get_awareness),
        SetProcessDpiAwarenessContext=_Function(lambda fn, ctx: 1),
    )


def _install(monkeypatch, user32) -> None:
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=user32))
    monkeypatch.setattr(dpi, "_PROTOTYPES_SET", False)


def test_an_aware_context_is_reported_by_its_full_handle(monkeypatch):
    """The context is compared as a whole pointer, not its low 32 bits."""
    _install(monkeypatch, _fake_user32())

    assert dpi.current_dpi_awareness() == dpi.PER_MONITOR_AWARE_V2


def test_each_predefined_context_keeps_its_name(monkeypatch):
    expected = {
        -4: dpi.PER_MONITOR_AWARE_V2,
        -3: dpi.PER_MONITOR_AWARE,
        -2: dpi.SYSTEM_AWARE,
        -1: dpi.UNAWARE,
    }
    for handle, name in expected.items():
        _install(monkeypatch, _fake_user32(dpi._handle(handle)))

        assert dpi.current_dpi_awareness() == name


def test_a_failed_context_read_is_unaware_not_a_guess(monkeypatch):
    user32 = _fake_user32()
    user32.GetThreadDpiAwarenessContext = _Function(lambda fn: 0)
    _install(monkeypatch, user32)

    assert dpi.current_dpi_awareness() == dpi.UNAWARE


def test_ensure_upgrades_an_unaware_process_once(monkeypatch):
    _install(monkeypatch, _fake_user32(dpi._handle(-1)))
    monkeypatch.setattr(dpi, "_MODE", None)

    mode = dpi.ensure_dpi_awareness()

    assert mode == dpi.PER_MONITOR_AWARE_V2
