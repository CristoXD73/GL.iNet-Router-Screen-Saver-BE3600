# Security

## Reporting a problem

Please report a security problem **privately**, not in a public issue: use GitHub's
[private vulnerability reporting](https://github.com/CristoXD73/GL.iNet-Router-Screen-Saver-BE3600/security/advisories/new)
(the *Security* tab, then *Report a vulnerability*). You'll get a reply as soon as the
maintainer sees it, and a fix and credit (if you want it) once it's resolved.

## What this project does that is worth knowing about

* **Studio Link** is a small program you run on your own computer. It listens only on
  `127.0.0.1`, only answers a page from Motion Studio's own site (or `localhost`) **that also
  holds a secret token** made fresh each time it starts, and only ever accepts a valid `.bea`
  animation. It never accepts `Origin: null`.
* **Your router password** is typed into its own window, held in memory only, given to `ssh`
  through the environment of that one `ssh` process for the moment it runs, and never written
  anywhere.
* **"Remember this computer"** (optional) keeps a private key on your computer, locked with a
  random passphrase that only your Windows account (DPAPI), macOS Keychain or Linux keyring can
  open. The key gives root access to the router; `Studio-Link.cmd -Forget` (or `--forget`)
  removes it from both places.
* **GIF search** (Motion Studio's GIF tab) talks to only two services, and only when you press
  Search: Wikimedia Commons through Openverse (no account), or Giphy if you paste a free key
  of your own (kept in your browser only). They see the words you search for, as any web search
  would. The page's Content-Security-Policy allows no other address, results are only used if
  they point at those services' own hosts, and a downloaded GIF goes through the same size
  limits as any GIF you choose yourself. Studio Link and its token are never involved.
* **Screen pages** come on with the install, except the two that reveal or fetch something: the Wi-Fi QR page
  and the weather page stay off until you add them (Motion Studio's *Screen pages*, or `be3600-anim pages`).
  The page list is changed through Studio Link only with its token, and only to known page names. What they touch: the internet page
  pings `PING_TARGET` (default 1.1.1.1) and times the replies; the weather page asks open-meteo.com for the forecast
  of the coordinates you put in the config (and only then); the guest switch runs `uci` and `wifi reload` when you
  hold your finger on it; the Wi-Fi QR page shows the network's password to anyone who can see the screen. Collected
  data lives in memory (`/tmp/be3600-widgets`, the password file readable only by root). Scripts in
  `widgets.d` run as root, so only put your own there.
* **Motion Studio served by Studio Link** (the default on a Mac, where Safari cannot reach Studio Link from
  the website): only the page's own files (`index.html`, `fan.html`, `shared.*`, `vendor/*.js`) are served, from
  memory, to 127.0.0.1 only, with the same Host/Origin checks as every other request, `no-store`, `nosniff`
  and no framing. Nothing else on the computer can be requested through it.
* **The Install and Uninstall downloads** are Studio Link itself, with what it does fixed inside it; they
  open no port at all. The router address is checked the same way (plain addresses only, and a question
  before a password could go to an address outside home and office networks), the password is held in
  memory and handed to ssh only through the askpass helper's environment, never a command line, and
  *Save this login* stores a key (never the password) whose passphrase the keychain / DPAPI keeps. A
  login is only ever saved on a device confirmed to be a GL-BE3600. Uninstall removes those keys from
  the router (`be3600-uninstall --forget-keys`: only lines labelled `be3600-studio-link-...`) and from
  this computer, with the keychain entry.
* **The downloads** (``studio/downloads/``) are published with `SHA256SUMS`, and the router
  program they carry (`router/usr/bin/be3600-player`) is built reproducibly:
  `sh native/build-reproducible.sh --check` proves it matches `native/be3600-player.c`.

## Verifying a download

```sh
sha256sum -c SHA256SUMS        # in the folder with the downloads
```

```powershell
Get-FileHash Studio-Link.cmd -Algorithm SHA256    # compare with the line in SHA256SUMS
```
