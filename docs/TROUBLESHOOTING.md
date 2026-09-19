# Troubleshooting

* [How to open a terminal on the router](#how-to-open-a-terminal-on-the-router)
* [First, ask the router what's wrong](#first-ask-the-router-whats-wrong)
* [The installer](#the-installer)
* [Motion Studio](#motion-studio)
* [Everyday problems](#everyday-problems)
* [Technical problems](#technical-problems)

## How to open a terminal on the router

Most fixes below are commands you type on the router. To get there:

1. **Open a terminal on your computer.** Windows: press the Start button, type
   `PowerShell`, press Enter. Mac: open *Terminal* (press Cmd+Space, type
   Terminal). Linux: open your terminal app.
2. **Connect to the router** by typing this and pressing Enter (use your
   router's address if it isn't the factory one):

   ```
   ssh root@192.168.8.1
   ```

3. The first time, it asks *"Are you sure you want to continue connecting?"*.
   Type `yes` and press Enter.
4. Type your router's **admin password** and press Enter. *Nothing shows as you
   type. That is normal.*
5. When you see a prompt ending in `#`, you are on the router. Type `exit` to
   leave.

## First, ask the router what's wrong

On the router, run:

```
be3600-anim doctor
```

It checks the display, the touchscreen, the animation and the service, and says
in plain words what is fine and what isn't. Lines marked `[FAIL]` are the
problem. `be3600-anim status` gives a shorter summary.

If you need the recent log:

```
logread | grep be3600-screen | grep -v daemon.info | tail -n 20
```

## The installer

### Windows says the script is blocked or from an untrusted source

The ZIP you downloaded is marked as coming from the internet. Right-click the
ZIP, choose **Properties**, tick **Unblock**, click OK, then unzip again. Or
choose "More info, Run anyway" on the warning.

### The installer says "Could not connect or log in"

* Is this computer connected to the router (its Wi-Fi or its LAN port)?
* The password is the router's **admin** password, the same one used on its
  admin web page. Nothing is shown while you type it.
* If it tried the wrong address, run it again with the right one:
  `Install.cmd -Router 192.168.x.x` (Windows) or `./install.sh 192.168.x.x`.
  The address that works is remembered for next time in
  `%LOCALAPPDATA%\be3600-screensaver\router.txt` (Windows) or
  `~/.config/be3600-screensaver/router` (macOS/Linux); delete that file to make
  it forget.

### The installer says the device "is not a GL-BE3600"

The address answered, but it isn't a router with the BE3600's front display (a
second router on your network, for example). Nothing was changed on it. The
installer asks for the right address and tries again.

### The router installer refuses to run

* "not a GL-BE3600 ... expected 76,284 at 16bpp": this isn't a GL-BE3600
  display. `FORCE=1 sh setup/router-install.sh` on the router overrides the
  check, but the frame size won't match and the player will refuse animations.
* "no lua interpreter": install one with `opkg install lua`.

## Motion Studio

### The drop zone says "Studio Link is not running"

Studio Link is the small helper that lets Motion Studio's drop zone send files to
your router. Use the **Download Studio Link** buttons on the page (Windows: double-click
the file; Mac/Linux: `python3 studio-link.py`), type your router password in its window,
and leave the window open. The square connects by itself within a few seconds and then
shows your router's three slots. (In a cloned repo, `Studio-Link.cmd` / `./studio-link.sh`
does the same.) Windows may warn about the downloaded `.cmd`; choose *Keep* and *Run anyway*.

**Connected, but the slots say "Empty" and it isn't right?** Close Studio Link and start it
again; it says "no router password" on the page if it was started without one.

Still not connecting?

* **Your browser asked about "devices on your local network"** and you said no. Allow
  it for the Motion Studio page (the padlock or site-settings icon in the address bar).
* **The port is busy.** Studio Link uses port 8791. If it says it can't start, another
  copy is already running; close it. To use another port: `Studio-Link.cmd -Port 8792`
  (the page always looks at 8791, so this is only for troubleshooting).
* **Using a copy of Motion Studio on your own web address?** Studio Link only accepts the
  official page, a local file, and `localhost`. Set `STUDIO_LINK_ORIGINS` to your
  address before starting it.
* **Your browser blocks it anyway** (some do). Nothing is lost: save the file and drag it
  onto **`Set-Animation.cmd`** instead.

### "The router did not accept that file"

The router holds at most 3 animations. Send the file under a name you already use to
replace that one, or remove one first: `be3600-anim list`, then
`be3600-anim remove NAME`. The router's own message is in the Studio Link window.

### The GIF looks cropped, or has bars

That's the fit mode in the GIF tab: **Fill** crops the edges to cover the strip (use
**Focus** to choose which part stays), **Fit** shows the whole picture with bars in the
background colour, and **Stretch** squashes it to the strip's shape.

## Everyday problems

### After `be3600-anim off` the normal screen is blank

```
/etc/init.d/gl_screen restart
```

### The animation doesn't start

Run `be3600-anim doctor`. The usual causes: it is switched off (`be3600-anim on`),
or the screen hasn't been idle long enough (the default is 10 seconds; see
`IDLE_SECONDS` in `/etc/be3600-screen/config`).

### The colours are wrong

Generate the test pattern and compare:

```
python3 tools/make-sample-bea.py colors colors.bea
be3600-anim set colors.bea
```

It should show red, green, blue, then white. If red and blue are swapped, change
the byte packing in `pack_pixel()` in that script.

### Removing everything

On the router, `be3600-uninstall` (or `be3600-uninstall --purge` to also delete
your animation and settings) puts it back the way it was.

## Technical problems

### The animation flashes for about a second, then the normal screen returns

The touch helper is failing instantly. Run it by hand while touching nothing:

```
timeout 4 lua /usr/bin/be3600-wait-touch.lua; echo $?
```

`124` is correct (it waited until the timeout). `2` means it cannot read the
input device. Make sure the file contains `f:setvbuf("no")`; see
[HOW-IT-WORKS.md](HOW-IT-WORKS.md#the-buffering-pitfall). Also confirm the
touchscreen was found: `lua /usr/bin/be3600-wait-touch.lua --which`.

### `lua: ...be3600-player.lua:NN: unexpected symbol near '#'`

Lua comments start with `--`, not `#`. A shell-style `# comment` anywhere except
the `#!` first line is a syntax error. Check any file you edited:

```
lua -e "assert(loadfile('/usr/bin/be3600-player.lua'))"
```

### `Invalid BEA animation`, `Wrong framebuffer size`, or `INVALID: ...`

The file isn't a valid `.bea` for this display. Run
`lua /usr/bin/be3600-bea-check.lua file.bea`; it names the exact problem. See
[BEA-FORMAT.md](BEA-FORMAT.md). Frames must be exactly 43168 bytes (76x284 at 16
bits per pixel).

### Scripts fail with `not found` or odd characters

You edited a script on Windows and saved it with CRLF line endings. BusyBox `sh`
reads the `\r` as part of every command. Convert to LF, or use the repository's
`.gitattributes`, which forces LF. PowerShell also re-adds CRLF when you pipe a
multi-line string to `ssh`; copy files with `scp -O` instead.

### `scp` says "subsystem request failed" / "sftp-server: not found"

Only relevant if you copy files by hand: OpenWrt's SSH server (dropbear) usually
has no SFTP. Use the legacy protocol: `scp -O ...`. (The installers avoid `scp`
entirely and stream the files through a single `ssh` session.)
