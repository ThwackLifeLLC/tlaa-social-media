// TLAA video factory renderer. Usage: node render.js spec.json out.mp4 [--frames t1,t2]
const fs = require('fs'), path = require('path'), { spawn, execFileSync } = require('child_process');
const { chromium } = require('playwright');
const [specPath, out] = process.argv.slice(2);
const framesArg = process.argv.includes('--frames') ? process.argv[process.argv.indexOf('--frames') + 1] : null;
const spec = JSON.parse(fs.readFileSync(specPath, 'utf8'));
const FPS = 30, root = __dirname;
const b64 = (p, mime) => `data:${mime};base64,${fs.readFileSync(p).toString('base64')}`;
const nm = path.join(root, 'node_modules/@fontsource');

// Captions: group timed words into short lines, breaking at sentence ends.
function captions(words) {
  const out = []; let cur = [];
  const flush = () => { if (cur.length) { out.push({ start: cur[0].start, words: cur }); cur = []; } };
  const small = /^(a|an|the|to|on|in|of|for|or|and|our|your|that's|there's|it's|from)$/i;
  words.forEach((w, i) => {
    cur.push(w);
    const end = /[.?!]$/.test(w.text), comma = /,$/.test(w.text) && cur.length >= 3;
    const full = cur.length >= 5 && !small.test(w.text) && !(words[i + 1] && /^[^ ]+[.?!]$/.test(words[i + 1].text) && cur.length < 7);
    if (end || comma || full) flush();
  });
  flush();
  out.forEach((c, i) => { c.end = i + 1 < out.length ? out[i + 1].start : c.words.at(-1).end + 0.6; });
  return out;
}
// The spoken web address reads as a real address on screen.
const shown = [];
for (let i = 0; i < spec.words.length; i++) {
  const w = spec.words[i], n = spec.words.slice(i, i + 4).map(x => x.text.toLowerCase()).join(' ');
  if (n === 'thwack life dot com.') { shown.push({ text: 'thwacklife.com', start: w.start, end: spec.words[i + 3].end }); i += 3; } else shown.push(w);
}
spec.captions = captions(shown);

let html = fs.readFileSync(path.join(root, 'template.html'), 'utf8')
  .replace('FONT_BC', b64(path.join(nm, 'barlow-condensed/files/barlow-condensed-latin-700-normal.woff2'), 'font/woff2'))
  .replace('FONT_M6', b64(path.join(nm, 'montserrat/files/montserrat-latin-600-normal.woff2'), 'font/woff2'))
  .replace('FONT_M8', b64(path.join(nm, 'montserrat/files/montserrat-latin-800-normal.woff2'), 'font/woff2'));
const logo = b64(path.join(root, spec.logo), 'image/png');
html = html.split('LOGO').join(logo).replace('SPEC', JSON.stringify(spec).split('LOGO').join(logo));

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
  await page.setContent(html, { waitUntil: 'load' });
  await page.evaluate(() => window.ready);
  // Block text that runs outside the area the apps keep clear.
  for (const sc of spec.scenes) {
    const bad = await page.evaluate(t => { window.draw(t); document.querySelectorAll('[data-at]').forEach(e => e.style.transform = 'none');
      const stage = document.getElementById('stage'); stage.style.transform = 'none'; const box = stage.getBoundingClientRect();
      let worst = null; const walker = document.createTreeWalker(stage, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) { const r = document.createRange(); r.selectNodeContents(walker.currentNode);
        for (const q of r.getClientRects()) if (q.right > 950 || q.left < 60 || q.top < 230 || q.bottom > 1240) worst = walker.currentNode.textContent.slice(0, 40); }
      return worst; }, sc.start + 0.6);
    if (bad) throw new Error(`Text outside safe area at ${sc.start}s: ${bad}`);
  }
  if (framesArg) {
    for (const t of framesArg.split(',').map(Number)) {
      await page.evaluate(x => window.draw(x), t);
      await page.screenshot({ path: `${out}-${t}.jpg`, type: 'jpeg', quality: 85 });
    }
    await browser.close(); return;
  }
  const silent = out.replace(/\.mp4$/, '.silent.tmp.mp4');
  const ff = spawn('ffmpeg', ['-y', '-hide_banner', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-r', String(FPS), silent], { stdio: ['pipe', 'inherit', 'inherit'] });
  const total = Math.ceil(spec.duration * FPS);
  for (let f = 0; f < total; f++) {
    await page.evaluate(x => window.draw(x), f / FPS);
    const buf = await page.screenshot({ type: 'jpeg', quality: 92 });
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
  }
  ff.stdin.end();
  await new Promise((res, rej) => ff.on('exit', c => c === 0 ? res() : rej(new Error('ffmpeg ' + c))));
  await browser.close();
  // Add the voice: louden to social level, keep a real audio track for the whole video.
  const tmp = out.replace(/\.mp4$/, '.tmp.mp4');
  execFileSync('ffmpeg', ['-y', '-hide_banner', '-loglevel', 'error', '-i', silent, '-i', path.join(root, spec.audio),
    '-filter_complex', `[1:a]loudnorm=I=-14:TP=-1.5:LRA=11,apad,atrim=0:${spec.duration},aresample=48000[a]`,
    '-map', '0:v', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-movflags', '+faststart', tmp]);
  fs.renameSync(tmp, out); fs.unlinkSync(silent);
  console.log('rendered', out);
})().catch(e => { console.error(e); process.exit(1); });
