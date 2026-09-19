#!/usr/bin/env python3
"""Builds the single-file downloads that Motion Studio offers, into studio/downloads/.

    python3 tools/build_downloads.py           write them
    python3 tools/build_downloads.py --check   exit 1 if the committed copies are out of date

  Studio-Link.cmd   Windows: ONE file to double-click (batch stub + the PowerShell code
                    from tools/studio-link.ps1 and tools/lib.ps1, unpacked to a temp file
                    when it runs)
  studio-link.py    macOS/Linux: ONE file to run with python3 (tools/studio_link.py with
                    tools/bea2.py folded in)

The website is static, so these are committed; tests/run.sh runs --check so they can
never fall behind the sources.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "studio", "downloads")


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


def build_cmd():
    lib = read("tools", "lib.ps1")
    link = read("tools", "studio-link.ps1")

    m = re.search(r"^param\(.*?^\)\n", link, re.S | re.M)
    dot = '. "$PSScriptRoot\\lib.ps1"\n'
    if not m or dot not in link:
        sys.exit("tools/studio-link.ps1 changed shape: expected a param(...) block and a dot-source of lib.ps1")

    header = link[:m.start()]
    param = m.group(0)
    rest = link[link.index(dot) + len(dot):]
    ps = "##PS-CODE##\n" + header + param + "\n" + lib + "\n" + rest
    if any(ord(c) > 127 for c in ps):
        sys.exit("the PowerShell code must stay ASCII")

    stub = (
        "@echo off\n"
        "rem Studio Link for Windows: ONE file. Double-click it, type your router's admin password\n"
        "rem when it asks, and leave the window open; Motion Studio's drop zone then connects to it.\n"
        "title GL.iNet Router Screen Saver (BE3600) - Studio Link\n"
        'set "SELF=%~f0"\n'
        'set "PSF=%TEMP%\\be3600-studio-link.ps1"\n'
        "rem Unpack the PowerShell code below the marker line into a temp file, run it, delete it.\n"
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:SELF); '
        "$i=$t.IndexOf('#'+'#PS-CODE#'+'#'); [IO.File]::WriteAllText($env:PSF,$t.Substring($i))\"\n"
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PSF%" %*\n'
        'del "%PSF%" >nul 2>&1\n'
        "echo.\n"
        "pause\n"
        "goto :eof\n"
    )
    return (stub + ps).replace("\n", "\r\n").encode("ascii")


def build_py():
    py = read("tools", "studio_link.py")
    bea2 = read("tools", "bea2.py")
    old = ("sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
           "import bea2  # noqa: E402  (same folder)\n")
    if old not in py:
        sys.exit("tools/studio_link.py changed shape: expected its bea2 import")
    new = ("# The animation-file reader from tools/bea2.py, folded in so this is a single file.\n"
           "import types  # noqa: E402\n"
           "bea2 = types.ModuleType(\"bea2\")\n"
           "exec(compile(%r, \"bea2.py\", \"exec\"), bea2.__dict__)\n" % bea2)
    return py.replace(old, new).encode("utf-8")


FILES = {"Studio-Link.cmd": build_cmd, "studio-link.py": build_py}


def main():
    check = "--check" in sys.argv
    stale = []
    for name, build in FILES.items():
        want = build()
        path = os.path.join(OUT, name)
        have = open(path, "rb").read() if os.path.exists(path) else None
        if check:
            if have != want:
                stale.append(name)
        else:
            os.makedirs(OUT, exist_ok=True)
            with open(path, "wb") as f:
                f.write(want)
            print("wrote studio/downloads/%s (%d bytes)" % (name, len(want)))
    if check:
        if stale:
            sys.exit("out of date: %s. Run: python3 tools/build_downloads.py" % ", ".join(stale))
        print("studio/downloads is up to date")


if __name__ == "__main__":
    main()
