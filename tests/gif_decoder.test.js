// Tests for the GIF decoder inside studio/index.html.
//   node tests/gif_decoder.test.js FIXTURE_DIR      (FIXTURE_DIR from tests/make_gif_fixtures.py)
'use strict';
const fs = require('fs');
const path = require('path');

const dir = process.argv[2];
if (!dir) { console.log('usage: node tests/gif_decoder.test.js FIXTURE_DIR'); process.exit(2); }

const html = fs.readFileSync(path.join(__dirname, '..', 'studio', 'index.html'), 'utf8');
const m = html.match(/\/\* GIF-DECODER-BEGIN \*\/([\s\S]*?)\/\* GIF-DECODER-END \*\//);
if (!m) { console.log('  FAIL  could not find the decoder in studio/index.html'); process.exit(1); }
const dec = new Function(m[1] + '; return { parseGif, gifCompose, gifLzw, gifDeinterlace };')();

let fails = 0;
function ok(cond, name) { console.log((cond ? '  ok    ' : '  FAIL  ') + name); if (!cond) fails++; }

console.log('== decoding real GIFs, compared with Pillow pixel for pixel ==');
for (const name of ['anim', 'synth', 'noise']) {
  const gif = fs.readFileSync(path.join(dir, name + '.gif'));
  const ref = fs.readFileSync(path.join(dir, name + '.ref'));
  const meta = JSON.parse(fs.readFileSync(path.join(dir, name + '.json'), 'utf8'));
  const g = dec.parseGif(gif.buffer.slice(gif.byteOffset, gif.byteOffset + gif.length));
  ok(g.width === meta.w && g.height === meta.h, name + ': size ' + g.width + ' x ' + g.height);
  ok(g.frames.length === meta.delays.length, name + ': ' + g.frames.length + ' frames');

  const size = meta.w * meta.h * 4;
  let count = 0, wrong = 0, badDelay = 0, firstBad = '';
  dec.gifCompose(g, (rgba, delay, i) => {
    count++;
    const want = meta.delays[i] <= 10 ? 100 : meta.delays[i];
    if (delay !== want) badDelay++;
    for (let p = 0; p < size; p += 4) {
      const o = i * size + p;
      const refA = ref[o + 3];
      // Where the picture shows, the colour must match; where it is see-through, it must be see-through.
      const same = refA === 0 ? rgba[p + 3] === 0
        : rgba[p + 3] === 255 && rgba[p] === ref[o] && rgba[p + 1] === ref[o + 1] && rgba[p + 2] === ref[o + 2];
      if (!same) { wrong++; if (!firstBad) firstBad = 'frame ' + i + ' pixel ' + (p / 4); }
    }
  });
  ok(count === meta.delays.length, name + ': every frame is produced');
  ok(wrong === 0, name + ': every pixel of every frame matches' + (wrong ? ' (' + wrong + ' differ, first at ' + firstBad + ')' : ''));
  ok(badDelay === 0, name + ': frame delays match');
}

console.log('== interlaced rows ==');
{
  // 10 rows of width 2, stored interlaced: rows 0,8 | 4 | 2,6 | 1,3,5,7,9  (value = the row it belongs to)
  const order = [0, 8, 4, 2, 6, 1, 3, 5, 7, 9];
  const idx = new Uint8Array(order.flatMap(r => [r, r]));
  const out = dec.gifDeinterlace(idx, 2, 10);
  let good = true;
  for (let r = 0; r < 10; r++) if (out[r * 2] !== r || out[r * 2 + 1] !== r) good = false;
  ok(good, 'interlaced rows go back to their places');
}

console.log('== files that are not usable ==');
function throws(bytes, part, name) {
  try { dec.parseGif(bytes.buffer ? bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length) : bytes); ok(false, name + ' (was accepted)'); }
  catch (e) { ok(e.message.indexOf(part) >= 0, name + ' -> "' + e.message + '"'); }
}
throws(new Uint8Array(20), 'not a GIF', 'not a GIF at all');
throws(Buffer.from('GIF89a'), 'not a GIF', 'a GIF cut off after its signature');
throws(Buffer.from([0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 1, 0, 1, 0, 0, 0, 0, 0x3B]), 'no pictures', 'a GIF with no pictures');

console.log(fails ? '\n' + fails + ' GIF decoder test(s) FAILED.' : '\nAll GIF decoder tests passed.');
process.exit(fails ? 1 : 0);
