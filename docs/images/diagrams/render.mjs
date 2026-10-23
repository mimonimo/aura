// SVG 구성도를 PNG(2배)로 — 헤드리스 크롬(따로 쓰는 user-data-dir)으로 그린다. 사용: node docs/images/diagrams/render.mjs
import {spawn} from 'node:child_process';
import {readdir, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
const chromeBin = process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const chrome = spawn(chromeBin, ['--headless', '--disable-gpu', '--no-first-run', '--hide-scrollbars', '--remote-debugging-port=0',
  '--user-data-dir=/private/tmp/aura-diagram-render', 'about:blank'], {stdio: ['ignore', 'ignore', 'pipe']});
const url = await new Promise((res, rej) => { let o = ''; chrome.stderr.on('data', b => { o += b; const m = o.match(/DevTools listening on (ws:\/\/\S+)/); if (m) res(m[1]); }); setTimeout(() => rej(Error('startup')), 15000).unref(); });
const ws = new WebSocket(url); await new Promise(r => ws.addEventListener('open', r, {once: true}));
let id = 0, session; const pending = new Map();
ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(Error(JSON.stringify(m.error))) : p.resolve(m.result); } };
const send = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, {resolve, reject}); ws.send(JSON.stringify({id: i, method, params, ...(session ? {sessionId: session} : {})})); });
try {
  const t = await send('Target.createTarget', {url: 'about:blank'});
  session = (await send('Target.attachToTarget', {targetId: t.targetId, flatten: true})).sessionId;
  await send('Page.enable');
  for (const f of (await readdir(here)).filter(f => f.endsWith('.svg')).sort()) {
    const svg = await readFile(path.join(here, f), 'utf8');
    const [, w, h] = svg.match(/width="(\d+)" height="(\d+)"/);
    await send('Emulation.setDeviceMetricsOverride', {width: +w, height: +h, deviceScaleFactor: 2, mobile: false});
    await send('Page.navigate', {url: 'file://' + path.join(here, f)});
    await new Promise(r => setTimeout(r, 700));
    const shot = await send('Page.captureScreenshot', {format: 'png', clip: {x: 0, y: 0, width: +w, height: +h, scale: 1}});
    await writeFile(path.join(here, f.replace(/\.svg$/, '.png')), Buffer.from(shot.data, 'base64'));
    console.log('png', f.replace(/\.svg$/, '.png'));
  }
} finally { ws.close(); chrome.kill(); }
