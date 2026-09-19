#!/usr/bin/env python3
"""Builds the single-file downloads that Motion Studio offers, into studio/downloads/.

    python3 tools/build_downloads.py           write them
    python3 tools/build_downloads.py --check   exit 1 if the committed copies are out of date

  Studio-Link.cmd   Windows: ONE file to double-click (batch stub + the PowerShell code
                    from tools/studio-link.ps1 and tools/lib.ps1, unpacked to a temp file
                    when it runs)
  studio-link.py    macOS/Linux: ONE file to run with python3 (tools/studio_link.py with
                    tools/bea2.py folded in)

Both also carry the router's files (router/, setup/, animations/ as a .tar.gz) so that
Studio Link can put the screen saver on a router that does not have it yet, with nothing
else to download. The payload is packed deterministically and identified by a short hash;
--check compares the unpacked contents, so different zlib versions cannot fail it.

The website is static, so these are committed; tests/run.sh runs --check so they can
never fall behind the sources.
"""
import base64
import gzip
import hashlib
import io
import os
import re
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "studio", "downloads")
PAYLOAD_DIRS = ("router", "setup", "animations")
CMD_MARK = b"\n##PAYLOAD## "          # the payload follows this marker in Studio-Link.cmd


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


# ---------------------------------------------------------------------------------------
# The payload: what the router needs, packed the same way every time
# ---------------------------------------------------------------------------------------

def payload_tar():
    """-> the uncompressed tar of router/, setup/ and animations/ (byte-for-byte repeatable)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as t:
        for top in PAYLOAD_DIRS:
            base = os.path.join(ROOT, top)
            names = []
            for dirpath, dirs, files in os.walk(base):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                names += [os.path.join(dirpath, f) for f in sorted(files)]
            for path in names:
                rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                with open(path, "rb") as f:
                    data = f.read()
                if b"\0" not in data:                      # text: the router wants LF line endings
                    data = data.replace(b"\r\n", b"\n")
                info = tarfile.TarInfo(rel)
                info.size = len(data)
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o755 if rel.endswith((".sh", ".lua", "be3600-screensaver", "be3600-anim", "be3600-player")) else 0o644
                t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def gz(data):
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=9, mtime=0) as g:
        g.write(data)
    return out.getvalue()


def wrap(b64, width=76):
    return "\n".join(b64[i:i + width] for i in range(0, len(b64), width))


def existing_payload(path):
    """-> (version id, base64) from a committed download, or (None, None)."""
    if not os.path.exists(path):
        return None, None
    raw = open(path, "rb").read()
    if path.endswith(".cmd"):
        i = raw.rfind(CMD_MARK.replace(b"\n", b"\r\n"))
        if i < 0:
            return None, None
        head, _, rest = raw[i + len(CMD_MARK) + 1:].partition(b"\n")
        return head.strip().decode("ascii", "replace"), b"".join(rest.split()).decode("ascii", "replace")
    m = re.search(r'PAYLOAD = \("([0-9a-f]+)", """(.*?)"""\)', raw.decode("utf-8"), re.S)
    return (m.group(1), "".join(m.group(2).split())) if m else (None, None)


def payload_b64(want_tar, pid, path):
    """The compressed payload to embed: the committed one if it still unpacks to the same
    tar (so rebuilding never churns the file), otherwise a fresh one."""
    have_id, have_b64 = existing_payload(path)
    if have_id == pid and have_b64:
        try:
            if gzip.decompress(base64.b64decode(have_b64)) == want_tar:
                return have_b64
        except (OSError, ValueError, EOFError):
            pass
    return base64.b64encode(gz(want_tar)).decode("ascii")


# ---------------------------------------------------------------------------------------
# The two downloads
# ---------------------------------------------------------------------------------------

def build_cmd(pid, b64):
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
        "rem Studio Link for Windows: ONE file. Double-click it; the first time it puts the screen saver\n"
        "rem on your router, then Motion Studio opens and connects by itself.\n"
        "title GL.iNet Router Screen Saver (BE3600) - Studio Link\n"
        'set "SELF=%~f0"\n'
        'set "PSF=%TEMP%\\be3600-studio-link.ps1"\n'
        "rem Unpack the PowerShell code below the first marker into a temp file, run it, delete it.\n"
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:SELF); '
        "$i=$t.IndexOf('#'+'#PS-CODE#'+'#'); $j=$t.LastIndexOf('#'+'#PAYLOAD#'+'# '); if($j -lt 0){$j=$t.Length}; "
        '[IO.File]::WriteAllText($env:PSF,$t.Substring($i,$j-$i))"\n'
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PSF%" %*\n'
        'del "%PSF%" >nul 2>&1\n'
        "echo.\n"
        "pause\n"
        "goto :eof\n"
    )
    text = stub + ps + "\n##PAYLOAD## " + pid + "\n" + wrap(b64) + "\n"
    return text.replace("\n", "\r\n").encode("ascii")


def build_py(pid, b64):
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
    slot = "PAYLOAD = None  # __PAYLOAD__\n"
    if slot not in py:
        sys.exit("tools/studio_link.py changed shape: expected its PAYLOAD line")
    packed = 'PAYLOAD = ("%s", """\n%s\n""")\n' % (pid, wrap(b64))
    return py.replace(old, new).replace(slot, packed).encode("utf-8")


FILES = {"Studio-Link.cmd": build_cmd, "studio-link.py": build_py}


def main():
    check = "--check" in sys.argv
    tar = payload_tar()
    pid = hashlib.sha256(tar).hexdigest()[:12]
    stale = []
    for name, build in FILES.items():
        path = os.path.join(OUT, name)
        want = build(pid, payload_b64(tar, pid, path))
        have = open(path, "rb").read() if os.path.exists(path) else None
        if check:
            if have != want:
                stale.append(name)
        elif have != want:
            os.makedirs(OUT, exist_ok=True)
            with open(path, "wb") as f:
                f.write(want)
            print("wrote studio/downloads/%s (%d bytes)" % (name, len(want)))
        else:
            print("studio/downloads/%s is already up to date" % name)
    if check:
        if stale:
            sys.exit("out of date: %s. Run: python3 tools/build_downloads.py" % ", ".join(stale))
        print("studio/downloads is up to date")


if __name__ == "__main__":
    main()
