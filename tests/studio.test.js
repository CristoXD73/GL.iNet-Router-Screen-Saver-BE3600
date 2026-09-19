// Tests for studio/bea.js (the browser side of the project). Run: node tests/studio.test.js
'use strict';
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const crypto = require('crypto');
const BEA = require(path.join(__dirname, '..', 'studio', 'bea.js'));

let fails = 0;
function ok(cond, name) {
    console.log((cond ? '  ok    ' : '  FAIL  ') + name);
    if (!cond) fails++;
}
const same = (a, b) => a.length === b.length && Buffer.compare(Buffer.from(a), Buffer.from(b)) === 0;

console.log('== bea.js against the real bundled animation ==');
const original = zlib.gunzipSync(fs.readFileSync(path.join(__dirname, '..', 'animations', 'default.bea.gz')));
const anim = BEA.decode(new Uint8Array(original));
ok(anim.magic === 'BEA2' && anim.frames.length === 199 && anim.fps === 8 && anim.ticks === 200, 'decodes the bundled BEA2 (199 frames, 8 fps, 200 ticks)');
ok(same(BEA.encodeBea2(anim.frames, anim.fps), original), 're-encoding gives the identical bytes (matches tools/bea2.py)');
const full = BEA.encodeBea1(anim.frames, anim.fps);
const sha = crypto.createHash('sha256').update(full).digest('hex');
ok(sha === '17c7943efae58bfb87c2d03605a1802e674768c8547dd1f428178bd3151524e3', 'the full-frame BEA1 export is the original animation (SHA-256 matches)');
const back = BEA.decode(full);
ok(back.frames.length === anim.frames.length && back.frames.every((f, i) => f.run === anim.frames[i].run && same(f.data, anim.frames[i].data)), 'BEA1 round trip keeps every frame');

console.log('== turning pictures into frames ==');
function randomFrame() {
    const f = new Uint8Array(BEA.FRAME_BYTES);
    for (let i = 0; i < f.length; i++) f[i] = Math.floor(Math.random() * 256);
    return f;
}
['cw', 'ccw'].forEach(dir => {
    const frame = randomFrame();
    const rgba = new Uint8ClampedArray(BEA.LW * BEA.LH * 4);
    BEA.frameToLandscape(frame, dir, rgba);
    ok(same(BEA.landscapeToFrame(rgba, dir), frame), dir + ': frame -> wide picture -> frame is lossless');
});
function whiteColumn(lx) {
    const rgba = new Uint8ClampedArray(BEA.LW * BEA.LH * 4);
    for (let ly = 0; ly < BEA.LH; ly++) { const o = (ly * BEA.LW + lx) * 4; rgba[o] = rgba[o + 1] = rgba[o + 2] = 255; rgba[o + 3] = 255; }
    return rgba;
}
function rowIsWhite(frame, py) {
    for (let px = 0; px < 76; px++) { const o = py * 152 + px * 2; if (frame[o] !== 255 || frame[o + 1] !== 255) return false; }
    return true;
}
ok(rowIsWhite(BEA.landscapeToFrame(whiteColumn(0), 'cw'), 0), 'cw: the left edge of the wide view is the top row of the frame');
ok(rowIsWhite(BEA.landscapeToFrame(whiteColumn(0), 'ccw'), 283), 'ccw: the left edge of the wide view is the bottom row of the frame');
const red = new Uint8ClampedArray(BEA.LW * BEA.LH * 4);
for (let i = 0; i < red.length; i += 4) { red[i] = 255; red[i + 3] = 255; }
const rf = BEA.landscapeToFrame(red, 'cw');
ok(rf[0] === 0x00 && rf[1] === 0xF8, 'pure red is 0xF800 little-endian (RGB565)');

console.log('== writing and reading small animations ==');
const A = randomFrame(), B = A.slice(); B[100] ^= 0xFF; B[20000] ^= 0x55;
const frames = [{ run: 2, data: A }, { run: 1, data: B }, { run: 3, data: B }, { run: 1, data: A }];
const b2 = BEA.encodeBea2(frames, 12), b1 = BEA.encodeBea1(frames, 12);
const d2 = BEA.decode(b2), d1 = BEA.decode(b1);
ok(d2.ticks === 7 && d1.ticks === 7, 'runs add up to the loop length');
ok(d2.frames.length === 3 && d2.frames[1].run === 4, 'BEA2 merges identical neighbouring frames into a longer hold');
ok(same(d2.frames[0].data, A) && same(d2.frames[1].data, B) && same(d2.frames[2].data, A), 'BEA2 keeps every picture');
ok(b2.length < b1.length / 2, 'BEA2 is much smaller than BEA1 for a small change (' + b1.length + ' -> ' + b2.length + ')');
ok(d1.frames.length === 3, 'BEA1 merges identical neighbouring frames');

console.log('== bad files are refused with plain messages ==');
function throws(bytes, part, name) {
    try { BEA.decode(bytes); ok(false, name + ' (was accepted)'); }
    catch (e) { ok(e.message.indexOf(part) >= 0, name + ' -> "' + e.message + '"'); }
}
throws(new Uint8Array(5), 'too small', 'tiny file');
throws(b2.slice(0, b2.length - 1), 'cut off', 'truncated BEA2');
throws(Uint8Array.from([...b2, 0]), 'does not match', 'trailing byte');
const badMagic = b2.slice(); badMagic[0] = 88;
throws(badMagic, 'not a .bea', 'wrong magic');
const badFps = b2.slice(); badFps[4] = 99;
throws(badFps, 'speed', 'fps out of range');
const firstHold = b2.slice(); firstHold[14] = 2;
throws(firstHold, 'first frame', 'first record not a full picture');

console.log(fails ? '\n' + fails + ' studio test(s) FAILED.' : '\nAll studio tests passed.');
process.exit(fails ? 1 : 0);
