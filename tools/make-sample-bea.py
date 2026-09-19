#!/usr/bin/env python3
"""Generate a small test animation (.bea) for the BE3600 screensaver.

No dependencies beyond the Python 3 standard library.

    python3 make-sample-bea.py colors  colors.bea   # red, green, blue, white: check colour order
    python3 make-sample-bea.py scroll  scroll.bea   # a scrolling rainbow: check motion

Then, on the router:  be3600-anim set colors.bea

The frame layout is described in docs/BEA-FORMAT.md. The display is 76x284
pixels, 16 bits per pixel, and each frame is written to /dev/fb0 as-is.
This script packs each pixel as RGB565, little-endian. If the colours of the
"colors" pattern look wrong on your screen, adjust pack_pixel() below.
"""
import colorsys
import struct
import sys

WIDTH, HEIGHT = 76, 284
STRIDE = WIDTH * 2                 # bytes per row (matches /sys/class/graphics/fb0/stride)
FRAME_BYTES = STRIDE * HEIGHT      # 43168


def pack_pixel(r, g, b):
    """RGB888 -> 16-bit RGB565, little-endian."""
    value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return struct.pack("<H", value)


def make_frame(pixel_fn):
    """pixel_fn(x, y) -> (r, g, b). Returns FRAME_BYTES bytes."""
    rows = bytearray()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            rows += pack_pixel(*pixel_fn(x, y))
    assert len(rows) == FRAME_BYTES
    return bytes(rows)


def write_bea(path, fps, frames):
    """frames: list of (run, frame_bytes); run = ticks (1/fps s) the frame stays up."""
    with open(path, "wb") as f:
        f.write(b"BEA1")
        f.write(struct.pack("<HHI", fps, len(frames), FRAME_BYTES))
        for run, data in frames:
            assert len(data) == FRAME_BYTES
            f.write(struct.pack("<H", run))
            f.write(data)


def colors(path):
    solid = lambda rgb: make_frame(lambda x, y: rgb)
    write_bea(path, fps=2, frames=[
        (2, solid((255, 0, 0))),      # 1 s red
        (2, solid((0, 255, 0))),      # 1 s green
        (2, solid((0, 0, 255))),      # 1 s blue
        (2, solid((255, 255, 255))),  # 1 s white
    ])


def scroll(path, steps=24):
    frames = []
    for i in range(steps):
        def px(x, y, i=i):
            hue = ((y + i * HEIGHT / steps) % HEIGHT) / HEIGHT
            r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            return int(r * 255), int(g * 255), int(b * 255)
        frames.append((1, make_frame(px)))
    write_bea(path, fps=12, frames=frames)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("colors", "scroll"):
        sys.exit(__doc__)
    {"colors": colors, "scroll": scroll}[sys.argv[1]](sys.argv[2])
    print("wrote", sys.argv[2])
