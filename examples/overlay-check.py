"""Check the overlay the only way that counts: look at the screen.

An agent's cursor is a picture on somebody's desktop, so the proof is
a pixel of the agent's colour appearing where it was asked to appear -
and the user's foreground window surviving the whole thing.

The capture uses GDI ``BitBlt`` with ``CAPTUREBLT``: ordinary screen
grabbing skips layered windows, which is exactly what an overlay is.

    python examples\\overlay-check.py
    python examples\\overlay-check.py --agent-id worker-b --label B
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from openeyes.agent.overlay import CursorOverlay

#: The screen copy flag that includes layered windows.
CAPTUREBLT = 0x40000000
SRCCOPY = 0x00CC0020


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _capture_screen() -> Image.Image:
    """A screen copy that includes layered windows."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_int]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_uint]
    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                             ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]

    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    gdi32.SelectObject(memory, bitmap)
    gdi32.BitBlt(memory, 0, 0, width, height, screen, 0, 0,
                 SRCCOPY | CAPTUREBLT)
    info = _BitmapInfoHeader()
    info.biSize = ctypes.sizeof(_BitmapInfoHeader)
    info.biWidth = width
    info.biHeight = -height
    info.biPlanes = 1
    info.biBitCount = 32
    info.biCompression = 0
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(info), 0)
    image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA",
                              0, 1).convert("RGB")
    gdi32.DeleteDC(memory)
    gdi32.DeleteObject(bitmap)
    return image


def _count_colour(image: Image.Image, rgb: tuple[int, int, int]) -> int:
    total = 0
    for count, colour in image.getcolors(maxcolors=4_000_000):
        if colour == rgb:
            total += count
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent-id", default="worker-a")
    parser.add_argument("--label", default="A")
    parser.add_argument("--color", default="#00c853")
    parser.add_argument("--x", type=int, default=300)
    parser.add_argument("--y", type=int, default=200)
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()

    import win32gui

    rgb = tuple(int(args.color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    before = _count_colour(_capture_screen(), rgb)
    foreground_before = int(win32gui.GetForegroundWindow())

    overlay = CursorOverlay(agent_id=args.agent_id, label=args.label,
                            color=args.color)
    overlay.show(args.x, args.y)
    time.sleep(args.seconds)
    shown = _count_colour(_capture_screen(), rgb)
    foreground_after = int(win32gui.GetForegroundWindow())

    overlay.hide()
    time.sleep(0.5)
    hidden = _count_colour(_capture_screen(), rgb)
    overlay.close()

    print(f"agent={args.agent_id} colour={args.color} at "
          f"({args.x},{args.y}) window={overlay.render().size}")
    print(f"pixels of the agent colour: before={before} shown={shown} "
          f"hidden={hidden}")
    print(f"foreground window: before={foreground_before} "
          f"after={foreground_after} unchanged={foreground_before == foreground_after}")
    if shown > before and hidden <= shown and foreground_before == foreground_after:
        print("OK: the cursor is on the screen, and the user kept the focus")
        return 0
    print("FAILED: the cursor did not behave as promised")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
