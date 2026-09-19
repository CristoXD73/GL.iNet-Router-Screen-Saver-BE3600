#!/usr/bin/env python3
"""Convert between the two .bea animation formats. Standard library only.

    python3 bea2.py encode IN.bea OUT.bea     BEA1 (full frames) -> BEA2 (changes only, much smaller)
    python3 bea2.py decode IN.bea OUT.bea     BEA2 -> BEA1
    python3 bea2.py info   FILE.bea           show the format, size and how well it packs

The format is described in docs/BEA-FORMAT.md. Both formats play on the router;
BEA2 is just smaller (an animation that only moves a small part of the screen
shrinks by 10x or more) and writes less to the display.
"""
import struct
import sys

FRAME_BYTES = 43168
HEADER = 12
GAP = 12            # changed bytes closer together than this are merged into one span
SPAN_COST = 6       # u32 offset + u16 length in front of every span
FULL, DELTA, HOLD = 0, 1, 2


def read_header(data):
    if len(data) < HEADER:
        sys.exit("not a .bea file: shorter than the 12-byte header")
    magic = data[:4]
    if magic not in (b"BEA1", b"BEA2"):
        sys.exit("not a .bea file: bad magic %r" % magic)
    fps, records, frame_bytes = struct.unpack_from("<HHI", data, 4)
    if frame_bytes != FRAME_BYTES:
        sys.exit("frame size is %d bytes, this display needs %d" % (frame_bytes, FRAME_BYTES))
    return magic, fps, records


def read_bea1(data):
    """-> (fps, [(run, frame_bytes), ...])"""
    _, fps, records = read_header(data)
    if len(data) != HEADER + records * (2 + FRAME_BYTES):
        sys.exit("BEA1 size does not match its header")
    frames, pos = [], HEADER
    for _ in range(records):
        (run,) = struct.unpack_from("<H", data, pos)
        frames.append((run, data[pos + 2:pos + 2 + FRAME_BYTES]))
        pos += 2 + FRAME_BYTES
    return fps, frames


def spans_between(prev, cur):
    """Byte ranges (offset, bytes) where cur differs from prev, nearby ones merged."""
    if prev == cur:
        return []
    out, i, n = [], 0, FRAME_BYTES
    while i < n:
        if prev[i] == cur[i]:
            i += 1
            continue
        start = i
        last = i
        i += 1
        while i < n and i - last <= GAP:
            if prev[i] != cur[i]:
                last = i
            i += 1
        out.append((start, cur[start:last + 1]))
        i = last + 1
    # A span may not be longer than a u16.
    split = []
    for off, blob in out:
        while len(blob) > 65535:
            split.append((off, blob[:65535]))
            off, blob = off + 65535, blob[65535:]
        split.append((off, blob))
    return split


def record(run, kind, payload=b""):
    return struct.pack("<HBI", run, kind, len(payload)) + payload


def encode(src, dst):
    data = open(src, "rb").read()
    magic, _, _ = read_header(data)
    if magic == b"BEA2":
        sys.exit("already BEA2")
    fps, frames = read_bea1(data)
    recs, prev = [], None
    for run, frame in frames:
        if prev is None:
            recs.append([run, FULL, frame])
        else:
            spans = spans_between(prev, frame)
            if not spans:
                # identical to the previous frame: just hold it longer
                if recs[-1][0] + run <= 65535:
                    recs[-1][0] += run
                else:
                    recs.append([run, HOLD, b""])
            else:
                payload = struct.pack("<H", len(spans)) + b"".join(
                    struct.pack("<IH", off, len(b)) + b for off, b in spans)
                if len(spans) > 65535 or len(payload) >= FRAME_BYTES:
                    recs.append([run, FULL, frame])
                else:
                    recs.append([run, DELTA, payload])
        prev = frame
    body = b"".join(record(r, k, p) for r, k, p in recs)
    open(dst, "wb").write(b"BEA2" + struct.pack("<HHI", fps, len(recs), FRAME_BYTES) + body)
    print("wrote %s: %d -> %d bytes (%.1fx smaller), %d records" % (
        dst, len(data), HEADER + len(body), len(data) / (HEADER + len(body)), len(recs)))


def read_bea2(data):
    """-> (fps, [(run, kind, payload), ...]) with the structure fully validated."""
    _, fps, records = read_header(data)
    recs, pos = [], HEADER
    for i in range(records):
        if pos + 7 > len(data):
            sys.exit("truncated at record %d" % (i + 1))
        run, kind, plen = struct.unpack_from("<HBI", data, pos)
        pos += 7
        if pos + plen > len(data):
            sys.exit("truncated inside record %d" % (i + 1))
        recs.append((run, kind, data[pos:pos + plen]))
        pos += plen
    if pos != len(data):
        sys.exit("%d unexpected trailing bytes" % (len(data) - pos))
    return fps, recs


def apply_delta(frame, payload):
    frame = bytearray(frame)
    (n,) = struct.unpack_from("<H", payload, 0)
    pos = 2
    for _ in range(n):
        off, ln = struct.unpack_from("<IH", payload, pos)
        pos += 6
        frame[off:off + ln] = payload[pos:pos + ln]
        pos += ln
    return bytes(frame)


def decode(src, dst):
    data = open(src, "rb").read()
    magic, _, _ = read_header(data)
    if magic == b"BEA1":
        sys.exit("already BEA1")
    fps, recs = read_bea2(data)
    frames, cur = [], None
    for run, kind, payload in recs:
        if kind == FULL:
            cur = payload
        elif kind == DELTA:
            cur = apply_delta(cur, payload)
        # HOLD: same picture
        frames.append((run, cur))
    with open(dst, "wb") as f:
        f.write(b"BEA1" + struct.pack("<HHI", fps, len(frames), FRAME_BYTES))
        for run, frame in frames:
            f.write(struct.pack("<H", run) + frame)
    print("wrote %s: %d frames" % (dst, len(frames)))


def info(path):
    data = open(path, "rb").read()
    magic, fps, records = read_header(data)
    if magic == b"BEA1":
        _, frames = read_bea1(data)
        ticks = sum(r for r, _ in frames)
    else:
        _, recs = read_bea2(data)
        ticks = sum(r for r, _, _ in recs)
        kinds = [k for _, k, _ in recs]
        print("records: %d full, %d delta, %d hold" % (kinds.count(FULL), kinds.count(DELTA), kinds.count(HOLD)))
    print("%s  %d records, %d ticks at %d fps = %.1f s per loop, %d bytes"
          % (magic.decode(), records, ticks, fps, ticks / fps, len(data)))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "info":
        info(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] in ("encode", "decode"):
        {"encode": encode, "decode": decode}[sys.argv[1]](sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
