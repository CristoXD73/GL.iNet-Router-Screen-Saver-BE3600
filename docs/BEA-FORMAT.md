# The `.bea` animation format

A `.bea` file is a header followed by a list of raw display frames. It is
deliberately trivial so it can be played by a 100-line Lua script on a router.

All integers are **little-endian**.

## Header (12 bytes)

| Offset | Size | Field        | Meaning                                                    |
|-------:|-----:|--------------|------------------------------------------------------------|
| 0      | 4    | magic        | the ASCII bytes `BEA1`                                     |
| 4      | 2    | `fps`        | ticks per second, 1 to 24                                  |
| 6      | 2    | `records`    | number of frames that follow                               |
| 8      | 4    | `frame_bytes`| size of one frame; must be `43168` on the GL-BE3600        |

## Records (`records` of them, back to back)

| Size          | Field  | Meaning                                                     |
|--------------:|--------|-------------------------------------------------------------|
| 2             | `run`  | how many ticks this frame stays on screen                   |
| `frame_bytes` | pixels | one raw frame, written verbatim to `/dev/fb0`               |

So a file is exactly `12 + records * (2 + frame_bytes)` bytes.

The player shows each frame for `run / fps` seconds, then loops back to the
first record forever. The loop length is therefore `sum(run) / fps` seconds.

## Pixels

The front display of the GL-BE3600 is a **76 x 284** panel (driver
`fb_st7789p3`) at **16 bits per pixel**, with a row stride of 152 bytes
(`76 * 2`, no padding), so `frame_bytes = 152 * 284 = 43168`.

Pixels are **RGB565, little-endian**, row by row from the top-left:

```
bit 15..11  red   (5 bits)
bit 10..5   green (6 bits)
bit  4..0   blue  (5 bits)
```

This was confirmed on a real GL-BE3600 with the `colors` pattern from
`tools/make-sample-bea.py` (red, green, blue, white appear as named).

## Worked example

A 4-frame, 2 fps file where each frame is held for 2 ticks:

```
header   12 bytes
4 * (2 + 43168)  = 172680 bytes
total            = 172692 bytes, loop length = (2+2+2+2) / 2 = 4.0 s
```

## Checking a file

On the router:

```
be3600-anim check                     # the active animation
lua /usr/bin/be3600-bea-check.lua x.bea
```

It verifies the magic, `fps`, `frame_bytes` and total size, and prints the
loop length, e.g.
`OK x.bea: 199 frames, 200 ticks at 8 fps = 25.0 s per loop, 8590842 bytes`.

## Making one

`tools/make-sample-bea.py` writes a valid file from scratch and is the
smallest working reference for the format. To convert your own video or
images, render each frame to 76x284, pack it as RGB565 little-endian, and
write it with the record layout above.
