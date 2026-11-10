"""The typing primitive must type the character it was given.

``ord(ch)`` is not a virtual-key code. Before this test, "," arrived as
VK_SNAPSHOT, "@" as a key that does not exist, and an upper-case letter
arrived lower-case because Shift was never pressed. These tests pin the
translation, not the keyboard: they ask the active layout and check the
answer is a real key plus the right Shift flag.
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="Win32 keyboard layout only")

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