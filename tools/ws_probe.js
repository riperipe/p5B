// Node probe for the WebSocket path — same bytes a browser canvas would send.
// Requires Node >= 22 (global WebSocket, client frames are masked by the impl).
const W = 320, H = 180, FRAMES = 120, FPS = 60;

function buildFrame(seq) {
  const pixels = new Uint8Array(W * H * 4);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      const band = ((x + seq * 4) % 64) < 32;
      pixels[i] = band ? 240 : 20;
      pixels[i + 1] = Math.round((y / H) * 255);
      pixels[i + 2] = band ? 120 : 200;
      pixels[i + 3] = 255;
    }
  }
  const buf = new ArrayBuffer(20 + pixels.length);
  const dv = new DataView(buf);
  new Uint8Array(buf, 0, 4).set([0x50, 0x35, 0x42, 0x31]); // "P5B1"
  dv.setUint32(4, W, true);
  dv.setUint32(8, H, true);
  dv.setUint32(12, seq, true);
  dv.setUint32(16, pixels.length, true);
  new Uint8Array(buf, 20).set(pixels);
  return buf;
}

const ws = new WebSocket('ws://127.0.0.1:9877');
ws.binaryType = 'arraybuffer';
let seq = 0, t0 = 0;

ws.onopen = () => {
  console.log('ws open');
  t0 = performance.now();
  const tick = () => {
    if (seq >= FRAMES) {
      const dt = (performance.now() - t0) / 1000;
      console.log(`sent ${seq} frames in ${dt.toFixed(2)}s -> ${(seq / dt).toFixed(1)} fps`);
      ws.close();
      return;
    }
    ws.send(buildFrame(seq++));
    setTimeout(tick, 1000 / FPS);
  };
  tick();
};
ws.onerror = (e) => { console.error('ws error', e.message || e); process.exit(2); };
ws.onclose = () => { console.log('ws closed'); process.exit(0); };
