#!/usr/bin/env python3
"""The Install and Uninstall conversations, word for word.

Every folder in tests/setup_flow/ is one situation: which routers answer, what the person
types, and the exact text they must see (expected.N for run N). tools/studio_link.py (macOS
and Linux) must produce it here; tests/windows_setup_flow_test.ps1 holds tools/studio-link.ps1
(Windows) to the very same files, so all three systems say the same things.

    python3 tests/setup_flow_test.py                   run them all against tools/studio_link.py
    python3 tests/setup_flow_test.py --downloads DIR   against the built downloads in DIR instead
                                                       (install-screen-saver.py, uninstall-screen-saver.py)
    python3 tests/setup_flow_test.py --update          rewrite expected.N from what it says now

A scenario file holds "key: value" lines:
    runs:        install uninstall ...      one run each, in order, keeping this computer's state
    routers:     ADDRESS=be3600 ...         the routers that exist (anything but be3600 = another make)
    installed:   ADDRESS ...                routers that already have the screen saver
    reachable:   ADDRESS ...                which addresses answer on SSH
    gateway:     ADDRESS                    this computer's gateway
    password:    WORD                       the routers' admin password
    install_rc:  ADDRESS=CODE               the router installer's exit code, and
    install_out: TEXT                       what it prints (\\n for a new line)
    keychain:    no                         this computer has no keychain (never on Windows or a Mac)
    only:        python                     not on Windows (it always has a keychain: DPAPI)
    exit.N:      CODE                       run N's exit code (default 0)
    reset.N:     ADDRESS                    after run N the router is reset: its saved logins are gone
    check:       NAME [ARG]                 after the last run (see CHECKS)
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOWS = os.path.join(ROOT, "tests", "setup_flow")


def parse(path):
    conf = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            k, _, v = line.partition(":")
            conf.setdefault(k.strip(), []).append(v.strip())
    return conf


def one(conf, key, default=""):
    return conf.get(key, [default])[0]


def normalize(text):
    return "\n".join(l.rstrip() for l in text.replace("\r\n", "\n").split("\n")).strip("\n") + "\n"


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def build_world(conf, work):
    routers = os.path.join(work, "routers")
    os.makedirs(routers)
    with open(os.path.join(routers, "password"), "w") as f:
        f.write(one(conf, "password", "hunter2"))
    for spec in one(conf, "routers").split():
        ip, _, model = spec.partition("=")
        os.makedirs(os.path.join(routers, ip))
        with open(os.path.join(routers, ip, "model"), "w") as f:
            f.write(model)
    for ip in one(conf, "installed").split():
        open(os.path.join(routers, ip, "installed"), "w").close()
    for spec in one(conf, "install_rc").split():
        ip, _, rc = spec.partition("=")
        with open(os.path.join(routers, ip, "install_rc"), "w") as f:
            f.write(rc)
        with open(os.path.join(routers, ip, "install_out"), "w") as f:
            f.write(one(conf, "install_out").replace("\\n", "\n") + "\n")
    fake = os.path.join(work, "fake-ssh")
    with open(fake, "w") as f:
        f.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, os.path.join(ROOT, "tests", "fake_router.py")))
    os.chmod(fake, 0o755)
    return routers, fake


def run_program(program, action, conf, work, routers, fake, answers):
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("BE3600_", "STUDIO_LINK_", "SSH_ASKPASS")):
            del env[k]
    env.update({
        "BE3600_TESTING": "1",
        "BE3600_SSH": fake,
        "FAKE_ROUTER_DIR": routers,
        "BE3600_REACHABLE": ",".join(one(conf, "reachable").split()) or "none",
        "BE3600_GATEWAY": one(conf, "gateway") or "none",
        "BE3600_ANSWERS": answers,
        "XDG_CONFIG_HOME": os.path.join(work, "config"),
        "HOME": os.path.join(work, "home"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    # Always a test keychain (or none at all): never the real macOS Keychain or libsecret.
    env["BE3600_VAULT_DIR"] = "none"
    if one(conf, "keychain") != "no":
        env["BE3600_VAULT_DIR"] = os.path.join(work, "vault")
        os.makedirs(env["BE3600_VAULT_DIR"], exist_ok=True)
    if os.path.isdir(program):                       # the built downloads: one file per action
        args = [sys.executable, os.path.join(program, "%s-screen-saver.py" % action)]
    else:
        args = [sys.executable, program, "--" + action]
    r = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


WINDOWS = "--windows" in sys.argv          # checking a Windows run (tests/windows_setup_flow_test.ps1)


def config_dir(work):
    if WINDOWS:
        return os.path.join(work, "appdata", "be3600-screensaver")
    return os.path.join(work, "config", "be3600-screensaver")


def keychain_used(work):
    """Is this computer's saved-login passphrase in its keychain (the test one, or DPAPI's file)?"""
    if WINDOWS:
        return os.path.exists(os.path.join(config_dir(work), "studio-key.pass"))
    return bool(os.listdir(os.path.join(work, "vault")))


def state_file(work):
    return os.path.join(config_dir(work), "router.txt" if WINDOWS else "router")


def saved_logins(routers, ip):
    return [l for l in (read(os.path.join(routers, ip, "authorized_keys")) or "").splitlines() if "be3600-studio-link-" in l]


CHECKS = {
    # name: (description, function(work, routers, arg) -> bool)
    "installed": ("the screen saver is on %s", lambda w, r, a: os.path.exists(os.path.join(r, a, "installed"))),
    "not-installed": ("the screen saver is not on %s", lambda w, r, a: not os.path.exists(os.path.join(r, a, "installed"))),
    "saved-login": ("this computer's login is saved, on the router and in the keychain",
                    lambda w, r, a: len(saved_logins(r, a)) == 1 and os.path.exists(os.path.join(config_dir(w), "studio-key"))
                    and keychain_used(w)),
    "no-saved-login": ("no login is saved on %s", lambda w, r, a: not saved_logins(r, a)),
    "remembered-address": ("the address %s is remembered for next time",
                           lambda w, r, a: (read(state_file(w)) or "").strip() == a),
    "computer-clean": ("nothing is left on this computer: address, key files, keychain entry",
                       lambda w, r, a: not os.path.exists(config_dir(w)) and not keychain_used(w)),
    "no-password-sent-to": ("the password never went to %s",
                            lambda w, r, a: not any(l.startswith(a + " password") for l in (read(os.path.join(r, "log")) or "").splitlines())),
}


def run_scenario(name, program, update):
    folder = os.path.join(FLOWS, name)
    conf = parse(os.path.join(folder, "scenario"))
    work = tempfile.mkdtemp(prefix="be3600-flow-")
    fails, lines = 0, []
    try:
        routers, fake = build_world(conf, work)
        for n, action in enumerate(one(conf, "runs").split(), 1):
            answers = os.path.join(folder, "answers.%d" % n)
            if not os.path.exists(answers):
                answers = os.path.join(work, "no-answers")
                open(answers, "w").close()
            code, out = run_program(program, action, conf, work, routers, fake, answers)
            got = normalize(out)
            exp_path = os.path.join(folder, "expected.%d" % n)
            want = read(exp_path)
            want_code = int(one(conf, "exit.%d" % n, "0"))
            if update:
                with open(exp_path, "w", encoding="utf-8") as f:
                    f.write(got)
                want = got
            ok = want is not None and got == normalize(want) and code == want_code
            lines.append(("  ok    " if ok else "  FAIL  ") + "%s, run %d (%s): the exact text%s" % (
                name, n, action, "" if code == want_code else " (exit %d, not %d)" % (code, want_code)))
            if not ok:
                fails += 1
                if want is not None and got != normalize(want):
                    import difflib
                    lines += ["        " + d for d in difflib.unified_diff(
                        normalize(want).splitlines(), got.splitlines(), "expected", "got", lineterm="")]
            for ip in one(conf, "reset.%d" % n).split():
                try:
                    os.unlink(os.path.join(routers, ip, "authorized_keys"))
                except OSError:
                    pass
        f, l = run_checks(name, work, routers)
        fails += f
        lines += l
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return fails, lines


def run_checks(name, work, routers):
    conf = parse(os.path.join(FLOWS, name, "scenario"))
    fails, lines = 0, []
    for spec in conf.get("check", []):
        cname, _, arg = spec.partition(" ")
        desc, fn = CHECKS[cname]
        ok = fn(work, routers, arg.strip())
        lines.append(("  ok    " if ok else "  FAIL  ") + "%s: %s" % (name, desc % arg if "%s" in desc else desc))
        fails += 0 if ok else 1
    return fails, lines


def main():
    # For tests/windows_setup_flow_test.ps1: the same routers, and the same checks afterwards.
    #   --world NAME WORK         make scenario NAME's routers in WORK/routers
    #   --check NAME WORK         print its checks (with --windows: Windows' own folders)
    if "--world" in sys.argv:
        i = sys.argv.index("--world")
        name, work = sys.argv[i + 1], sys.argv[i + 2]
        build_world(parse(os.path.join(FLOWS, name, "scenario")), work)
        return
    if "--check" in sys.argv:
        i = sys.argv.index("--check")
        name, work = sys.argv[i + 1], sys.argv[i + 2]
        fails, lines = run_checks(name, work, os.path.join(work, "routers"))
        print("\n".join(lines))
        sys.exit(1 if fails else 0)
    update = "--update" in sys.argv
    program = os.path.join(ROOT, "tools", "studio_link.py")
    if "--downloads" in sys.argv:
        program = os.path.abspath(sys.argv[sys.argv.index("--downloads") + 1])
    names = sorted(d for d in os.listdir(FLOWS) if os.path.isdir(os.path.join(FLOWS, d)))
    only = [a for a in sys.argv[1:] if a in names]
    total = 0
    for name in only or names:
        conf = parse(os.path.join(FLOWS, name, "scenario"))
        if one(conf, "only") == "windows":
            continue
        fails, lines = run_scenario(name, program, update)
        total += fails
        print("\n".join(lines), flush=True)
    print("\n%s" % ("All Install / Uninstall conversations match." if not total else "%d failed." % total))
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
