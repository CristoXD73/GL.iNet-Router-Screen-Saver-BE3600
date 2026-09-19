/*
 * bea.js -- reads and writes .bea animations (BEA1 and BEA2) and converts pictures
 * to the GL-BE3600 display's frame layout. No dependencies; works in a browser
 * (global `BEA`) and in Node (require). The format is documented in
 * docs/BEA-FORMAT.md, and this file must stay in step with tools/bea2.py.
 *
 * A "landscape" picture is the wide 284 x 76 view of the display, which is how the
 * strip is usually seen. A "frame" is what the router wants: 76 x 284 pixels,
 * portrait, RGB565 little-endian, 43,168 bytes. dir says which way the wide view
 * is turned to get the frame: "cw" (clockwise) or "ccw".
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.BEA = factory();
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    var LW = 284, LH = 76;               // wide view
    var FW = 76, FH = 284;               // frame (portrait)
    var FRAME_BYTES = 43168;
    var HEADER = 12;
    var FULL = 0, DELTA = 1, HOLD = 2;
    var GAP = 12;                        // changed bytes closer than this share one span

    // ---- pictures <-> frames ------------------------------------------------

    // rgba: Uint8ClampedArray/Uint8Array of LW*LH*4  ->  Uint8Array(FRAME_BYTES)
    function landscapeToFrame(rgba, dir) {
        var out = new Uint8Array(FRAME_BYTES);
        for (var py = 0; py < FH; py++) {
            for (var px = 0; px < FW; px++) {
                var lx, ly;
                if (dir === 'ccw') { lx = LW - 1 - py; ly = px; }
                else               { lx = py;          ly = LH - 1 - px; }
                var s = (ly * LW + lx) * 4;
                var v = ((rgba[s] & 0xF8) << 8) | ((rgba[s + 1] & 0xFC) << 3) | (rgba[s + 2] >> 3);
                var o = py * (FW * 2) + px * 2;
                out[o] = v & 255;
                out[o + 1] = v >> 8;
            }
        }
        return out;
    }

    // Uint8Array(FRAME_BYTES) -> fills rgba (LW*LH*4) with the wide view
    function frameToLandscape(frame, dir, rgba) {
        for (var ly = 0; ly < LH; ly++) {
            for (var lx = 0; lx < LW; lx++) {
                var px, py;
                if (dir === 'ccw') { px = ly;          py = LW - 1 - lx; }
                else               { px = LH - 1 - ly; py = lx; }
                var o = py * (FW * 2) + px * 2;
                var v = frame[o] | (frame[o + 1] << 8);
                var r = (v >> 11) & 31, g = (v >> 5) & 63, b = v & 31;
                var d = (ly * LW + lx) * 4;
                rgba[d] = (r << 3) | (r >> 2);
                rgba[d + 1] = (g << 2) | (g >> 4);
                rgba[d + 2] = (b << 3) | (b >> 2);
                rgba[d + 3] = 255;
            }
        }
        return rgba;
    }

    // ---- writing ------------------------------------------------------------

    function le16(a, p, v) { a[p] = v & 255; a[p + 1] = (v >> 8) & 255; }
    function le32(a, p, v) { a[p] = v & 255; a[p + 1] = (v >>> 8) & 255; a[p + 2] = (v >>> 16) & 255; a[p + 3] = (v >>> 24) & 255; }
    function rd16(a, p) { return a[p] | (a[p + 1] << 8); }
    function rd32(a, p) { return (a[p] | (a[p + 1] << 8) | (a[p + 2] << 16) | (a[p + 3] * 16777216)) >>> 0; }

    function equal(a, b) {
        for (var i = 0; i < FRAME_BYTES; i++) if (a[i] !== b[i]) return false;
        return true;
    }

    // Identical neighbouring frames become one frame that stays up longer.
    function mergeRuns(frames) {
        var out = [];
        for (var i = 0; i < frames.length; i++) {
            var f = frames[i], last = out[out.length - 1];
            if (last && last.run + f.run <= 65535 && equal(last.data, f.data)) last.run += f.run;
            else out.push({ run: f.run, data: f.data });
        }
        return out;
    }

    function header(magic, fps, records) {
        var h = new Uint8Array(HEADER);
        for (var i = 0; i < 4; i++) h[i] = magic.charCodeAt(i);
        le16(h, 4, fps);
        le16(h, 6, records);
        le32(h, 8, FRAME_BYTES);
        return h;
    }

    function join(parts) {
        var n = 0, i;
        for (i = 0; i < parts.length; i++) n += parts[i].length;
        var out = new Uint8Array(n), p = 0;
        for (i = 0; i < parts.length; i++) { out.set(parts[i], p); p += parts[i].length; }
        return out;
    }

    // frames: [{run, data}]. Every frame is stored whole.
    function encodeBea1(frames, fps) {
        checkFps(fps);
        frames = mergeRuns(frames);
        var parts = [header('BEA1', fps, frames.length)];
        frames.forEach(function (f) {
            var r = new Uint8Array(2);
            le16(r, 0, f.run);
            parts.push(r, f.data);
        });
        return join(parts);
    }

    function spansBetween(prev, cur) {
        var out = [], i = 0, n = FRAME_BYTES;
        while (i < n) {
            if (prev[i] === cur[i]) { i++; continue; }
            var start = i, last = i;
            i++;
            while (i < n && i - last <= GAP) {
                if (prev[i] !== cur[i]) last = i;
                i++;
            }
            out.push({ off: start, data: cur.subarray(start, last + 1) });
            i = last + 1;
        }
        return out;
    }

    function record(run, kind, payload) {
        var h = new Uint8Array(7);
        le16(h, 0, run);
        h[2] = kind;
        le32(h, 3, payload.length);
        return [h, payload];
    }

    // The first frame in full, then only the changed byte ranges.
    function encodeBea2(frames, fps) {
        checkFps(fps);
        var recs = [], prev = null;
        frames.forEach(function (f) {
            if (prev === null) {
                recs.push({ run: f.run, kind: FULL, payload: f.data });
            } else {
                var spans = spansBetween(prev, f.data);
                if (spans.length === 0) {
                    var last = recs[recs.length - 1];
                    if (last.run + f.run <= 65535) last.run += f.run;
                    else recs.push({ run: f.run, kind: HOLD, payload: new Uint8Array(0) });
                } else {
                    var size = 2;
                    spans.forEach(function (s) { size += 6 + s.data.length; });
                    if (spans.length > 65535 || size >= FRAME_BYTES) {
                        recs.push({ run: f.run, kind: FULL, payload: f.data });
                    } else {
                        var p = new Uint8Array(size), at = 2;
                        le16(p, 0, spans.length);
                        spans.forEach(function (s) {
                            le32(p, at, s.off);
                            le16(p, at + 4, s.data.length);
                            p.set(s.data, at + 6);
                            at += 6 + s.data.length;
                        });
                        recs.push({ run: f.run, kind: DELTA, payload: p });
                    }
                }
            }
            prev = f.data;
        });
        var parts = [header('BEA2', fps, recs.length)];
        recs.forEach(function (r) { parts.push.apply(parts, record(r.run, r.kind, r.payload)); });
        return join(parts);
    }

    function checkFps(fps) {
        if (!(fps >= 1 && fps <= 24)) throw new Error('The speed must be 1 to 24 frames per second.');
    }

    // ---- reading (validates as strictly as the router does) ---------------------

    // -> { magic, fps, frames: [{run, data}], ticks }
    function decode(bytes) {
        if (bytes.length < HEADER) throw new Error('That file is too small to be a .bea animation.');
        var magic = String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3]);
        if (magic !== 'BEA1' && magic !== 'BEA2') throw new Error('That is not a .bea animation (it does not start with BEA1 or BEA2).');
        var fps = rd16(bytes, 4), n = rd16(bytes, 6), fb = rd32(bytes, 8);
        if (fb !== FRAME_BYTES) throw new Error('Its frames are ' + fb + ' bytes; this display needs ' + FRAME_BYTES + ' (76 x 284 pixels).');
        if (fps < 1 || fps > 24) throw new Error('Its speed is ' + fps + ' frames per second; it must be 1 to 24.');
        if (n < 1) throw new Error('It has no frames.');

        var frames = [], pos = HEADER, ticks = 0, cur = null, i;
        for (i = 0; i < n; i++) {
            var run, data;
            if (magic === 'BEA1') {
                if (pos + 2 + FRAME_BYTES > bytes.length) throw new Error('It is cut off at frame ' + (i + 1) + '. It may be incomplete.');
                run = rd16(bytes, pos);
                data = bytes.slice(pos + 2, pos + 2 + FRAME_BYTES);
                pos += 2 + FRAME_BYTES;
            } else {
                if (pos + 7 > bytes.length) throw new Error('It is cut off at frame ' + (i + 1) + '. It may be incomplete.');
                run = rd16(bytes, pos);
                var kind = bytes[pos + 2], plen = rd32(bytes, pos + 3);
                pos += 7;
                if (plen > bytes.length - pos) throw new Error('It is cut off at frame ' + (i + 1) + '. It may be incomplete.');
                if (i === 0 && kind !== FULL) throw new Error('The first frame must be a full picture.');
                if (kind === FULL) {
                    if (plen !== FRAME_BYTES) throw new Error('Frame ' + (i + 1) + ' is not a complete picture.');
                    cur = bytes.slice(pos, pos + plen);
                } else if (kind === DELTA) {
                    cur = cur.slice();
                    if (plen < 2) throw new Error('Frame ' + (i + 1) + ' is damaged.');
                    var spans = rd16(bytes, pos), at = 2;
                    for (var s = 0; s < spans; s++) {
                        if (at + 6 > plen) throw new Error('Frame ' + (i + 1) + ' is damaged.');
                        var off = rd32(bytes, pos + at), len = rd16(bytes, pos + at + 4);
                        at += 6;
                        if (len < 1 || off + len > FRAME_BYTES || at + len > plen) throw new Error('Frame ' + (i + 1) + ' is damaged.');
                        cur.set(bytes.subarray(pos + at, pos + at + len), off);
                        at += len;
                    }
                    if (at !== plen) throw new Error('Frame ' + (i + 1) + ' is damaged.');
                } else if (kind === HOLD) {
                    if (plen !== 0) throw new Error('Frame ' + (i + 1) + ' is damaged.');
                } else {
                    throw new Error('Frame ' + (i + 1) + ' is not valid BEA2 data.');
                }
                data = cur;
                pos += plen;
            }
            frames.push({ run: run, data: data });
            ticks += run;
        }
        if (pos !== bytes.length) throw new Error('Its size does not match its frames. It may be incomplete.');
        return { magic: magic, fps: fps, frames: frames, ticks: ticks };
    }

    return {
        LW: LW, LH: LH, FRAME_BYTES: FRAME_BYTES,
        landscapeToFrame: landscapeToFrame, frameToLandscape: frameToLandscape,
        encodeBea1: encodeBea1, encodeBea2: encodeBea2, decode: decode
    };
}));
