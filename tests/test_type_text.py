"""The typing primitive must type the character it was given.

``ord(ch)`` is not a virtual-key code. Before this test, "," arrived as
VK_SNAPSHOT, "@" as a key that does not exist, and an upper-case letter
arrived lower-case because Shift was never pressed. These tests pin the
translation, not the keyboard: they ask the active layout and check the
answer is a real key plus the right Shift flag.
"""
from __future__ import annotations

import contextlib
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="Win32 keyboard layout only")

from openeyes.actuators import win32 as w  # noqa: E402
from openeyes.actuators.win32 import _vk_for_char  # noqa: E402


def test_letters_ask_for_shift_only_when_upper_case():
    lower = _vk_for_char("a")
    upper = _vk_for_char("A")
    assert lower is not None and upper is not None
    assert lower[0] == upper[0]
    assert lower[1] is False
    assert upper[1] is True


def test_digits_and_punctuation_get_real_keys():
    comma = _vk_for_char(",")
    at = _vk_for_char("@")
    assert comma is not None and comma[0] == 0xBC  # VK_OEM_COMMA
    assert comma[1] is False
    assert at is not None and at[0] != ord("@")


def test_signs_that_need_shift_ask_for_it():
    question = _vk_for_char("?")
    assert question is not None
    assert question[1] is True


def test_a_character_the_layout_cannot_type_is_refused():
    assert _vk_for_char("\u4e2d") is None


class _RecordingUser32:
    """SendInput stub that records packets instead of delivering them."""

    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.events: list[tuple[int, int, int]] = []

    def SendInput(self, count, events, size):
        if not self.accept:
            return 0
        for i in range(count):
            self.events.append((
                int(events[i].type),
                int(events[i].ki.wScan),
                int(events[i].ki.dwFlags),
            ))
        return count


def test_ascii_goes_out_as_unicode_packets_not_layout_keys(monkeypatch):
    """A Chinese IME must not be able to turn a URL into pinyin candidates."""
    fake = _RecordingUser32()
    monkeypatch.setattr(w, "user32", fake)
    w.type_text("a@b")
    assert [e[1] for e in fake.events] == [ord(ch) for ch in "a@b" for _ in range(2)]
    assert [bool(e[2] & w.KEYEVENTF_UNICODE) for e in fake.events] == [True] * 6
    assert [bool(e[2] & w.KEYEVENTF_KEYUP) for e in fake.events] == [False, True] * 3


def test_cjk_text_is_typed_verbatim(monkeypatch):
    fake = _RecordingUser32()
    monkeypatch.setattr(w, "user32", fake)
    w.type_text("\u6587\u7a3f")
    assert [e[1] for e in fake.events[::2]] == [ord("\u6587"), ord("\u7a3f")]


def test_refused_packets_fall_back_to_layout_with_ime_pinned_english(monkeypatch):
    fake = _RecordingUser32(accept=False)
    monkeypatch.setattr(w, "user32", fake)
    typed: list[str] = []
    monkeypatch.setattr(w, "_type_by_layout",
                        lambda text, interval: typed.append(text))
    monkeypatch.setattr(w, "_ascii_ime_guard", contextlib.nullcontext)
    w.type_text("https://example.com")
    assert typed == ["https://example.com"]


class _FakeImm:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.himc = 4242

    def ImmGetContext(self, hwnd):
        return self.himc

    def ImmGetConversionStatus(self, himc, conv, sent):
        conv._obj.value = w.IME_CMODE_NATIVE
        sent._obj.value = 0
        return 1

    def ImmGetOpenStatus(self, himc):
        return 1

    def ImmSetConversionStatus(self, himc, conv, sent):
        self.calls.append(("set", conv))
        return 1

    def ImmSetOpenStatus(self, himc, opened):
        self.calls.append(("open", opened))
        return 1

    def ImmNotifyIME(self, himc, action, index, data):
        self.calls.append(("notify", action))
        return 1

    def ImmReleaseContext(self, hwnd, himc):
        self.calls.append(("release", himc))
        return 1


def test_ime_guard_pins_english_then_restores_the_previous_mode(monkeypatch):
    imm = _FakeImm()
    monkeypatch.setattr(w, "_imm32", lambda: imm)
    with w._ascii_ime_guard():
        pass
    pinned = imm.calls.index(("set", w.IME_CMODE_ALPHANUMERIC))
    restored = imm.calls.index(("set", w.IME_CMODE_NATIVE))
    assert pinned < restored
    assert imm.calls[-1] == ("release", imm.himc)
