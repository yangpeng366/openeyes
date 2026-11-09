"""Check the overlay the only way that counts: look at the screen.

An agent's cursor is a picture on somebody's desktop, so the proof is
a pixel of the agent's colour appearing where it was asked to appear -
and the user's foreground window surviving the whole thing.

    python examples\overlay-check.py
    python examples\overlay-check.py --agent-id worker-b --label B

The cursor is an ordinary topmost window cut to the shape of the
cursor, so a plain screen grab sees it: no layered-window trickery is
needed to look at it, and no special capture path either.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops

from openeyes.agent.overlay import CursorOverlay
from openeyes.core.capture import capture_screen


def _colour_mask(image: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
    """A mask of the pixels that are exactly this colour."""
    red, green, blue = image.convert("RGB").split()
    mask = None
    for band, value in zip((red, green, blue), rgb, strict=True):
        same = band.point(lambda v, value=value: 255 if v == value else 0)
        mask = same if mask is None else ImageChops.multiply(mask, same)
    return mask


def _count_colour(image: Image.Image, rgb: tuple[int, int, int]) -> int:
    """How many pixels of the screen are exactly this colour."""
    return _colour_mask(image, rgb).histogram()[255]


def _colour_box(image: Image.Image,
                rgb: tuple[int, int, int]) -> tuple[int, int, int, int] | None:
    """The box the colour occupies on the screen, if it is there."""
    return _colour_mask(image, rgb).getbbox()


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

    from openeyes.core.dpi import ensure_dpi_awareness
    ensure_dpi_awareness()

    rgb = tuple(int(args.color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    before = _count_colour(capture_screen(), rgb)
    foreground_before = int(win32gui.GetForegroundWindow())

    overlay = CursorOverlay(agent_id=args.agent_id, label=args.label,
                            color=args.color)
    overlay.show(args.x, args.y)
    time.sleep(args.seconds)
    shown_image = capture_screen()
    shown = _count_colour(shown_image, rgb)
    box = _colour_box(shown_image, rgb)
    foreground_after = int(win32gui.GetForegroundWindow())

    overlay.hide()
    time.sleep(0.5)
    hidden = _count_colour(capture_screen(), rgb)
    overlay.close()

    size = overlay.render().size
    print(f"agent={args.agent_id} colour={args.color} at "
          f"({args.x},{args.y}) window={size}")
    print(f"pixels of the agent colour: before={before} shown={shown} "
          f"hidden={hidden}")
    print(f"the colour sits in {box}")
    print(f"foreground window: before={foreground_before} "
          f"after={foreground_after} unchanged={foreground_before == foreground_after}")

    where = (box is not None
             and box[0] >= args.x and box[1] >= args.y
             and box[2] < args.x + size[0] and box[3] < args.y + size[1])
    if (shown > before and hidden <= before
            and foreground_before == foreground_after and where):
        print("OK: the cursor is on the screen where it was asked to be, "
              "and the user kept the focus")
        return 0
    print("FAILED: the cursor did not behave as promised")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
