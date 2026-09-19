# The `.bea` animation format

A `.bea` file is a header followed by a list of records. It is deliberately
simple so it can be played by a small Lua script on a router.

There are two versions, told apart by the first four bytes. Both play on the
router and both use the `.bea` extension:

| Magic  | Stores                       | Use it when                                             |
|--------|------------------------------|---------------------------------------------------------|
| `BEA1` | every frame in full          | you generate frames yourself; it is the simplest thing  |
| `BEA2` | the first frame, then only what changed | you want small files (10x or more smaller for most animations) |

`tools/bea2.py` converts between them losslessly. This page describes `BEA1`
first (`BEA2` shares its header and pixel format), and `BEA2` [below](#bea2-changes-only).

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

## BEA2: changes only

The header is identical except that the magic is `BEA2`. After it come
`records` records, each with its own small header:

| Size            | Field    | Meaning                                                      |
|----------------:|----------|--------------------------------------------------------------|
| 2               | `run`    | how many ticks this picture stays on screen                  |
| 1               | `kind`   | `0` full frame, `1` delta, `2` hold                          |
| 4               | `length` | number of payload bytes that follow                          |
| `length`        | payload  | depends on `kind`, below                                     |

* **`kind 0`, full frame**: the payload is one whole frame (`frame_bytes`).
* **`kind 1`, delta**: the payload is a `u16` span count, then that many spans,
  each a `u32` byte offset, a `u16` length and that many bytes of new data. Each
  span overwrites part of the picture already on the display.
* **`kind 2`, hold**: no payload; the previous picture just stays longer.

Rules: the first record must be a full frame (the loop restarts from it), every
span must lie inside the frame, and the records must fill the file exactly.
`be3600-bea-check.lua` verifies all of that before a file is ever installed, so
the player never has to trust its input.

Playback is the same idea as `BEA1`: show each picture for `run / fps` seconds,
then loop. Because the display keeps its picture between records, the player
only rewrites the bytes that changed, which also means far less data is pushed
to the panel per frame.

To convert:

```
python3 tools/bea2.py encode  full.bea   small.bea    # BEA1 -> BEA2
python3 tools/bea2.py decode  small.bea  full.bea     # BEA2 -> BEA1 (byte-identical to the original)
python3 tools/bea2.py info    small.bea               # kinds, size, loop length
```

The bundled animation goes from 8.2 MB (`BEA1`) to 661 KB (`BEA2`).

## Checking a file

On the router:

```
be3600-anim check                     # the active animation
lua /usr/bin/be3600-bea-check.lua x.bea
```

It verifies the magic, `fps`, `frame_bytes`, the record structure (for `BEA2`,
every span of every delta) and the total size, and prints the loop length, e.g.
`OK x.bea: 199 frames, 200 ticks at 8 fps = 25.0 s per loop, 661381 bytes`.

## Making one

`tools/make-sample-bea.py` writes a valid `BEA1` file from scratch and is the
smallest working reference for the format. To convert your own video or
images, render each frame to 76x284, pack it as RGB565 little-endian, and
write it with the `BEA1` record layout; then run it through `tools/bea2.py
encode` to shrink it.
