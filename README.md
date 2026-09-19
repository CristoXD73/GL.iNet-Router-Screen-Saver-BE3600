<div align="center">

# GL.iNet Router Screen Saver (BE3600)

**Give your GL.iNet Slate 7's little front screen an animated screen saver.**<br>
Robot eyes, colour fields, your own text, or anything you design.

[![CI](https://github.com/CristoXD73/GL.iNet-Router-Screen-Saver-BE3600/actions/workflows/ci.yml/badge.svg)](https://github.com/CristoXD73/GL.iNet-Router-Screen-Saver-BE3600/actions/workflows/ci.yml)
![Device](https://img.shields.io/badge/device-GL.iNet%20GL--BE3600%20(Slate%207)-2ea44f)
![Firmware](https://img.shields.io/badge/firmware-4.8.3-blue)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

<img src="docs/assets/animation.gif" alt="The bundled animation: two glowing eyes that look around" width="640">

<sub>The animation that comes with it, shown as the wide strip. It loops for 25 seconds.</sub>

</div>

---

## What is this?

The GL-BE3600 has a small screen on the front. After a few idle seconds, this
plays an animation on it. **Touch the screen and the normal GL.iNet screen comes
straight back.** It is easy to turn off and easy to remove completely.

* **One click to install** on Windows, macOS or Linux. No coding.
* **Comes with an animation**, so it works right away.
* **Make your own** in the [Motion Studio](#make-your-own-animation) in your browser.
* **Safe:** it doesn't change the router's firmware. It adds a few small files, and one command removes them.

## What you need

* A **GL.iNet GL-BE3600 (Slate 7)**. It is only tested on this router. See [Good to know](#good-to-know).
* A computer on the **same network** as the router (its Wi-Fi or a cable).
* The router's **admin password** (the one you use on its web page).

## Install

<div align="center">
<img src="docs/assets/steps.svg" alt="1. Download and unzip. 2. Double-click Install. 3. Enter your router password." width="820">
</div>

**Windows**

1. On this page click the green **Code** button, then **Download ZIP**. Unzip it.
2. Double-click **`Install.cmd`**.
3. When it asks, type your router's admin password and press Enter.
   *Nothing appears while you type. That is normal.*

**macOS / Linux**

```sh
./install.sh
```

That's it. The animation starts after the screen has been idle for a few
seconds, and it keeps working after the router restarts.

<details>
<summary><b>Something went wrong, or you want to know what it does</b></summary>

* **Windows says the script is blocked.** Right-click the ZIP, choose
  *Properties*, tick *Unblock*, click OK, then unzip it again. Or click
  *More info*, then *Run anyway*.
* **It can't find the router.** It tries the address that worked last time, then
  your computer's default gateway, then GL.iNet's factory address
  (`192.168.8.1`). If your router uses a different address, it asks you once and
  remembers it. You can also give it directly: `Install.cmd -Router 192.168.x.x`
  or `./install.sh 192.168.x.x`.
* **What is the password for?** It's the same as on the router's admin page. It
  is typed into the computer's own secure-login prompt (`ssh`); these scripts
  never see or store it.
* **What does it change?** It copies a few small files onto the router and
  starts a background service. The next section shows how to undo it.

More help: [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).
</details>

## Make your own animation

<div align="center">

### [Open Motion Studio](https://cristoxd73.github.io/GL.iNet-Router-Screen-Saver-BE3600/studio/)

<img src="https://github.com/user-attachments/assets/54829fd5-542e-4acf-9441-0a5379d06e56" alt="Motion Studio" width="720">

</div>

Motion Studio runs in your browser. There is nothing to install and nothing is
uploaded.

* **Atmosphere:** six slow, full-screen colour scenes (Silk, Tide, Orbit, Halo, Mesh, Cells).
* **Robot Eyes:** a library of animated eyes with 24 expressions.
* **Optimizer:** shows what an animation costs before you save it.

Click **Save**, and it downloads a file called `be3600-active.bea`. Then put it
on the router:

* **Windows:** drag the file onto **`Set-Animation.cmd`**. (Or double-click
  `Set-Animation.cmd` and drag the file into the window that opens.)
* **macOS / Linux:** run `./set-animation.sh`, then drag the file into the window.

The tool checks the file first and tells you in plain words if something is
wrong, then sends it to the router. You can drop several files in a row.

<details>
<summary><b>Smaller files, and making animations without the Studio</b></summary>

An animation is a `.bea` file. The Studio saves the plain kind (`BEA1`), which
stores every frame in full. `tools/bea2.py` converts to a compact kind (`BEA2`)
that stores only what changes, usually **10x or more smaller**, and back again
without losing anything:

```sh
python3 tools/bea2.py encode be3600-active.bea smaller.bea
```

To see a file as a GIF: `python3 tools/make-preview-gif.py file.bea preview.gif`
(needs `pip install pillow`). The format is described in
[`docs/BEA-FORMAT.md`](docs/BEA-FORMAT.md), and `tools/make-sample-bea.py` makes
a colour test and a scrolling rainbow you can try.
</details>

## Turn it off, or remove it

On the router, in a terminal (see [how to open one](docs/TROUBLESHOOTING.md#how-to-open-a-terminal-on-the-router)):

```sh
be3600-anim off            # switch it off; everything stays installed
be3600-anim on             # switch it back on
be3600-uninstall           # remove it completely (keeps your animation)
be3600-uninstall --purge   # remove everything, including your animation
```

Either way, the normal GL.iNet screen is put back exactly as it was.

<details>
<summary><b>All commands</b></summary>

| Command | What it does |
|---------|--------------|
| `be3600-anim on` | Turn it on now and after every restart. |
| `be3600-anim off` | Turn it off and restore the normal screen. |
| `be3600-anim status` | Is it on, who is drawing on the screen, and which animation. |
| `be3600-anim set FILE.bea` | Check `FILE.bea` and make it the animation. |
| `be3600-anim preview [FILE.bea] [SECONDS]` | Play an animation for a few seconds (default 10) without installing it, then restore everything. Touch to stop early. |
| `be3600-anim check` | Check the current animation and show how long it is. |
| `be3600-anim doctor` | Full health check. Run this first if something seems wrong. |

**Settings** are in `/etc/be3600-screen/config`. Run `be3600-anim on` after editing.

```sh
IDLE_SECONDS=10      # seconds without a touch before the animation starts (0 = at once)
TOUCH_DEVICE=""      # leave empty: the touchscreen is found automatically
PLAYER_ENGINE="auto" # "auto" uses the fast native player; "lua" forces the fallback
```
</details>

## Good to know

* **Only tested on a GL-BE3600** with firmware 4.8.3. The installer refuses to
  install on a device that doesn't have this display, and the screen saver
  refuses to draw on one with a different layout.
* **While the animation is showing, the normal screen is paused.** Touching the
  screen brings it back, and the animation returns when the screen is idle again.
* **Firmware updates:** the project's files are on the list OpenWrt keeps across
  a "keep settings" upgrade, but this hasn't been tried on a real upgrade. After
  an upgrade, the screen saver checks the display and touchscreen before taking
  over, and leaves the normal screen alone if anything looks different. If it is
  ever missing, just run the installer again.
* **Fast full-screen motion may tear a little.** This display can't do the tricks
  that prevent it ([why](docs/TEARING.md)), so gentle motion looks best.
* The Windows installer was tested on Windows 11. The macOS/Linux one was tested
  in a Linux container, **not on a real Mac**.

## For the curious

| | |
|---|---|
| [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md) | What runs on the router and why, including a touchscreen bug worth knowing about |
| [`docs/BEA-FORMAT.md`](docs/BEA-FORMAT.md) | The animation file format (`BEA1` and `BEA2`) |
| [`docs/TEARING.md`](docs/TEARING.md) | What was measured about tear-free drawing on this display |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Fixes for common problems |

```
Install.cmd        Windows: double-click to install
Set-Animation.cmd  Windows: drag a .bea onto it to change the animation
install.sh         macOS/Linux installer
set-animation.sh   macOS/Linux drag-and-drop tool
animations/        the bundled animation
studio/            Motion Studio (the browser tool)
router/            files that end up on the router (same paths as on the device)
setup/             scripts that run on the router during install and uninstall
native/            source of the fast player, its build script, a display probe
tools/             the Windows scripts, and helpers for making and converting animations
tests/             the test suite (run it with: sh tests/run.sh)
docs/              the guides above
```

## License

MIT, see [LICENSE](LICENSE), including the bundled animation. Motion Studio
uses Three.js and Vanta.js (both MIT); see [`studio/OPEN_SOURCE.md`](studio/OPEN_SOURCE.md).
Not affiliated with or endorsed by GL Technologies. Use at your own risk: this
takes over the router's front display while the animation plays.
