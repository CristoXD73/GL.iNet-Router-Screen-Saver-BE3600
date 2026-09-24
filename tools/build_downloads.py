#!/usr/bin/env python3
"""Builds the single-file downloads that Motion Studio offers, into studio/downloads/.

    python3 tools/build_downloads.py           write them
    python3 tools/build_downloads.py --check   exit 1 if the committed copies are out of date

  Studio-Link.cmd   Windows: ONE file to double-click (batch stub + the PowerShell code
                    from tools/studio-link.ps1 and tools/lib.ps1, unpacked to a temp file
                    when it runs)
  studio-link.py    macOS/Linux: ONE file to run with python3 (tools/studio_link.py with
                    tools/bea2.py folded in, and Motion Studio's own pages, which it serves on
                    a Mac because Safari cannot reach it from the website)

The one-click Install and Uninstall (what studio/get.html offers first) are the same two
programs with what they do set inside them:

  Install-Screen-Saver.cmd / Uninstall-Screen-Saver.cmd       Windows (studio-link.ps1 -Install / -Uninstall)
  install-screen-saver.py  / uninstall-screen-saver.py        Linux: python3 FILE (studio_link.py --install / ...)
  Install-Screen-Saver-Mac.zip / Uninstall-Screen-Saver-Mac.zip
                            macOS: the same file as the .py, named "Install Screen Saver.command" and
                            executable, in a zip (a browser download loses the executable bit; a zip
                            keeps it), so it opens in Terminal with a double-click

The Install and Studio Link files also carry the router's files (router/, setup/, animations/ as a .tar.gz) so that
Studio Link can put the screen saver on a router that does not have it yet, with nothing
else to download. The payload is identified by a short hash of the files it holds; --check compares the
unpacked files, so different Python or zlib versions cannot fail it.

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
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "studio", "downloads")
PAYLOAD_DIRS = ("router", "setup", "animations")
CMD_MARK = b"\n##PAYLOAD## "          # the payload follows this marker in Studio-Link.cmd


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


# ---------------------------------------------------------------------------------------
# The payload: what the router needs, read the same way every time
# ---------------------------------------------------------------------------------------

def junk(name):
    """Files a computer leaves behind that are not part of the project: macOS Finder's .DS_Store
    and ._ resource forks, Windows' Thumbs.db, editor swap files. Never packed, so building on a
    Mac gives exactly the same downloads as anywhere else."""
    return name in (".DS_Store", "Thumbs.db", "desktop.ini") or name.startswith("._") or name.endswith((".swp", "~"))


def payload_files():
    """-> [(path in the tar, mode, bytes)] for router/, setup/ and animations/, in a fixed order."""
    out = []
    for top in PAYLOAD_DIRS:
        base = os.path.join(ROOT, top)
        names = []
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not junk(d))
            names += [os.path.join(dirpath, f) for f in sorted(files) if not junk(f)]
        for path in names:
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            with open(path, "rb") as f:
                data = f.read()
            if b"\0" not in data:                          # text: the router wants LF line endings
                data = data.replace(b"\r\n", b"\n")
            mode = 0o755 if rel.endswith((".sh", ".lua", "be3600-screensaver", "be3600-anim", "be3600-player")) else 0o644
            out.append((rel, mode, data))
    return out


def studio_files():
    """-> [(path in the tar, mode, bytes)]: the Motion Studio pages Studio Link serves on a Mac
    (the same list tools/studio_link.py serves from a clone)."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import studio_link
    base = os.path.join(ROOT, "studio")
    out = []
    for rel in studio_link.studio_names(base):
        with open(os.path.join(base, rel), "rb") as f:
            out.append((rel, 0o644, f.read().replace(b"\r\n", b"\n")))
    return out


def tar_of(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as t:
        for rel, mode, data in files:
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = mode
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def files_in(tar_bytes):
    """What a packed tar contains, read back (so comparing does not depend on tar's own byte layout,
    which differs between Python versions)."""
    out = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as t:
        for m in t.getmembers():
            out.append((m.name, m.mode, t.extractfile(m).read()))
    return out


def files_id(files):
    h = hashlib.sha256()
    for rel, mode, data in files:
        h.update(("%s\0%o\0%d\0" % (rel, mode, len(data))).encode())
        h.update(data)
    return h.hexdigest()[:12]


def gz(data):
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=9, mtime=0) as g:
        g.write(data)
    return out.getvalue()


def wrap(b64, width=76):
    return "\n".join(b64[i:i + width] for i in range(0, len(b64), width))


def existing_payload(path, slot="PAYLOAD"):
    """-> (version id, base64) from a committed download, or (None, None)."""
    if not os.path.exists(path):
        return None, None
    raw = open(path, "rb").read()
    if slot != "PAYLOAD":
        m = re.search(r'%s = \("([0-9a-f]+)", """(.*?)"""\)' % slot, raw.decode("utf-8"), re.S)
        return (m.group(1), "".join(m.group(2).split())) if m else (None, None)
    if path.endswith(".cmd"):
        i = raw.rfind(CMD_MARK.replace(b"\n", b"\r\n"))
        if i < 0:
            return None, None
        head, _, rest = raw[i + len(CMD_MARK) + 1:].partition(b"\n")
        return head.strip().decode("ascii", "replace"), b"".join(rest.split()).decode("ascii", "replace")
    m = re.search(r'PAYLOAD = \("([0-9a-f]+)", """(.*?)"""\)', raw.decode("utf-8"), re.S)
    return (m.group(1), "".join(m.group(2).split())) if m else (None, None)


def payload_b64(files, pid, path, slot="PAYLOAD"):
    """The compressed payload to embed: the committed one if it still holds exactly these
    files (so rebuilding never churns the file), otherwise a fresh one."""
    have_id, have_b64 = existing_payload(path, slot)
    if have_id == pid and have_b64:
        try:
            if files_in(gzip.decompress(base64.b64decode(have_b64))) == files:
                return have_b64
        except (OSError, ValueError, EOFError, tarfile.TarError):
            pass
    return base64.b64encode(gz(tar_of(files))).decode("ascii")


# ---------------------------------------------------------------------------------------
# The two downloads
# ---------------------------------------------------------------------------------------

def build_cmd(pid, b64, action=None):
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

    if action:
        return (cmd_stub(action) + ps + ("\n##PAYLOAD## " + pid + "\n" + wrap(b64) + "\n" if b64 else "")
                ).replace("\n", "\r\n").encode("ascii")
    stub = (
        "@echo off\n"
        "rem Studio Link for Windows: ONE file. Double-click it; the first time it puts the screen saver\n"
        "rem on your router, then Motion Studio opens and connects by itself.\n"
        "title GL.iNet Router Screen Saver (BE3600) - Studio Link\n"
        'set "SELF=%~f0"\n'
        'set "PSF=%TEMP%\\be3600-studio-link-%RANDOM%%RANDOM%%RANDOM%.ps1"\n'
        "rem Unpack the PowerShell code below the first marker into a temp file with a random name\n"
        "rem (the program deletes it as soon as it is running), then run it.\n"
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


def cmd_stub(action):
    """The batch part of Install-Screen-Saver.cmd / Uninstall-Screen-Saver.cmd. The PowerShell
    flow itself says "Press Enter to close this window", so there is no pause here."""
    what = {"install": "Puts the animated screen saver on your GL.iNet GL-BE3600 (Slate 7).",
            "uninstall": "Takes the screen saver off your GL.iNet GL-BE3600 (Slate 7), and puts its normal screen back."}[action]
    title = action.capitalize()
    return (
        "@echo off\n"
        "rem Double-click me. " + what + "\n"
        "rem It asks for your router's admin password in this window only, and tells you each step.\n"
        "title GL.iNet Router Screen Saver - " + title + "\n"
        'set "SELF=%~f0"\n'
        'set "PSF=%TEMP%\\be3600-setup-%RANDOM%%RANDOM%%RANDOM%.ps1"\n'
        "rem Unpack the PowerShell code below the first marker into a temp file with a random name\n"
        "rem (the program deletes it as soon as it is running), then run it.\n"
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:SELF); '
        "$i=$t.IndexOf('#'+'#PS-CODE#'+'#'); $j=$t.LastIndexOf('#'+'#PAYLOAD#'+'# '); if($j -lt 0){$j=$t.Length}; "
        '[IO.File]::WriteAllText($env:PSF,$t.Substring($i,$j-$i))"\n'
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PSF%" -' + title + " %*\n"
        'set "RC=%ERRORLEVEL%"\n'
        'del "%PSF%" >nul 2>&1\n'
        "exit /b %RC%\n"
    )


# The first lines of install-screen-saver.py / uninstall-screen-saver.py: a shell script and a
# Python program at once. Run as a shell script (a double-clicked .command on a Mac, or sh FILE),
# these lines hand the file to python3; to Python they are only strings. Each starts with four
# quotes: two empty strings to the shell, the start of a string to Python (which the #''' at
# the end of the line closes). A Mac without Apple's Command Line Tools has a python3 that only
# offers to install them, so that is said in plain words first.
POLYGLOT = r"""#!/bin/sh
# GL.iNet Router Screen Saver - {title}. Mac: double-click it. Linux: python3 {name} (or: sh {name})
''''[ "$(uname -s)" = Darwin ] && [ "$(command -v python3)" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1 && { echo; echo '  This needs Python 3, which comes with the free Command Line Tools from Apple.'; echo '  Your Mac will offer to install them now: click Install, wait until it has finished, then double-click this file again.'; xcode-select --install >/dev/null 2>&1; echo; printf '  Press Enter to close this window. '; read -r _; exit 1; } #'''
''''command -v python3 >/dev/null 2>&1 || { echo; echo '  This needs Python 3. Install it (for example: sudo apt install python3), then run this again.'; exit 1; } #'''
''''exec python3 "$0" "$@" #'''
"""


def build_py(pid, b64, action=None):
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
    packed = 'PAYLOAD = ("%s", """\n%s\n""")\n' % (pid, wrap(b64)) if b64 else slot
    studio_slot = "STUDIO = None  # __STUDIO__\n"
    if studio_slot not in py:
        sys.exit("tools/studio_link.py changed shape: expected its STUDIO line")
    if action:
        # The one-click Install / Uninstall: no Motion Studio inside (they do not serve it), and
        # Uninstall needs none of the router's files either.
        top = "#!/usr/bin/env python3\n"
        if not py.startswith(top):
            sys.exit("tools/studio_link.py changed shape: expected it to start with " + top.strip())
        act_slot = "ACTION = None  # __ACTION__\n"
        if act_slot not in py:
            sys.exit("tools/studio_link.py changed shape: expected its ACTION line")
        name = "%s-screen-saver.py" % action
        py = POLYGLOT.replace("{title}", action.capitalize()).replace("{name}", name) + py[len(top):]
        py = py.replace(old, new).replace(act_slot, 'ACTION = "%s"\n' % action)
        return py.replace(slot, packed).encode("utf-8")
    sfiles = studio_files()
    sid = files_id(sfiles)
    sb64 = payload_b64(sfiles, sid, os.path.join(OUT, "studio-link.py"), "STUDIO")
    studio = 'STUDIO = ("%s", """\n%s\n""")\n' % (sid, wrap(sb64))
    return py.replace(old, new).replace(slot, packed).replace(studio_slot, studio).encode("utf-8")


def mac_zip(inner_name, data, path):
    """A zip holding one executable file. Stored (not compressed) with a fixed date and Unix
    permissions, so it comes out the same everywhere; the committed zip is kept when it already
    holds exactly this file (so a different Python cannot make it look out of date)."""
    if os.path.exists(path):
        try:
            with zipfile.ZipFile(path) as z:
                infos = z.infolist()
                if (len(infos) == 1 and infos[0].filename == inner_name and (infos[0].external_attr >> 16) == 0o100755
                        and z.read(infos[0]) == data):
                    with open(path, "rb") as f:
                        return f.read()
        except (OSError, zipfile.BadZipFile):
            pass
    buf = io.BytesIO()
    info = zipfile.ZipInfo(inner_name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3                         # Unix, so macOS's Archive Utility honours the mode
    info.external_attr = 0o100755 << 16            # a regular file, rwxr-xr-x
    info.compress_type = zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(info, data)
    return buf.getvalue()


FILES = {"Studio-Link.cmd": build_cmd, "studio-link.py": build_py}
# The one-click Install / Uninstall. name: (builder, what it does, carries the router's files)
SETUP_FILES = {
    "Install-Screen-Saver.cmd": (build_cmd, "install", True),
    "Uninstall-Screen-Saver.cmd": (build_cmd, "uninstall", False),
    "install-screen-saver.py": (build_py, "install", True),
    "uninstall-screen-saver.py": (build_py, "uninstall", False),
}
# The Mac zips hold the .py above, named as a .command so a double-click opens it in Terminal.
MAC_ZIPS = {
    "Install-Screen-Saver-Mac.zip": ("install-screen-saver.py", "Install Screen Saver.command"),
    "Uninstall-Screen-Saver-Mac.zip": ("uninstall-screen-saver.py", "Uninstall Screen Saver.command"),
}


def main():
    check = "--check" in sys.argv
    files = payload_files()
    pid = files_id(files)
    stale = []
    built = {}
    for name, build in FILES.items():
        built[name] = build(pid, payload_b64(files, pid, os.path.join(OUT, name)))
    for name, (build, action, carries) in SETUP_FILES.items():
        b64 = payload_b64(files, pid, os.path.join(OUT, name)) if carries else None
        built[name] = build(pid, b64, action)
    for name, (source, inner) in MAC_ZIPS.items():
        built[name] = mac_zip(inner, built[source], os.path.join(OUT, name))
    # SHA256SUMS lets anyone check a download:  sha256sum -c SHA256SUMS   (PowerShell: Get-FileHash)
    built["SHA256SUMS"] = "".join("%s  %s\n" % (hashlib.sha256(built[n]).hexdigest(), n)
                                  for n in sorted(built)).encode("ascii")
    for name, want in built.items():
        path = os.path.join(OUT, name)
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
