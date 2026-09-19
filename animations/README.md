# Bundled animation

`default.bea.gz` is the animation the installer puts on a router that doesn't
have one yet. It is gzip-compressed (about 200 KB instead of 8.2 MB); the
installer unpacks it on the router.

* 199 frames at 8 frames per second, one 25-second loop
* two rounded white "eyes" that look around on a black background
* 76 x 284 pixels, the BE3600's front display (see [`../docs/BEA-FORMAT.md`](../docs/BEA-FORMAT.md))

The preview in the main README is rendered from real frames of this file.

To use it as a starting point or inspect it:

```sh
gunzip -k default.bea.gz          # writes default.bea next to it
```

To swap it for your own on a router that already has it installed, drag a
`.bea` onto `Set-Animation.cmd` (Windows) or run `./set-animation.sh`
(macOS/Linux) from the top of the project.

It is released under the project's MIT license, like everything else here.
