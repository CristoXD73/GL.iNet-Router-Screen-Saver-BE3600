#!/usr/bin/env python3
"""
sound_to_fan.py -- turn a short sound into something the router's cooling fan can play.

    python3 tools/sound_to_fan.py jingle.wav
    python3 tools/sound_to_fan.py jingle.wav --min-ms 320 --name 1up

A fan has no pitch control. What you hear is its blade-passing tone, which rises and falls
with how fast it is spinning, so the only note it can play is "how fast am I going". Two
things follow, and this script does both:

  * Pitch becomes speed. The tone is proportional to RPM, so a musical interval of 3/2 needs
    1.5x the RPM. The tune is shifted (and, if it is too wide, squeezed) until it fits between
    the slowest speed you can hear and the fastest the fan will go.
  * Time gets stretched. A fan takes about half a second to change speed, so notes shorter
    than --min-ms are lengthened; everything else is stretched with them to keep the rhythm.

The result is a contour, not a cover version: you will recognise the shape of the tune, the
way you recognise a whistled melody. It prints a line you can paste into be3600-fan.

Reads 8/16/24/32-bit PCM WAV files. Pure standard library.
"""

import argparse
import array
import math
import sys
import wave

# Measured on a GL.iNet BE3600: what each PWM duty actually spins at, in RPM.
# Re-measure on other hardware with:  for d in ...; do echo $d > pwm1; sleep 2; cat fan1_input; done
CALIBRATION = [
    (36, 1096), (60, 1759), (90, 2711), (100, 3118), (115, 3349), (120, 3432),
    (130, 3702), (145, 3946), (160, 4223), (175, 4489), (190, 4677), (200, 4810),
    (205, 4875), (220, 5105), (235, 5278), (250, 5462), (255, 5567),
]


def read_mono(path):
    """The samples as floats in -1..1, plus the sample rate."""
    with wave.open(path, "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 1:                                     # 8-bit WAV is unsigned
        data = [(b - 128) / 128.0 for b in raw]
    elif width == 2:
        a = array.array("h")
        a.frombytes(raw)
        if sys.byteorder == "big":
            a.byteswap()
        data = [s / 32768.0 for s in a]
    elif width == 3:
        data = []
        for i in range(0, len(raw) - 2, 3):
            v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            if v & 0x800000:
                v -= 1 << 24
            data.append(v / 8388608.0)
    elif width == 4:
        a = array.array("i")
        a.frombytes(raw)
        if sys.byteorder == "big":
            a.byteswap()
        data = [s / 2147483648.0 for s in a]
    else:
        raise SystemExit("only 8, 16, 24 and 32-bit PCM WAV files, sorry")
    if ch > 1:
        data = [sum(data[i:i + ch]) / ch for i in range(0, len(data) - ch + 1, ch)]
    return data, rate


def downsample(data, rate, target=22050):
    """Autocorrelation gets cheaper the fewer samples there are, and we only care about pitch."""
    if rate <= target:
        return data, rate
    step = rate / float(target)
    out, i, n = [], 0.0, len(data)
    while i < n - 1:
        j = int(i)
        f = i - j
        out.append(data[j] * (1.0 - f) + data[j + 1] * f)
        i += step
    return out, int(rate / step)


def pitch_of(frame, rate, lo_hz, hi_hz):
    """
    The repeating period in one frame, by autocorrelation. Returns Hz, or 0 for "not a note".

    The correlation is normalised by the energy of the two windows being compared, otherwise it
    simply decays with lag. Then, rather than the tallest peak, we take the *first* peak that
    comes close to it: a signal that repeats every N samples also repeats every 2N, so the
    tallest peak is often an octave too low, and the earliest strong one is the real period.
    """
    n = len(frame)
    lag_min = max(2, int(rate / hi_hz))
    lag_max = min(n - 2, int(rate / lo_hz))
    if lag_max <= lag_min + 1:
        return 0.0
    if sum(s * s for s in frame) <= 1e-9:
        return 0.0

    corr = [0.0] * (lag_max + 2)
    for lag in range(lag_min, lag_max + 2):
        c = e1 = e2 = 0.0
        for i in range(n - lag):
            a, b = frame[i], frame[i + lag]
            c += a * b
            e1 += a * a
            e2 += b * b
        corr[lag] = c / math.sqrt(e1 * e2) if e1 > 1e-12 and e2 > 1e-12 else 0.0

    best = max(corr[lag_min:lag_max + 1])
    if best < 0.55:                                    # nothing periodic enough to call a note
        return 0.0
    chosen = 0
    for lag in range(lag_min + 1, lag_max):
        if corr[lag] >= 0.86 * best and corr[lag] >= corr[lag - 1] and corr[lag] >= corr[lag + 1]:
            chosen = lag
            break
    if not chosen:
        return 0.0
    # A parabola through the peak and its neighbours puts the period between two samples.
    a, b, c = corr[chosen - 1], corr[chosen], corr[chosen + 1]
    denom = a - 2 * b + c
    shift = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
    if abs(shift) > 1.0:
        shift = 0.0
    return rate / (chosen + shift)


def median3(pitches):
    """One stray frame between two agreeing neighbours is a glitch, not a note."""
    out = list(pitches)
    for i in range(1, len(pitches) - 1):
        a, b, c = pitches[i - 1], pitches[i], pitches[i + 1]
        if a > 0 and c > 0 and b > 0:
            out[i] = sorted((a, b, c))[1]
    return out


def track(data, rate, frame_ms, hop_ms, lo_hz, hi_hz, gate_db):
    """A pitch (or 0 for silence) every hop_ms."""
    frame_n = max(64, int(rate * frame_ms / 1000.0))
    hop_n = max(1, int(rate * hop_ms / 1000.0))
    peak = max((abs(s) for s in data), default=0.0) or 1.0
    gate = peak * (10.0 ** (gate_db / 20.0))
    out = []
    for start in range(0, max(1, len(data) - frame_n), hop_n):
        frame = data[start:start + frame_n]
        rms = math.sqrt(sum(s * s for s in frame) / len(frame))
        out.append(pitch_of(frame, rate, lo_hz, hi_hz) if rms >= gate else 0.0)
    return out


def to_notes(pitches, hop_ms, min_note_ms, tol=0.045):
    """Runs of frames at roughly the same pitch become one note: (Hz, milliseconds)."""
    notes, cur, count = [], 0.0, 0
    for p in list(pitches) + [0.0]:
        same = cur > 0 and p > 0 and abs(math.log(p / cur)) < tol
        if same:
            cur = (cur * count + p) / (count + 1)      # running mean keeps it steady
            count += 1
        else:
            if count:
                notes.append((cur, count * hop_ms))
            cur, count = (p, 1) if p > 0 else (0.0, 0)
    notes = [(f, ms) for f, ms in notes if ms >= min_note_ms]

    merged = []                                        # neighbours within a semitone are one note
    for f, ms in notes:
        if merged and abs(math.log(f / merged[-1][0])) < 0.035:
            pf, pms = merged[-1]
            merged[-1] = ((pf * pms + f * ms) / (pms + ms), pms + ms)
        else:
            merged.append((f, ms))
    return merged


def fit_to_fan(notes, rpm_lo, rpm_hi, squeeze_label):
    """Shift the tune into the fan's range, squeezing the intervals only if it will not fit."""
    lo = min(f for f, _ in notes)
    hi = max(f for f, _ in notes)
    span = math.log(hi / lo) if hi > lo else 0.0
    room = math.log(rpm_hi / rpm_lo)
    scale = 1.0 if span <= room or span == 0 else room / span
    if scale < 1.0:
        squeeze_label.append(scale)
    # The top note sits at the top of the range; everything else falls below it by its interval.
    return [(rpm_hi * math.exp(-scale * math.log(hi / f)), ms) for f, ms in notes]


NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(hz):
    n = int(round(12 * math.log(hz / 440.0, 2))) + 69
    return "%s%d" % (NAMES[n % 12], n // 12 - 1)


def rpm_to_duty(rpm):
    """Invert the calibration curve."""
    if rpm <= CALIBRATION[0][1]:
        return CALIBRATION[0][0]
    for (d0, r0), (d1, r1) in zip(CALIBRATION, CALIBRATION[1:]):
        if rpm <= r1:
            return int(round(d0 + (d1 - d0) * (rpm - r0) / float(r1 - r0)))
    return CALIBRATION[-1][0]


def main():
    ap = argparse.ArgumentParser(description="turn a short sound into fan chime steps")
    ap.add_argument("wav")
    ap.add_argument("--min-ms", type=int, default=300, help="shortest note the fan can show (default 300)")
    ap.add_argument("--max-ms", type=int, default=6000, help="be3600-fan will not hold the fan longer than this")
    ap.add_argument("--rpm-lo", type=int, default=2400, help="slowest speed worth hearing")
    ap.add_argument("--rpm-hi", type=int, default=5560, help="fastest the fan goes")
    ap.add_argument("--lo-hz", type=float, default=150.0)
    ap.add_argument("--hi-hz", type=float, default=2200.0)
    ap.add_argument("--gate-db", type=float, default=-26.0, help="quieter than this below the peak is silence")
    ap.add_argument("--max-notes", type=int, default=8, help="keep only the most prominent notes")
    ap.add_argument("--min-note-ms", type=int, default=45, help="anything shorter than this is a glitch, not a note")
    ap.add_argument("--name", help="print it as a be3600-fan chime entry with this name")
    args = ap.parse_args()

    data, rate = read_mono(args.wav)
    data, rate = downsample(data, rate)
    hop_ms = 8.0
    pitches = median3(track(data, rate, 46.0, hop_ms, args.lo_hz, args.hi_hz, args.gate_db))
    notes = to_notes(pitches, hop_ms, args.min_note_ms)
    if not notes:
        raise SystemExit("no pitched notes found -- try --gate-db -35 or a different clip")

    print("heard %d notes:" % len(notes))
    for f, ms in notes:
        print("   %7.1f Hz  %4d ms  %s" % (f, ms, note_name(f)))

    if len(notes) > args.max_notes:                    # keep the longest ones, in order
        keep = sorted(sorted(range(len(notes)), key=lambda i: -notes[i][1])[:args.max_notes])
        notes = [notes[i] for i in keep]
        print("kept the %d longest, so it fits the fan's patience" % len(notes))

    squeezed = []
    rpms = fit_to_fan(notes, args.rpm_lo, args.rpm_hi, squeezed)

    # Stretch time until a typical note is long enough for the fan to get there. Using the median
    # rather than the shortest note keeps one grace note from stretching the whole tune to a crawl;
    # anything still too short afterwards is simply held for the minimum.
    lens = sorted(ms for _, ms in notes)
    median_ms = lens[len(lens) // 2]
    stretch = max(1.0, args.min_ms / float(median_ms))
    durs = [max(args.min_ms, int(round(ms * stretch))) for _, ms in notes]
    total = sum(durs)
    if total > args.max_ms:                            # too long: shed the shortest notes first
        order = sorted(range(len(notes)), key=lambda i: notes[i][1])
        drop = set()
        for i in order:
            if total <= args.max_ms or len(drop) >= len(notes) - 2:
                break
            drop.add(i)
            total -= durs[i]
        if drop:
            print("dropped %d note(s) to fit the %d ms limit" % (len(drop), args.max_ms))
        rpms = [r for i, r in enumerate(rpms) if i not in drop]
        durs = [d for i, d in enumerate(durs) if i not in drop]

    steps = ["%d:%d" % (rpm_to_duty(rpm), d) for (rpm, _), d in zip(rpms, durs)]

    print()
    if squeezed:
        print("the tune spans more than the fan does; intervals squeezed to %.0f%%" % (squeezed[0] * 100))
    print("stretched x%.2f, %d ms in all" % (stretch, sum(durs)))
    print()
    line = " ".join(steps)
    if args.name:
        print("        %-11s echo \"%s\" ;;" % (args.name + ")", line))
    else:
        print("be3600-fan play \"%s\"" % line)


if __name__ == "__main__":
    main()
