#!/usr/bin/env python3
"""Tests be3600-fan (the cooling fan used as a chime) against a fake hwmon tree.

    python3 tests/fan_test.py            (needs a POSIX sh; runs anywhere the shell tests run)

Nothing here spins a real fan: BE3600_HWMON points at ordinary files, so the test can read back
exactly which duty cycles would have been written, and in which order.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAN = os.path.join(HERE, "..", "router", "usr", "sbin", "be3600-fan")
ACTION = os.path.join(HERE, "..", "router", "usr", "sbin", "be3600-widget-action")
passed = failed = 0


def check(ok, name, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + name)
    else:
        failed += 1
        print("  FAIL  " + name + (("  (" + extra + ")") if extra else ""))


def make_env(temp_mc=45000, conf="FAN_CHIME=1\n"):
    root = tempfile.mkdtemp(prefix="be3600-fantest-")
    hw = root + "/hwmon"
    os.makedirs(hw)
    for name, val in (("pwm1", "0"), ("fan1_input", "0")):
        with open(hw + "/" + name, "w") as f:
            f.write(val + "\n")
    with open(root + "/temp", "w") as f:
        f.write("%d\n" % temp_mc)
    with open(root + "/config", "w") as f:
        f.write(conf)
    env = dict(os.environ,
               BE3600_HWMON=hw,
               BE3600_TEMPFILE=root + "/temp",
               BE3600_CONF=root + "/config",
               BE3600_FANLOCK=root + "/lock",
               BE3600_FANHOLD=root + "/hold",
               BE3600_FAN=os.path.abspath(FAN),
               BE3600_FAN_NOSLEEP="1")
    return root, hw, env


def run(env, *args):
    p = subprocess.run(["sh", FAN] + list(args), env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def duty(hw):
    with open(hw + "/pwm1") as f:
        return f.read().strip()


def trace(env, hw, *args):
    """Play something with the write to pwm1 recorded, so the whole pattern can be inspected."""
    log = os.path.dirname(hw) + "/trace"
    if os.path.exists(log):
        os.remove(log)
    e = dict(env, BE3600_TRACE=log)
    code, out = run(e, *args)
    steps = []
    if os.path.exists(log):
        with open(log) as f:
            steps = [ln.strip() for ln in f if ln.strip()]
    return code, out, steps


def main():
    print("== it knows the chimes")
    root, hw, env = make_env()
    code, out = run(env, "chimes")
    check(code == 0 and "siren" in out and "alert" in out, "chimes are listed by name", out)

    print("== a chime is a series of speeds, and the fan is left as it was found")
    code, out, steps = trace(env, hw, "chime", "up", "--force")
    check(code == 0, "a chime plays", out)
    check(steps[0] == "60" and steps[1] == "255", "a chime starts from an idle floor, then stabs", str(steps))
    check(steps[-1] == "0", "the old speed is put back at the end", str(steps))
    check(duty(hw) == "0", "and the file really holds it", duty(hw))

    code, out, steps = trace(env, hw, "chime", "alert", "--force")
    check(steps.count("255") == 3, "'alert' stabs three times", str(steps))

    code, out, steps = trace(env, hw, "chime", "down", "--force")
    check(steps[0] == "255" and steps[1:4] == ["150", "100", "60"], "'down' dies away in stages", str(steps))

    code, out, steps = trace(env, hw, "chime", "boot", "--force")
    check(steps[:3] == ["60", "100", "140"] and "255" in steps, "'boot' spools up to full", str(steps))

    code, out, steps = trace(env, hw, "chime", "rev", "--force")
    check(steps == ["60", "255", "60", "255", "60", "255", "0"], "'rev' is idle, two blips and a hold", str(steps))

    print("== a chime never comes before cooling")
    root2, hw2, hot = make_env(temp_mc=78000)
    code, out = run(hot, "chime", "up", "--force")
    check(code == 2 and "78C" in out, "it refuses while the router is hot", out)
    check(duty(hw2) == "0", "and does not touch the fan", duty(hw2))

    print("== quiet hours")
    root3, hw3, quiet = make_env(conf="FAN_CHIME=1\nFAN_CHIME_QUIET=00:00-23:59\n")
    code, out, steps = trace(quiet, hw3, "chime", "up")
    check(code == 0 and not steps, "inside quiet hours nothing plays")
    code, out, steps = trace(quiet, hw3, "chime", "up", "--force")
    check(steps, "but --force still plays it (that is what the preview uses)")

    print("== your own patterns")
    code, out, steps = trace(env, hw, "play", "100:50 200:50")
    check(steps[:2] == ["100", "200"], "play takes duty:milliseconds", str(steps))
    for bad, why in (("300:50", "a duty above 255"),
                     ("100", "a step with no milliseconds"),
                     ("abc:50", "a step that is not a number"),
                     ("200:13000", "longer than the fan may be held")):
        code, out = run(env, "play", bad)
        check(code == 1, "it refuses " + why, out)
    check(duty(hw) == "0", "a refused pattern leaves the fan alone", duty(hw))

    print("== holding a speed by hand")
    code, out = run(env, "spin", "100")
    check(duty(hw) == "255", "spin 100 goes to full", duty(hw))
    code, out = run(env, "spin", "0")
    check(duty(hw) == "0" and os.path.exists(env["BE3600_FANHOLD"]), "spin 0 holds it off", duty(hw))
    code, out = run(env, "spin", "auto")
    check(duty(hw) == "0" and not os.path.exists(env["BE3600_FANHOLD"]), "spin auto hands it back", duty(hw))
    code, out = run(env, "spin", "150")
    check(code == 1, "it refuses more than 100 per cent", out)

    print("== what the screen's banners do")
    code, out = run(env, "status")
    check("chimes: 1" in out, "status reports the setting", out)
    p = subprocess.run(["sh", ACTION, "chime", "bad"], env=env, capture_output=True, text=True)
    check(p.returncode == 0 and duty(hw) == "0", "a banner plays one and leaves the fan alone", p.stdout + p.stderr)
    off = dict(env, BE3600_CONF=root + "/off")
    with open(root + "/off", "w") as f:
        f.write("FAN_CHIME=0\n")
    log = root + "/trace2"
    p = subprocess.run(["sh", ACTION, "chime", "bad"], env=dict(off, BE3600_TRACE=log), capture_output=True, text=True)
    check(p.returncode == 0 and not os.path.exists(log), "with FAN_CHIME=0 a banner stays silent")

    print("== chimes you designed yourself")
    cdir = root + "/chimes.d"
    os.makedirs(cdir)
    with open(cdir + "/mine", "w") as f:
        f.write("# be3600 fan chime: mine\n# a comment\n255:400 0:450\n255:700\n")
    mine = dict(env, BE3600_CHIMES=cdir)
    code, out, steps = trace(mine, hw, "chime", "mine", "--force")
    check(steps[:3] == ["255", "0", "255"], "a saved .chime file plays by its name", str(steps))
    code, out = run(mine, "chimes")
    check("mine" in out, "and is listed with the built-in ones", out)
    code, out = run(mine, "chime", "../../etc/passwd", "--force")
    check(code == 1, "a name that tries to escape the folder is refused", out)
    code, out = run(mine, "chime", "nosuch", "--force")
    check(code == 1, "an unknown name is refused", out)

    print("== a router with no fan")
    root4 = tempfile.mkdtemp(prefix="be3600-fantest-")
    nofan = dict(env, BE3600_HWMON=root4)
    code, out = run(nofan, "status")
    check(code == 1 and "no controllable fan" in out, "it says so plainly", out)

    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
