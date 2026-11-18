// Headless stand-in for the browser host: same WS protocol, no canvas.
// Answers `cook` requests deterministically and streams frames on request.
//
//   node tools/fake_host.js --seconds 20

const args = process.argv.slice(2);
function arg(name, def) {
  const i = args.indexOf('--' + name);
  return i >= 0 ? Number(args[i + 1]) : def;
}
const SECONDS = arg('seconds', 20);
const STREAM = args.includes('--stream');   // pass --stream to also push frames unprompted
const W = 640, H = 360;

let state = { objects: [], frame: 0, t: 0 };
let frames = 0, cooks = 0, t0 = 0;
const buf = new Uint8Array(W * H * 4);
const mask = new Uint8Array(W * H);

function fillBackground() {
  for (let y = 0; y < H; y++) {
    const v = y / H;
    const r = Math.round(10 + 18 * v), g = Math.round(12 + 10 * v), b = Math.round(22 + 30 * v);
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      buf[i] = r; buf[i + 1] = g; buf[i + 2] = b; buf[i + 3] = 255;
    }
  }
  mask.fill(0);
}

function disc(maskOrBuf, cx, cy, radius, col) {
  const r2 = radius * radius;
  const x0 = Math.max(0, Math.floor(cx - radius)), x1 = Math.min(W - 1, Math.ceil(cx + radius));
  const y0 = Math.max(0, Math.floor(cy - radius)), y1 = Math.min(H - 1, Math.ceil(cy + radius));
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      const dx = x - cx, dy = y - cy;
      const d2 = dx * dx + dy * dy;
      if (d2 > r2) continue;
      const k = 1 - Math.sqrt(d2) / radius;
      if (maskOrBuf === mask) {
        mask[y * W + x] = Math.max(mask[y * W + x], Math.round(255 * k));
      } else {
        const i = (y * W + x) * 4;
        buf[i] = Math.min(255, col[0] * k + buf[i] * (1 - k));
        buf[i + 1] = Math.min(255, col[1] * k + buf[i + 1] * (1 - k));
        buf[i + 2] = Math.min(255, col[2] * k + buf[i + 2] * (1 - k));
      }
    }
  }
}

function draw() {
  fillBackground();
  for (const o of state.objects || []) {
    if (o.visible === false) continue;
    disc(buf, o.x * W, o.y * H, 16, [255, 210, 90]);
    disc(buf, o.x * W, o.y * H, 5, [255, 255, 240]);
    disc(mask, o.x * W, o.y * H, 40, null);   // soft mask around the dot
  }
}

// v2 frame: header + plane table + payloads
function buildFrame(seq, req) {
  draw();
  const planes = [
    { name: 'rgba', channels: 4, data: buf },
    { name: 'mask', channels: 1, data: mask },
  ];
  const hdrLen = 24 + planes.length * 9;
  const total = planes.reduce((a, p) => a + p.data.length, 0);
  const out = new Uint8Array(hdrLen + total);
  const dv = new DataView(out.buffer);
  out.set([0x50, 0x35, 0x42, 0x32], 0);           // "P5B2"
  dv.setUint32(4, W, true);
  dv.setUint32(8, H, true);
  dv.setUint32(12, seq, true);
  dv.setUint32(16, req, true);
  dv.setUint16(20, planes.length, true);
  dv.setUint16(22, 0, true);
  let off = 24;
  for (const p of planes) {
    for (let i = 0; i < 4; i++) out[off + i] = p.name.charCodeAt(i) || 0;
    off += 4;
    out[off] = p.channels; off += 1;
    dv.setUint32(off, p.data.length, true); off += 4;
  }
  for (const p of planes) { out.set(p.data, off); off += p.data.length; }
  return out;
}

const ws = new WebSocket('ws://127.0.0.1:9877');
ws.binaryType = 'arraybuffer';

ws.onopen = () => {
  console.log('ws open');
  ws.send(JSON.stringify({ type: 'want_sketch' }));
  t0 = performance.now();
  const loop = () => {
    if ((performance.now() - t0) / 1000 > SECONDS) {
      console.log(`sent ${frames} frames, ${cooks} cooks`);
      ws.close();
      return;
    }
    if (STREAM && ws.readyState === WebSocket.OPEN) ws.send(buildFrame(frames++, 0));
    setTimeout(loop, 1000 / 30);
  };
  loop();
};

ws.onmessage = (ev) => {
  if (typeof ev.data !== 'string') return;
  const msg = JSON.parse(ev.data);
  if (msg.type === 'sketch') {
    console.log(`got sketch v${msg.version} (${msg.source.length} chars)`);
  } else if (msg.type === 'state') {
    state = msg;
  } else if (msg.type === 'cook') {
    // deterministic answer: render the requested frame with the requested state
    state = msg.state || state;
    cooks++;
    ws.send(buildFrame(msg.id, msg.id));
    console.log(`cooked id=${msg.id} frame=${msg.frame} t=${msg.t.toFixed(3)}`);
  }
};

ws.onerror = (e) => { console.error('ws error', e && e.message); process.exit(2); };
ws.onclose = () => process.exit(0);
