# Bundled animation

`default.bea.gz` is the animation the installer puts on a router that doesn't
have one yet. It is stored in the compact **BEA2** format (only the parts of
the screen that change are stored: 661 KB instead of 8.2 MB) and gzip-compressed
on top of that (about 170 KB); the installer unpacks it on the router.

* 199 frames at 8 frames per second, one 25-second loop
* two rounded white "eyes" that look around on a black background
* 76 x 284 pixels, the BE3600's front display (see [`../docs/BEA-FORMAT.md`](../docs/BEA-FORMAT.md))

The preview in the main README is rendered from real frames of this file.

To use it as a starting point or inspect it:

```sh
gunzip -k default.bea.gz                           # writes default.bea (BEA2) next to it
python3 ../tools/bea2.py info default.bea          # what is in it
python3 ../tools/bea2.py decode default.bea full.bea   # the plain full-frame BEA1 version
```

The full-frame (BEA1) version decodes to exactly the file this animation was
originally made as: 8,590,842 bytes, SHA-256
`17c7943efae58bfb87c2d03605a1802e674768c8547dd1f428178bd3151524e3`.

To swap it for your own on a router that already has it installed, drag a
`.bea` onto `Set-Animation.cmd` (Windows) or run `./set-animation.sh`
(macOS/Linux) from the top of the project.

It is released under the project's MIT license, like everything else here.
