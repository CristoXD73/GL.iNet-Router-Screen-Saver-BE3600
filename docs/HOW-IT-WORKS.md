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
on the first finger-down. It reports only "a finger landed". It is used while the
normal screen is up (to time the idle wait) and as the fallback described below;
while an animation plays, the native player reads the touchscreen itself.

### Gestures (native player)

While an animation plays, `be3600-player --gestures` reads `/dev/input/event0` in
the same process that draws the frames, so no touch can fall in a gap between
helper programs (which is what made the old double-tap unreliable) and switching
never restarts anything.

* **Drag along the strip:** the picture follows the finger and the next (or
  previous) saved animation comes in behind it. Let go past a quarter of the
  strip, or with a quick flick, and it slides the rest of the way; otherwise it
  springs back. With only one saved animation it stretches a little and springs back.
* **Tap:** after the double-tap window (0.3 s) with no second tap, slides to the next one.
* **Double-tap, or a swipe across the strip:** the player exits with code 10 and the
  supervisor gives the display back to the stock screen.
* Thresholds follow common touch conventions (a small "touch slop" before a
  touch counts as a drag, a time limit for a tap, speed for a flick).
* The touchscreen's coordinates are taken to be the display's own pixels
  (0..75 across, 0..283 along the strip), as the stock screen program does
  (it swaps axes and calibrates the same way through LVGL's evdev driver). The kernel
  reports a generic 0..240 range, so it is ignored. If a swipe goes the wrong way,
  `SWIPE_INVERT=1`; if the axes are swapped, `TOUCH_LONG_AXIS=X`. What the player
  saw is logged in `/tmp/be3600-player.log`.
* Exit codes: 10 = dismissed, 11 = the touchscreen could not be opened (the
  supervisor then falls back to the helper scheme below). The kernel drops a
  coordinate that has not changed, so the player reads the device's current
  position when it opens it.

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
* **While the animation runs** (native player), the supervisor only waits for the
  player, and reads its exit code: 10 means the user dismissed it, so the stock
  screen comes back; 11 means the touchscreen could not be opened, so it switches to
  the fallback below; anything else is a crash, and the player is restarted up to
  three times before the supervisor gives up and returns to the stock screen rather
  than leave a blank display. The player also copies the animation it switched to over
  `active.bea` (in a short-lived child process, so the flash write never delays a
  touch), so a restart resumes with the one you were watching.
* **The fallback (Lua player, or a touchscreen the native player cannot open):**
  the supervisor watches the touch helper and the player. When the helper reports a
  touch, it waits for the finger to lift and starts a second helper under
  `timeout DOUBLE_TAP_WINDOW_SECONDS`: another touch is a **double-tap** (back to the
  stock screen); a timeout was a lone tap, so it steps to the next file in the library
  (`/etc/be3600-screen/animations/`, alphabetical, wrapping around), copies it to
  `active.bea` and restarts the player. There are no swipes and no slide here, because
  the helpers only know "a finger landed", and touches that arrive between two helper
  starts are missed. That gap is why the native player handles touches itself.* A process is checked for being alive by reading `/proc/PID/status`, not just
  `kill -0`, because `kill -0` succeeds on a crashed background job that has
  not been reaped yet (a zombie), which would hide a dead player.
* On `SIGTERM`/`SIGINT`/`SIGHUP` it restores the stock screen and exits.
* `procd` respawns the supervisor if it exits unexpectedly; `be3600-anim off`
  stops and disables the service so nothing respawns it.

## Screen pages

The native player draws the pages itself. A page is a function that draws a 284 x 76 picture the way you look at
the strip; it is turned into the display's layout when it is sent to the screen. Pages and animations are all
"slots" in one carousel, so the drag-and-slide of the gestures works between any two of them.

`native/` is included by `be3600-player.c` in dependency order, each file named after what is in it:

| | |
|---|---|
| `draw.inc` | anti-aliased shapes, gradients and Inter type, onto a 284 x 76 canvas |
| `sample.inc` | the live numbers, and the files `be3600-widgetd` writes |
| `pages_basic.inc` | clock, network speed, vitals, router info |
| `pages_network.inc` | clients, internet, data usage, VPN, health |
| `qr.inc` | a QR encoder, for the Wi-Fi page |
| `pages_touch.inc` | alerts, and the pages a touch does something to |
| `pages_extra.inc` | analog, aurora, doctor, talkers |
| `pages.inc` | the table of them all |
| `hello.inc` | the welcome after installing |

* **Live numbers** (CPU, memory, storage, temperature, speed, data used) come from `/proc` and `/sys` once a second.
* **Collected data** (ping history, Wi-Fi clients, VPN, guest network, QR details, weather, custom scripts) is written
  by `be3600-widgetd` to `/tmp/be3600-widgets` as small text files. The supervisor starts it while the screensaver is
  showing, only if `PAGES` lists a page that needs it.
* **Who is using the internet** (`talkers`) needs no firewall rules: the kernel already counts the bytes of every
  connection (`nf_conntrack_acct`), so the helper reads `/proc/net/nf_conntrack`, remembers each flow's counters and
  adds up what grew since the last round. Traffic that never leaves the house is left out.
* **The network doctor** times each stop on its own (the ISP gateway, the internet, a name lookup), so the first one
  that fails is the one to blame.
* **The welcome** (`be3600-anim hello`, run at the end of an install) is `be3600-player --hello`: `native/hello.inc`
  draws the logo's two eyes with a few keyframe tracks and no assets at all, so it needs no `.bea` and no config.
  The CLI starts the `rev` chime in the background first, and both last 7.95 s, which is why the eyes widen exactly
  when the fan stabs and blink in the cuts between them. It takes the screen the same way `preview` does, and then
  leaves `/tmp/be3600-screen.show-now` so the supervisor skips its idle countdown: the welcome runs straight into
  the screensaver rather than bouncing off the stock screen first.
* **Why the rev sounds the way it does.** A rev cannot open with a punch. From a standstill the rotor needs half a
  second to break away and another 1.6 s to approach full speed, so the first two seconds have to be the engine
  starting; only then is there speed to cut away from. The pattern was searched against the measured model and then
  checked against the fan: predicted troughs of 3400 and 2960 rpm came out at 3615 and 2974, and a predicted peak
  of 5507 came out at 5596.
* **Chimes** (`FAN_CHIME=1`) go out through the only moving part the router has. `/sys/class/hwmon/hwmon0/pwm1` is the
  cooling fan's duty cycle, 0 to 255, and writing it is exactly what the stock `gl_fan` daemon does above 75 °C.
  `be3600-fan` writes short patterns to it and always restores the previous value, including from a signal handler;
  it refuses above 70 °C, so a chime can never come at the cost of cooling. Measured on the BE3600: duty 36 is
  1096 rpm and duty 255 is 5567 rpm, a speed change takes about 1.5 s to settle, and the tachometer only updates
  every 600 ms. There is no audible blade-passing tone, so chimes are shaped like an engine being
  revved -- an idle floor, stabs of throttle, a held note -- rather than as pitch or as bursts from silence.
* **Housekeeping** runs once a second inside the player, whatever is on screen: timers ring, alerts are read from the
  helper's `events` file, night mode sets the backlight, the schedule and autoplay move between pages.
* **Touch on a page:** a swipe moves; a single tap does the page's tap action if it has one (Pomodoro, stopwatch),
  otherwise goes to the next page; holding a finger down does the hold action (reset, guest Wi-Fi switch).
* **Tests:** `tests/widgets_test.py` draws every page against a fake router tree and decodes the QR codes;
  `tests/pages_touch_test.py` drives the touch behaviour; `tests/widgetd_test.py` checks the helper with fake router commands.
## Firmware changes

The supervisor remembers which firmware and kernel version it last checked (the
`verified` file). If the version is different, for example after an upgrade, it
first probes the display layout (76x284, 16 bpp, 152-byte stride), the stock
screen service and the touchscreen. If the probe passes, it remembers the new
version and carries on. If it fails, it leaves the normal screen alone, logs the
reason, and tries again every minute.

## Persistence

* **Reboots:** `be3600-anim on` enables the service (`/etc/rc.d/S81...`), and it
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
