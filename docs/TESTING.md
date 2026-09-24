# Running the tests

Everything is tested on an ordinary computer, with no router needed. Fake framebuffers,
touch devices and router tools stand in for the real ones. The same suite runs on Linux
and on macOS, and CI runs both, plus the Windows scripts on Windows.

```
sh tests/run.sh
```

It prints `All tests passed.` or the number of failures, and exits with that number.

## Linux (what CI runs on `ubuntu-latest`)

```
sudo apt-get install -y lua5.1 shellcheck file python3-pil openssh-client zbar-tools
LUA=lua5.1 sh tests/run.sh
```

Lua 5.1 is the version the router runs. The native player is built with the system
`cc` against the real Linux headers.

## macOS (what CI runs on `macos-latest`)

```
xcode-select --install          # cc, and the python3 the tests use
brew install luajit pillow shellcheck node
sh tests/run.sh
```

What is different on a Mac, and how the suite handles it:

- **Lua 5.1:** Homebrew has no Lua 5.1, so the suite uses LuaJIT, which runs Lua 5.1
  code. `tests/run.sh` finds `lua5.1`, `lua-5.1` or `luajit` by itself; set `LUA=...` to
  pick another.
- **The native player** is Linux C. On macOS it is built for the tests with
  [`tests/compat/`](../tests/compat): the few parts of `<linux/input.h>` it uses, and
  `ppoll()`. Only the test build uses them; the router build (`native/build.sh`) never
  does.
- **`timeout`:** macOS has none. The suite uses `gtimeout` (`brew install coreutils`) if it
  is there, and otherwise a small stand-in with the same exit code, 124.
- **`/usr/bin/lua`:** the double-tap check runs the router's own function, which calls
  `/usr/bin/lua`. macOS cannot have one there, so the check runs that function with only
  the interpreter's path changed.

Pillow (`pillow`) is needed for the screen-page tests; `node` is only needed for Motion
Studio's GIF decoder check, which is skipped without it.

## Windows

The PowerShell checks in `tests/windows_*.ps1` run in CI on `windows-latest`. See the
`windows` job in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).
