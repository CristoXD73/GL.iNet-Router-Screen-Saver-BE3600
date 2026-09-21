#!/usr/bin/env python3
"""Builds docs/assets/social.png: the 1280x640 card GitHub, Reddit, Discord, Slack and X show
when someone shares this repo.

    cc -O2 -o /tmp/pn native/be3600-player.c
    python3 tools/make-social.py /tmp/pn

The three screens on it are drawn by the player itself, from the same fake tree the page tests
use, so the card can never drift from what the router actually puts on the strip. Needs Pillow
and the Inter font (Debian: fonts-inter, or point INTER/DISPLAY at your own copy).
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)

sys.argv = ["make-social", sys.argv[1] if len(sys.argv) > 1 else "/tmp/pn"]
with open("tests/widgets_test.py") as f:                     # make_tree, render, to_image
    exec(f.read().split("def main():")[0])                   # noqa: S102  (our own file)

W, H = 1280, 640
INTER = os.environ.get("INTER", "/usr/share/fonts/opentype/inter/Inter-%s.otf")
DISPLAY = os.environ.get("DISPLAY_FONT", "/usr/share/fonts/opentype/inter/InterDisplay-%s.otf")


def font(path, weight, size):
    return ImageFont.truetype(path % weight, size)


# ---- the three screens ----------------------------------------------------------------
root = make_tree()                                           # noqa: F821  (from the exec above)
gif = Image.open("docs/assets/animation.gif")                # the bundled animation, as the strip
gif.seek(0)
strips = [gif.convert("RGB").crop((28, 28, 596, 180))]       # inside the GIF's device bezel
for page in ("clock", "netspeed"):
    data, err = render(root, page)                           # noqa: F821
    if not data:
        raise SystemExit("could not draw the %s page: %s" % (page, err))
    strips.append(to_image(data))                            # noqa: F821

# ---- background: near-black, with the two soft glows the studio pages use ---------------
bg = Image.new("RGB", (W, H), (9, 11, 15))
glow = Image.new("RGB", (W, H), (9, 11, 15))
g = ImageDraw.Draw(glow)
for cx, cy, r, col in ((110, 60, 460, (30, 74, 140)), (1180, 600, 460, (66, 48, 132)),
                       (700, 20, 380, (16, 44, 70))):
    for i in range(28):
        k = 1 - i / 28.0
        rr = r * (i + 1) / 28.0
        g.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                  fill=tuple(int(9 + (c - 9) * k * k) for c in col))
bg = Image.blend(bg, glow.filter(ImageFilter.GaussianBlur(80)), 0.9)

# ---- the screens, stacked on the right, each over its own glow --------------------------
SW, SH, GAP = 620, 166, 26
X0 = W - SW - 70
top = (H - (SH * 3 + GAP * 2)) // 2

halo = Image.new("RGB", (W, H), (9, 11, 15))
hd = ImageDraw.Draw(halo)
for i in range(3):
    y = top + i * (SH + GAP)
    hd.rounded_rectangle([X0 + 16, y + 14, X0 + SW - 16, y + SH + 6], 26, fill=(44, 96, 158))
bg = Image.blend(bg, halo.filter(ImageFilter.GaussianBlur(40)), 0.5)

for i, s in enumerate(strips):
    y = top + i * (SH + GAP)
    card = s.resize((SW, SH), Image.LANCZOS)
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SW - 1, SH - 1], 16, fill=255)
    bg.paste(card, (X0, y), mask)
    ImageDraw.Draw(bg).rounded_rectangle([X0, y, X0 + SW - 1, y + SH - 1], 16,
                                         outline=(54, 60, 72), width=2)

# ---- the words last, so no glow washes them out -----------------------------------------
d = ImageDraw.Draw(bg)
d.text((72, 168), "G L - B E 3 6 0 0   ·   S L A T E   7", font=font(INTER, "Bold", 17),
       fill=(120, 190, 255))
d.text((70, 206), "Router", font=font(DISPLAY, "Bold", 72), fill=(255, 255, 255))
d.text((70, 282), "Screen Saver", font=font(DISPLAY, "Bold", 72), fill=(255, 255, 255))
d.text((72, 384), "Animations, live pages, and chimes", font=font(INTER, "Regular", 25),
       fill=(154, 161, 174))
d.text((72, 418), "played on the cooling fan.", font=font(INTER, "Regular", 25),
       fill=(154, 161, 174))

bg.save("docs/assets/social.png")
print("wrote docs/assets/social.png %dx%d" % bg.size)
