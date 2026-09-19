# How it works

## In plain words

The router's front screen is normally drawn by GL.iNet's own program. This
project adds a small helper that watches the touchscreen. When nobody has
touched it for a few seconds, the helper stops GL.iNet's program and plays your
animation on the screen instead. A single tap steps to your next saved
animation; a quick double-tap stops the animation and starts GL.iNet's program
again, so the normal screen is back instantly. It never lets the two draw at the
same time, because that would scramble the picture.

The rest of this page is for people who want the details.

## The two owners of the display

The small front display of the GL-BE3600 is a single framebuffer (`/dev/fb0`,
driver `fb_st7789p3`). GL.iNet's own UI process, `gl_screen`, draws to it. Two
programs drawing to one framebuffer at once produce garbage, so this project
guarantees **exactly one owner at a time**:

```
        +--------------------------+
        |  STOCK                   |   gl_screen running, no player
        |  (normal GL.iNet UI)     |
        +--------------------------+
             |                 ^
   IDLE_SECONDS with        a double-tap
   no touch                 on the screen
             v                 |
        +--------------------------+ <--+
        |  CUSTOM                  |    | a single tap: next saved
        |  (your animation)        |    | animation, stay in CUSTOM
        +--------------------------+ ---+
        gl_screen stopped, be3600-player writing frames to /dev/fb0
```

Every hand-over kills the outgoing owner and waits for it to be gone *before*
starting the incoming one.

## Components

| File | Role |
|------|------|
| `/usr/bin/be3600-screensaver` | The supervisor. Runs the state machine above. Only one can run at a time (an atomic lock in `/tmp/be3600-screen.lock`). |
| `/usr/bin/be3600-player` | The native player: a small static aarch64 program that plays a `.bea` with exact timing. Source in `native/`. |
| `/usr/bin/be3600-player.lua`  | The same job in Lua, used if the native player isn't installed. |
| `/usr/bin/be3600-wait-touch.lua` | Finds the touchscreen and blocks until a finger touches it, then exits 0. |
| `/usr/bin/be3600-bea-check.lua` | Validates a `.bea` file (`BEA1` or `BEA2`) and prints its loop length. |
| `/usr/sbin/be3600-anim` | The user-facing control: `on`, `off`, `status`, `set`, `check`, `preview`, `doctor`. |
| `/etc/init.d/be3600-screensaver` | A `procd` service that runs the supervisor at boot and respawns it if it dies. |
| `/etc/be3600-screen/config` | The settings (idle time, touch device, player). |
| `/etc/be3600-screen/active.bea` | The animation that is played. |
| `/etc/be3600-screen/animations/` | The library: up to 3 saved animations by name, each looping 25 s or less. `set` saves the file here first, and keeps the animation it replaces as `previous`. |
| `/etc/be3600-screen/verified` | The firmware version the display was last checked against (see below). |

## Touch detection

The touch controller is a Hynitron CST816X exposed as `/dev/input/event0`.
When a finger lands it emits `ABS_X`/`ABS_Y` and `ABS_MT_TRACKING_ID = 0`, and
`ABS_MT_TRACKING_ID = -1` when it lifts. It does not send `BTN_TOUCH`.
`be3600-wait-touch.lua` reads 24-byte `struct input_event` records and exits 0
on the first finger-down. It reports only "a finger landed"; telling a single
tap from a double-tap is done by the supervisor (below), which only needs that.

### The buffering pitfall

The helper opens the device with `f:setvbuf("no")`. **This line matters.**

What was observed: with Lua's default buffered I/O, `f:read(24)` on this
device returned `nil` immediately, so the helper exited with its "could not
read the device" code on *every* call, with nobody touching anything. In an
earlier version of this project the supervisor treated that failure as a touch,
so every animation vanished about one second after it started. With buffering
off the helper blocks until a real touch and detects it correctly. The
hardware and kernel driver were fine throughout: a raw `dd` of
`/dev/input/event0` blocked and delivered events, and the touch controller's
interrupt counter rose on every tap.

The likely cause (not confirmed): input devices reject reads smaller than one
whole `struct input_event` (24 bytes), and a buffered stdio read on this
router's C library (musl) may reach the kernel slightly smaller than that.
Treat the mechanism as a hypothesis; the fix is what was verified.

## Supervisor behaviour worth knowing

* **Idle detection** runs while stock owns the display: the helper is run
  under `timeout IDLE_SECONDS`. Exit 0 means a touch (timer restarts); a timeout
  exit means idle.
* **While the animation runs**, the supervisor watches two processes: the touch
  helper and the player. When the helper reports a touch, the supervisor starts
  a second helper under `timeout DOUBLE_TAP_WINDOW_SECONDS`: if that reports
  another touch, it is a **double-tap** and the supervisor returns to the stock
  screen; if it times out, it was a lone tap, so the supervisor steps to the
  next file in the library (`/etc/be3600-screen/animations/`, alphabetical,
  wrapping around), copies it to `active.bea`, and restarts the player without
  ever leaving the animation. With fewer than two saved animations a lone tap
  changes nothing. If the player dies it is restarted, up to three times, and
  then the supervisor gives up and returns to the stock screen rather than
  leave a blank display.
* **Why taps, not swipes:** the panel is only 76 pixels wide, and the touch
  controller's coordinate range hasn't been calibrated here, so telling a swipe
  from a tap would depend on guessed distances. Timing two touches needs only the
  "did a finger land" signal that already works, and is easy to do on a strip
  this small.
* A process is checked for being alive by reading `/proc/PID/status`, not just
  `kill -0`, because `kill -0` succeeds on a crashed background job that has
  not been reaped yet (a zombie), which would hide a dead player.
* On `SIGTERM`/`SIGINT`/`SIGHUP` it restores the stock screen and exits.
* `procd` respawns the supervisor if it exits unexpectedly; `be3600-anim off`
  stops and disables the service so nothing respawns it.

## Firmware changes

The supervisor remembers which firmware and kernel version it last checked (the
`verified` file). If the version is different, for example after an upgrade, it
first probes the display layout (76x284, 16 bpp, 152-byte stride), the stock
screen service and the touchscreen. If the probe passes, it remembers the new
version and carries on. If it fails, it leaves the normal screen alone, logs the
reason, and tries again every minute.

## Persistence

* **Reboots:** `be3600-anim on` enables the service (`/etc/rc.d/S99...`), and it
  starts again on every boot. `be3600-anim off` removes that link.
* **Firmware upgrades:** the installer appends the project's files to
  `/etc/sysupgrade.conf`, which is the standard OpenWrt list of files kept by a
  "keep settings" upgrade. **This has not been tested against a real firmware
  upgrade.** Run the installer again if anything is missing afterwards.

## How the installer works

`Install.cmd` / `install.sh` never copy files one by one. They pack `router/`,
`setup/` and `animations/` into a single tar stream and pipe it through **one**
`ssh` session that unpacks it into `/tmp` on the router and runs
`setup/router-install.sh`. One session means you type the password once, and no
`scp` (OpenWrt's SSH server has no SFTP) is involved. On Windows the stream is
attached with `cmd.exe`'s `<` redirection because PowerShell's own pipe
re-encodes data and would corrupt the archive.

`setup/router-install.sh` then: checks that this really is a BE3600-style
router (exit code 3 if not, which makes the desktop installer ask for another
address); installs the files and the `be3600-uninstall` command; unpacks the
bundled animation only if the router has no valid one; and starts the service.

`Set-Animation.cmd` / `set-animation.sh` validate a `.bea` locally first, then
stream just that file into `be3600-anim set` on the router in one session.
