#!/usr/bin/env python3
"""A stand-in for ssh, and for the routers behind it, for the Install / Uninstall tests.

Studio Link runs it instead of ssh (BE3600_SSH, test mode only). It logs in the way the real
thing does: the admin password arrives in BE3600_STUDIO_PW (what the askpass helper would type),
a saved login arrives as -i KEY and is checked against the router's authorized_keys, with the
key's passphrase checked by ssh-keygen. Then it answers the few commands the flow sends.

The routers live in FAKE_ROUTER_DIR, one folder per address:
    model            "be3600" or anything else (another brand of router)
    installed        present = the screen saver is on it
    authorized_keys  the logins computers saved
    install_rc       exit code for the install (default 0), with install_out as its output
And FAKE_ROUTER_DIR/password is the admin password of all of them. Every command is appended
to FAKE_ROUTER_DIR/log.
"""
import os
import shutil
import subprocess
import sys

HOME = os.environ["FAKE_ROUTER_DIR"]


def read(path, default=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


def deny(ip):
    sys.stderr.write("root@%s: Permission denied (publickey,password).\n" % ip)
    sys.exit(255)


def key_ok(router, key, secret):
    pub = read(key + ".pub").split()
    if len(pub) < 2 or pub[1] not in read(os.path.join(router, "authorized_keys")):
        return False
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        return True
    return subprocess.run([keygen, "-y", "-P", secret or "", "-f", key], capture_output=True).returncode == 0


def remove_saved_logins(router):
    path = os.path.join(router, "authorized_keys")
    lines = [l for l in read(path).splitlines(True) if not l.rstrip().split(" ")[-1].startswith("be3600-studio-link-")]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main():
    args = sys.argv[1:]
    key, target, rest = None, None, []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-i", "-o"):
            if a == "-i":
                key = args[i + 1]
            i += 2
            continue
        if target is None and a.startswith("root@"):
            target = a[5:]
        else:
            rest.append(a)
        i += 1
    remote = " ".join(rest)
    ip = target or "?"
    router = os.path.join(HOME, ip)
    with open(os.path.join(HOME, "log"), "a", encoding="utf-8") as f:
        f.write("%s %s %s\n" % (ip, "key" if key else "password", remote))
    if not os.path.isdir(router):
        sys.stderr.write("ssh: connect to host %s port 22: Connection timed out\n" % ip)
        sys.exit(255)

    secret = os.environ.get("BE3600_STUDIO_PW")
    if key:
        if not key_ok(router, key, secret):
            deny(ip)
    elif secret is None or secret != read(os.path.join(HOME, "password")).strip():
        deny(ip)

    data = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
    installed = os.path.join(router, "installed")
    if remote == "echo studio-ok":
        print("studio-ok")
    elif "virtual_size" in remote:
        if read(os.path.join(router, "model")).strip() == "be3600":
            print("be3600-yes")
        if os.path.exists(installed):
            print("be3600-installed")
    elif "setup/router-install.sh" in remote:
        if not data.startswith(b"\x1f\x8b"):
            print("  ERROR: the bundle did not arrive")
            sys.exit(1)
        out = read(os.path.join(router, "install_out"))
        sys.stdout.write(out)
        rc = int(read(os.path.join(router, "install_rc"), "0").strip() or 0)
        if rc == 0:
            open(installed, "w").close()
        sys.exit(rc)
    elif "cat >> /etc/dropbear/authorized_keys" in remote:
        with open(os.path.join(router, "authorized_keys"), "ab") as f:
            f.write(data)
    elif remote.startswith("/usr/sbin/be3600-uninstall"):
        if not os.path.exists(installed):
            sys.stderr.write("sh: /usr/sbin/be3600-uninstall: not found\n")
            sys.exit(127)
        os.unlink(installed)
        if "--forget-keys" in remote:
            remove_saved_logins(router)
        print("Done. The stock GL.iNet screen is back.")
    elif "be3600-studio-link-" in remote and "sed -i" in remote:
        remove_saved_logins(router)
    else:
        sys.stderr.write("fake router: unexpected command: %s\n" % remote)
        sys.exit(2)


if __name__ == "__main__":
    main()
