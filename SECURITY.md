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
* **The downloads** (`studio/downloads/`) are published with `SHA256SUMS`, and the router
  program they carry (`router/usr/bin/be3600-player`) is built reproducibly:
  `sh native/build-reproducible.sh --check` proves it matches `native/be3600-player.c`.

## Verifying a download

```sh
sha256sum -c SHA256SUMS        # in the folder with the downloads
```

```powershell
Get-FileHash Studio-Link.cmd -Algorithm SHA256    # compare with the line in SHA256SUMS
```
