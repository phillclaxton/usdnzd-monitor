#!/usr/bin/env python3
"""Generate the add-on icon and logo.

The images are produced from code rather than committed as opaque binaries so
they can be regenerated and reviewed.  Home Assistant expects a square
``icon.png`` and a wider ``logo.png``; both are written with only the standard
library.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

Colour = tuple[int, int, int]

BACKGROUND: Colour = (16, 26, 41)
LADDER: Colour = (56, 189, 248)
LADDER_TOP: Colour = (74, 222, 128)
TEXT: Colour = (226, 232, 240)

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = REPO_ROOT / "fx_strategy"


class Canvas:
    def __init__(self, width: int, height: int, fill: Colour) -> None:
        self.width = width
        self.height = height
        self.pixels = [[fill for _ in range(width)] for _ in range(height)]

    def rect(self, x: int, y: int, w: int, h: int, colour: Colour, radius: int = 0) -> None:
        for row in range(max(y, 0), min(y + h, self.height)):
            for col in range(max(x, 0), min(x + w, self.width)):
                if radius:
                    dx = min(col - x, x + w - 1 - col)
                    dy = min(row - y, y + h - 1 - row)
                    if dx < radius and dy < radius:
                        if (radius - dx) ** 2 + (radius - dy) ** 2 > radius * radius:
                            continue
                self.pixels[row][col] = colour

    def to_png(self) -> bytes:
        raw = bytearray()
        for row in self.pixels:
            raw.append(0)  # filter type: none
            for r, g, b in row:
                raw.extend((r, g, b))

        def chunk(tag: bytes, payload: bytes) -> bytes:
            body = tag + payload
            return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )


def draw_ladder(canvas: Canvas, left: int, bottom: int, width: int, height: int) -> None:
    """Five ascending bars: the tranche ladder the product is built around."""
    steps = 5
    bar_width = width // (steps * 2 - 1)
    gap = bar_width
    for index in range(steps):
        bar_height = int(height * (index + 1) / steps)
        x = left + index * (bar_width + gap)
        colour = LADDER_TOP if index == steps - 1 else LADDER
        canvas.rect(x, bottom - bar_height, bar_width, bar_height, colour, radius=2)


def build_icon(size: int = 256) -> Canvas:
    canvas = Canvas(size, size, BACKGROUND)
    margin = size // 8
    draw_ladder(canvas, margin, size - margin, size - 2 * margin, size - 2 * margin)
    # A baseline, so the bars read as a chart rather than a barcode.
    canvas.rect(margin, size - margin, size - 2 * margin, max(size // 64, 2), TEXT)
    return canvas


def build_logo(width: int = 500, height: int = 250) -> Canvas:
    canvas = Canvas(width, height, BACKGROUND)
    draw_ladder(canvas, 40, height - 50, 180, height - 100)
    canvas.rect(40, height - 50, 180, 4, TEXT)
    # A simple wordmark block: an arrow from a low band to a high band.
    canvas.rect(260, height // 2 - 6, 90, 12, LADDER, radius=6)
    canvas.rect(350, height // 2 - 30, 12, 60, LADDER_TOP, radius=6)
    canvas.rect(362, height // 2 - 30, 90, 12, LADDER_TOP, radius=6)
    return canvas


def main() -> None:
    ADDON_DIR.mkdir(parents=True, exist_ok=True)
    (ADDON_DIR / "icon.png").write_bytes(build_icon().to_png())
    (ADDON_DIR / "logo.png").write_bytes(build_logo().to_png())
    print(f"wrote {ADDON_DIR / 'icon.png'} and {ADDON_DIR / 'logo.png'}")


if __name__ == "__main__":
    main()
