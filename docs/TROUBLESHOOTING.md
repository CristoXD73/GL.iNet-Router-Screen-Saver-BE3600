# Troubleshooting

Start with:

```
be3600-anim status
logread | grep be3600-screen | grep -v daemon.info | tail -n 20
```

## The animation flashes for about a second, then the normal screen returns

The touch helper is failing instantly. (The current supervisor retries instead
of treating that as a touch, but the animation cannot be dismissed while the
helper is broken.) Run it by hand while touching nothing:

```
timeout 4 lua /usr/bin/be3600-wait-touch.lua; echo $?
```

`124` is correct (it blocked until the timeout). `2` means it cannot read the
input device. Make sure the file contains `f:setvbuf("no")`; see
[HOW-IT-WORKS.md](HOW-IT-WORKS.md#the-buffering-pitfall). Also confirm
`/dev/input/event0` is the touchscreen:

```
cat /proc/bus/input/devices | grep -A4 -i touch
```

## `lua: ...be3600-player.lua:NN: unexpected symbol near '#'`

Lua comments start with `--`, not `#`. A shell-style `# comment` anywhere but the
`#!` first line is a syntax error. Check any file you edited:

```
lua -e "assert(loadfile('/usr/bin/be3600-player.lua'))"
```

## `Invalid BEA animation`, `Wrong framebuffer size`, or `INVALID: ...`

The file is not a valid `.bea` for this display. Run
`lua /usr/bin/be3600-bea-check.lua file.bea`; it names the exact problem. See
[BEA-FORMAT.md](BEA-FORMAT.md). Frames must be exactly 43168 bytes (76x284 at
16 bits per pixel).

## Colours are wrong

Generate the test pattern and compare:

```
python3 tools/make-sample-bea.py colors colors.bea
be3600-anim set colors.bea
```

It should show red, green, blue, white. If red and blue are swapped, change the
byte packing in `pack_pixel()` in that script.

## `install.sh` refuses to run

* "display is ... expected 76,284 at 16bpp": this is not a GL-BE3600 display.
  `FORCE=1 sh install.sh` overrides the check, but the frame size will not
  match and the player will refuse your animations.
* "no lua interpreter": install one with `opkg install lua`.

## Scripts fail with `not found` or odd characters

You edited a script on Windows and saved it with CRLF line endings. BusyBox
`sh` reads the `\r` as part of every command. Convert to LF, or use the
repository's `.gitattributes`, which forces LF. Beware PowerShell too: piping a
multi-line string to `ssh` re-adds CRLF; copy files with `scp` instead.

## `scp` says "subsystem request failed" / "sftp-server: not found"

OpenWrt's SSH server (dropbear) usually has no SFTP. Use the legacy protocol:
`scp -O ...`.

## After `be3600-anim off` the normal screen is blank

```
/etc/init.d/gl_screen restart
```

## Removing everything

`sh uninstall.sh` (or `sh uninstall.sh --purge` to also delete your animation
and config) puts the router back the way it was.
