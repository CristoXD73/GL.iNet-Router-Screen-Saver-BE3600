# be3600-screensaver

A custom animation screensaver for the small front display of the
**GL.iNet GL-BE3600 (Slate 7)** travel router.

After the display has been idle for a while (10 seconds by default), your
animation takes over the screen. Touch the screen and the normal GL.iNet UI
comes straight back; leave it alone and the animation returns. One command
turns the whole thing off, and one script removes it.

```
be3600-anim on | off | status | set FILE.bea | check
```

> **Status:** tested on one GL-BE3600 running GL.iNet firmware 4.8.3. It has
> not been tried on other models or firmware versions, and the "keep across a
> firmware upgrade" list has not been tested against a real upgrade. See
> [Limitations](#limitations).

## What you need

* A GL-BE3600 with SSH access as `root`.
* `lua` on the router (it is in GL.iNet's firmware; otherwise `opkg install lua`).
* An animation in `.bea` format. There is no ready-made animation in this
  repository; [`tools/make-sample-bea.py`](tools/make-sample-bea.py) generates
  test ones, and [`docs/BEA-FORMAT.md`](docs/BEA-FORMAT.md) describes the
  format so you can produce your own.

## Install

From the project directory on your computer:

```sh
# macOS / Linux
tools/deploy.sh 192.168.8.1
```
```powershell
# Windows (PowerShell)
powershell -File tools\deploy.ps1 -Router 192.168.8.1
```

(Add `-Key path\to\private_key` on Windows if you use a key file. `192.168.8.1`
is GL.iNet's default router address.)

Or copy the folder to the router yourself and run `sh install.sh` there.

The installer copies the files, adds them to `/etc/sysupgrade.conf`, and
enables the service. It only **starts** the screensaver if a valid animation is
already at `/etc/be3600-screen/active.bea`; otherwise it tells you what to do:

```sh
scp -O my-animation.bea root@192.168.8.1:/tmp/
ssh root@192.168.8.1 'be3600-anim set /tmp/my-animation.bea && be3600-anim on'
```

To try it without making an animation first:

```sh
python3 tools/make-sample-bea.py colors colors.bea   # red, green, blue, white
python3 tools/make-sample-bea.py scroll scroll.bea   # scrolling rainbow
```

## Use

Run these on the router (over SSH):

| Command | What it does |
|---------|--------------|
| `be3600-anim on` | Enable at boot and start now. |
| `be3600-anim off` | Stop, restore the stock screen, and don't start at boot. |
| `be3600-anim status` | Show whether it is enabled/running, who owns the display, and the active animation. |
| `be3600-anim set FILE.bea` | Check `FILE.bea` and make it the active animation. |
| `be3600-anim check` | Validate the active animation and print its loop length. |

The animation **stays until you turn it off or remove it**: it survives
reboots (`be3600-anim off` is the only thing that stops it coming back).
Touching the screen only dismisses it temporarily.

### Settings

`/etc/be3600-screen/config`:

```sh
ANIMATION="/etc/be3600-screen/active.bea"
IDLE_SECONDS=10      # seconds without a touch before the animation starts (0 = at once)
```

Run `be3600-anim on` after editing to apply.

## Remove

The installer puts a removal command on the router, so over SSH:

```sh
be3600-uninstall            # remove the screensaver, keep your animation + config
be3600-uninstall --purge    # remove everything, including /etc/be3600-screen
```

(It is the same file as `uninstall.sh` in this repository.) It stops and
disables the service, deletes the installed files, removes the
`sysupgrade.conf` entries, and makes sure the stock GL.iNet screen is running
again. The difference from `be3600-anim off`: `off` is a switch (everything
stays installed, easy to turn back on); `be3600-uninstall` deletes it.

## How it works

The short version: `gl_screen` (GL.iNet's UI) and this project's player must
never draw to `/dev/fb0` at the same time, so a small supervisor script hands
the display back and forth between them, using the touchscreen to decide when.
The full story, including a subtle input-device bug worth knowing about, is in
[`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md). Fixes for common problems are
in [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

## Limitations

* **Only tested on a GL-BE3600** (display `fb_st7789p3`, 76x284, touch
  controller Hynitron CST816X) with firmware 4.8.3. The installer refuses to
  run on a display with different dimensions unless you set `FORCE=1`.
* **While the animation is showing, GL.iNet's screen UI is stopped.** Touch the
  screen to get it back.
* **Firmware upgrades are untested.** The installer lists the project's files
  in `/etc/sysupgrade.conf` so a "keep settings" upgrade should preserve them,
  but this has not been verified. Re-run the installer if anything is missing.
* The player writes frames directly to `/dev/fb0` with no double buffering, so
  fast, full-screen motion may show tearing.
* Uses only what ships in the firmware (`sh`, `lua`, `procd`); nothing to
  compile.

## Layout

```
router/            files that end up on the router (same paths as on the device)
install.sh         install on the router (also installs be3600-uninstall)
uninstall.sh       the removal script (installed on the router as be3600-uninstall)
tools/             deploy.sh / deploy.ps1 (push from your computer), make-sample-bea.py
docs/              BEA-FORMAT, HOW-IT-WORKS, TROUBLESHOOTING
```

## License

MIT, see [LICENSE](LICENSE). Not affiliated with or endorsed by GL Technologies.
Use at your own risk; this replaces the display owner on your router.
