#!/usr/bin/env python3
"""Turn a .bea animation into a GIF (for READMEs and sharing). Needs Pillow.

    pip install pillow
    python3 tools/make-preview-gif.py animations/default.bea docs/animation.gif [scale]

The GIF shows the wide 284 x 76 view of the display, in a dark rounded frame.
Both .bea versions are accepted. Frame timing matches the animation.
"""
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bea2  # noqa: E402  (same folder)

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("This needs Pillow:  pip install pillow")

FW, FH = 76, 284          # the frame as the router stores it
LW, LH = 284, 76          # the wide view


def frames_of(path):
    """-> (fps, [(run, frame_bytes)]) for BEA1 or BEA2."""
    data = open(path, "rb").read()
    magic, fps, _ = bea2.read_header(data)
    if magic == b"BEA1":
        return bea2.read_bea1(data)
    out, cur = [], None
    for run, kind, payload in bea2.read_bea2(data)[1]:
        if kind == bea2.FULL:
            cur = payload
        elif kind == bea2.DELTA:
            cur = bea2.apply_delta(cur, payload)
        out.append((run, cur))
    return fps, out


def to_wide(frame):
    """RGB565 portrait frame -> PIL image of the wide view (rotated clockwise)."""
    img = Image.new("RGB", (FW, FH))
    px = img.load()
    for y in range(FH):
        for x in range(FW):
            v = frame[y * FW * 2 + x * 2] | (frame[y * FW * 2 + x * 2 + 1] << 8)
            r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
            px[x, y] = ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))
    return img.transpose(Image.ROTATE_90)      # turn the strip so it reads left to right


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    scale = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    fps, frames = frames_of(src)

    pad = 14 * scale
    w, h = LW * scale + 2 * pad, LH * scale + 2 * pad
    bg = (238, 241, 246)
    images, durations = [], []
    for run, frame in frames:
        wide = to_wide(frame).resize((LW * scale, LH * scale), Image.LANCZOS)
        canvas = Image.new("RGB", (w, h), bg)
        mask = Image.new("L", wide.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, wide.size[0] - 1, wide.size[1] - 1), radius=10 * scale, fill=255)
        canvas.paste(wide, (pad, pad), mask)
        images.append(canvas.quantize(colors=48, method=Image.MEDIANCUT, dither=Image.NONE))
        durations.append(int(1000 * run / fps))

    images[0].save(dst, save_all=True, append_images=images[1:], duration=durations, loop=0,
                   optimize=True, disposal=1)
    print("wrote %s: %d frames, %.1f s, %d KB" % (dst, len(images), sum(durations) / 1000, os.path.getsize(dst) // 1024))


if __name__ == "__main__":
    main()
