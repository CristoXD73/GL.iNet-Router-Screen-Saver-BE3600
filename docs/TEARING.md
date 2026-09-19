# Tearing: what is possible on the BE3600's display

**Short answer: hardware double buffering is not possible on this display with
this firmware. The project does what can be done in software.**

## What was measured

`native/fbprobe.c` asks the framebuffer driver what it supports. On a GL-BE3600
(driver `fb_st7789p3`, firmware 4.8.3, kernel 5.4.213):

| Question | Answer | Meaning |
|----------|--------|---------|
| Framebuffer memory (`smem_len`) | 43,168 bytes | exactly one frame; no spare memory for a second page |
| Virtual height vs visible height | 284 = 284 | no extra rows to draw into off-screen |
| Panning steps (`ypanstep`) | 0 | the driver does not support panning |
| `FBIOPAN_DISPLAY` | rejected (`EINVAL`) | so pages cannot be flipped |
| Asking for a taller virtual screen | accepted but ignored | the driver will not grow the buffer |
| `FBIO_WAITFORVSYNC` | not supported | there is no vertical-blank signal to wait for |
| `mmap` of the framebuffer | works | not a way around the above |

So the usual tear-free techniques (draw the next frame off-screen and flip, or
wait for vsync before writing) are all unavailable. Doing them would need a
different display driver, which is a kernel change, not something a screen saver
can install.

To reproduce (with the screen owners stopped so nothing is drawing):

```sh
be3600-anim off
/etc/init.d/gl_screen stop
./fbprobe --try-flip /dev/fb0      # restores the original mode afterwards
/etc/init.d/gl_screen start
be3600-anim on
```

`fbprobe` without `--try-flip` only asks questions and is safe to run any time.

## What the project does instead

* **One burst per frame.** A full frame is written with a single `write()`, and a
  `BEA2` delta writes only the ranges that changed, all back to back. Small
  framebuffer drivers like this one typically push the framebuffer to the panel a
  little after a write rather than instantly, so writes that arrive together
  should be sent together (this behaviour was not itself measured).
* **Less data changes.** With `BEA2`, an animation that moves a small object only
  rewrites that object's bytes (the bundled animation averages a few kilobytes a
  frame instead of 43 KB), which shortens the window in which the panel could
  catch a half-updated picture.
* **Exact timing.** The native player uses absolute deadlines, so frames arrive at
  a steady rhythm instead of drifting against the panel's refresh.

## What is not known

Whether tearing is *visible* has not been measured; that needs a high-speed
camera or a photodiode, not software. Slow or moderate motion (like the bundled
eyes) shows none to the eye. Fast full-screen motion is the case most likely to
show it; if you see it, lowering `fps` or changing less of the screen per frame
is the practical remedy.
