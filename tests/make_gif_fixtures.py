#!/usr/bin/env python3
"""Makes GIFs for tests/gif_decoder.test.js, with Pillow's own idea of every frame's
finished picture beside each one (Pillow is the reference the decoder is checked against).

    python3 tests/make_gif_fixtures.py OUT_DIR

Writes, for each of anim / synth / noise:  NAME.gif, NAME.ref (all frames, RGBA, one after
another) and NAME.json (size and each frame's delay).
"""
import json
import os
import random
import sys

from PIL import Image, ImageSequence

out = sys.argv[1]
repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(out, exist_ok=True)


def reference(name):
    im = Image.open(os.path.join(out, name + ".gif"))
    w, h = im.size
    raw, delays = b"", []
    for frame in ImageSequence.Iterator(im):
        raw += frame.convert("RGBA").tobytes()
        delays.append(frame.info.get("duration", 0))
    with open(os.path.join(out, name + ".ref"), "wb") as f:
        f.write(raw)
    with open(os.path.join(out, name + ".json"), "w") as f:
        json.dump({"w": w, "h": h, "delays": delays}, f)
    print("%s: %dx%d, %d frames" % (name, w, h, len(delays)))


# 1. The README animation: optimised by Pillow, so frames are just the changed
#    rectangle, drawn over the last one with transparency.
with open(os.path.join(repo, "docs", "assets", "animation.gif"), "rb") as src:
    with open(os.path.join(out, "anim.gif"), "wb") as dst:
        dst.write(src.read())
reference("anim")

# 2. Every disposal method, a transparent colour, and a different palette per frame.
random.seed(7)
W, H = 64, 48
base = [0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0, 255, 0, 255, 0, 255, 255, 255, 255, 255]   # 8 colours; index 0 = transparent
frames = []
for i in range(6):
    img = Image.new("P", (W, H), 0)
    pal = list(base)
    if i % 2:                                  # a different palette on odd frames
        pal[3:24] = base[6:24] + base[3:6]
    img.putpalette(pal + [0] * (768 - len(pal)))
    px = img.load()
    for y in range(6 + i * 4, 20 + i * 4):
        for x in range(4 + i * 6, 24 + i * 6):
            px[x, y] = 1 + (i % 6)
    if i == 3:                                  # one frame with scattered dots
        for _ in range(120):
            px[random.randrange(W), random.randrange(H)] = 2
    frames.append(img)
frames[0].save(os.path.join(out, "synth.gif"), save_all=True, append_images=frames[1:], duration=[0, 40, 90, 250, 100, 30],
               loop=0, transparency=0, disposal=[2, 3, 1, 0, 2, 1])
reference("synth")

# 3. Noise in 256 colours: fills and resets the compression dictionary over and over.
frames = []
for _ in range(3):
    img = Image.new("P", (160, 120))
    img.putpalette([random.randrange(256) for _ in range(768)])
    img.putdata([random.randrange(256) for _ in range(160 * 120)])
    frames.append(img)
frames[0].save(os.path.join(out, "noise.gif"), save_all=True, append_images=frames[1:], duration=60, loop=0)
reference("noise")
