// 离线分析台：跑 demo_turbulence.js，并用数值方法验证核心结论
//
// 关键验证：v = (∂ψ/∂y, -∂ψ/∂x) 的散度恒为 0，而"两个噪声直接当速度分量"
// 的散度不为 0。散度 = 源/汇强度：为 0 才不会塌成一坨，才像流体。
//
// 跑法：node p5inBlender/tools/test_turbulence.mjs

import fs from 'node:fs';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('./demo_turbulence.js', import.meta.url));
const W = 640;
const H = 360;

// ---------------------------------------------------------------- 模拟 p5 API
class Vec {
  constructor(x = 0, y = 0) { this.x = x; this.y = y; }
  copy() { return new Vec(this.x, this.y); }
  set(x, y) { this.x = x; this.y = y; return this; }
  add(v) { this.x += v.x; this.y += v.y; return this; }
  sub(v) { this.x -= v.x; this.y -= v.y; return this; }
  mult(n) { this.x *= n; this.y *= n; return this; }
  mag() { return Math.hypot(this.x, this.y); }
  static sub(a, b) { return new Vec(a.x - b.x, a.y - b.y); }
}

function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// 确定性的 3D value noise（平滑插值，够中心差分数值求导用）
function hash3(i, j, k) {
  let h = Math.imul(i, 374761393) ^ Math.imul(j, 668265263) ^ Math.imul(k, 2147483647);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}
const smooth = (t) => t * t * (3 - 2 * t);
const lerp = (a, b, t) => a + (b - a) * t;
function noise3(x, y, z) {
  const xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z);
  const u = smooth(x - xi), v = smooth(y - yi), w = smooth(z - zi);
  const c = (dx, dy, dz) => hash3(xi + dx, yi + dy, zi + dz);
  const x00 = lerp(c(0, 0, 0), c(1, 0, 0), u);
  const x10 = lerp(c(0, 1, 0), c(1, 1, 0), u);
  const x01 = lerp(c(0, 0, 1), c(1, 0, 1), u);
  const x11 = lerp(c(0, 1, 1), c(1, 1, 1), u);
  return lerp(lerp(x00, x10, v), lerp(x01, x11, v), w);
}

function buildHarness() {
  let clock = 0;
  const calls = [];
  const rnd = mulberry32(20261119);
  const style = { fill: null, stroke: null };

  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.width = 0;
  sandbox.height = 0;
  sandbox.millis = () => clock;
  sandbox.frameRate = () => 60;
  sandbox.mouseX = -999;
  sandbox.mouseY = -999;
  sandbox.mouseIsPressed = false;
  sandbox.p5 = { Vector: Vec };
  sandbox.createVector = (x, y) => new Vec(x, y);
  sandbox.noise = noise3;
  sandbox.noiseDetail = () => {};
  sandbox.random = (a, b) => {
    if (a === undefined) return rnd();
    if (b === undefined) return rnd() * a;
    return a + rnd() * (b - a);
  };
  sandbox.createCanvas = (w, h) => { sandbox.width = w; sandbox.height = h; return { width: w, height: h }; };
  sandbox.resizeCanvas = (w, h) => { sandbox.width = w; sandbox.height = h; };
  sandbox.pixelDensity = () => {};
  sandbox.colorMode = () => {};
  sandbox.background = (a, b, c, d) => calls.push({ fn: 'background', args: [a, b, c, d] });
  sandbox.push = () => {};
  sandbox.pop = () => {};
  sandbox.noFill = () => { style.fill = null; };
  sandbox.noStroke = () => { style.stroke = null; };
  sandbox.fill = (...a) => { style.fill = a; };
  sandbox.stroke = (...a) => { style.stroke = a; };
  sandbox.strokeWeight = () => {};
  sandbox.rect = (x, y, w, h) => calls.push({ fn: 'rect', x, y, w, h, fill: style.fill });
  sandbox.line = (x1, y1, x2, y2) => calls.push({ fn: 'line', x1, y1, x2, y2 });
  sandbox.circle = (x, y, d) => calls.push({ fn: 'circle', x, y, d, stroke: style.stroke });
  sandbox.text = (t) => calls.push({ fn: 'text', text: t });
  sandbox.textSize = () => {};
  sandbox.textAlign = () => {};
  sandbox.CENTER = 'center';
  sandbox.LEFT = 'left';
  sandbox.BASELINE = 'baseline';
  sandbox.HSB = 'hsb';

  vm.runInNewContext(fs.readFileSync(SRC, 'utf8'), sandbox, { filename: SRC });
  sandbox.setup();

  return {
    sandbox,
    get clock() { return clock; },
    tracker(u, v, name = 'P5B_Tracker') {
      sandbox.p5inblender = {
        state: { objects: [{ name, x: u, y: v, depth: 1, visible: true }], frame: 0, t: 0 },
        t: 0, frame: 0, w: W, h: H, texture: null,
      };
    },
    frame(ms = 1000 / 60) {
      clock += ms;
      calls.length = 0;
      sandbox.draw();
      return calls.slice();
    },
    run(seconds, ms = 1000 / 60) {
      let last = [];
      const n = Math.round((seconds * 1000) / ms);
      for (let i = 0; i < n; i += 1) last = this.frame(ms);
      return last;
    },
    lines(calls) { return calls.filter((c) => c.fn === 'line'); },
  };
}

// ------------------------------------------------------------------- 分析工具
// 用有限差分算速度场的散度：∇·v = ∂vx/∂x + ∂vy/∂y
// 步长取 1.5，和 sampleField 里的差分步长一致：这样旋度场的散度只剩浮点误差
function divergenceAt(field, x, y, t, e = 1.5) {
  const vx1 = field(x + e, y, t).x;
  const vx0 = field(x - e, y, t).x;
  const vy1 = field(x, y + e, t).y;
  const vy0 = field(x, y - e, t).y;
  return ((vx1 - vx0) + (vy1 - vy0)) / (2 * e);
}

function stats(values) {
  const xs = values.slice().sort((a, b) => a - b);
  const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
  return {
    mean,
    median: xs[Math.floor(xs.length / 2)],
    max: xs[xs.length - 1],
    min: xs[0],
  };
}

const results = [];
function test(name, fn) {
  try {
    const extra = fn();
    results.push({ name, ok: true });
    console.log(`PASS  ${name}${extra ? '  → ' + extra : ''}`);
  } catch (err) {
    results.push({ name, ok: false, err: err.message });
    console.log(`FAIL  ${name}\n      ${err.message}`);
  }
}

// ------------------------------------------------------------------ 1. 散度
test('旋度场的散度 ≈ 0（对照组差几个数量级）', () => {
  const h = buildHarness();
  const field = (x, y, t) => h.sandbox.sampleField(x, y, t);
  // 对照组：直接把两个噪声当速度分量（网上最常见的写法）
  const s = 0.0032;
  const raw = (x, y, t) => new Vec((noise3(x * s, y * s, t) - 0.5) * 320, (noise3(x * s + 37, y * s + 91, t) - 0.5) * 320);

  const curlDiv = [];
  const rawDiv = [];
  for (let gx = 0; gx < 40; gx += 1) {
    for (let gy = 0; gy < 24; gy += 1) {
      const x = (gx + 0.5) * (W / 40);
      const y = (gy + 0.5) * (H / 24);
      curlDiv.push(Math.abs(divergenceAt(field, x, y, 1.3)));
      rawDiv.push(Math.abs(divergenceAt(raw, x, y, 1.3)));
    }
  }
  const c = stats(curlDiv);
  const r = stats(rawDiv);
  assert.ok(c.mean < 1e-9, `旋度场平均 |散度| 应≈0，实际 ${c.mean.toExponential(2)}`);
  assert.ok(r.mean > 0.05, `对照组的散度应该明显不为 0，实际 ${r.mean.toExponential(2)}`);
  return `旋度场 |div| = ${c.mean.toExponential(1)}（机器精度 0）/ 对照组均值 ${r.mean.toFixed(3)}`;
});

test('速度大小落在可用的量级（不是 0，也不会炸）', () => {
  const h = buildHarness();
  const mags = [];
  for (let gx = 0; gx < 40; gx += 1) {
    for (let gy = 0; gy < 24; gy += 1) {
      const v = h.sandbox.sampleField((gx + 0.5) * (W / 40), (gy + 0.5) * (H / 24), 2.0);
      mags.push(v.mag());
    }
  }
  const m = stats(mags);
  assert.ok(m.median > 0.15 && m.median < 6, `速度中位数 ${m.median.toFixed(2)} 不在 0.15..6 之间`);
  assert.ok(m.max < 20, `速度最大 ${m.max.toFixed(2)} 太大，粒子会瞬移`);
  return `|v| 中位数 ${m.median.toFixed(2)}、最大 ${m.max.toFixed(2)} px/帧`;
});

// ------------------------------------------------------------------ 2. 运动
test('粒子真的在动，且每帧都画线', () => {
  const h = buildHarness();
  h.tracker(0.5, 0.5);
  const calls = h.run(0.5);
  const lines = h.lines(calls);
  assert.ok(lines.length > 300, `每帧应画几百条线，实际 ${lines.length}`);
  const moved = lines.filter((l) => Math.hypot(l.x2 - l.x1, l.y2 - l.y1) > 1e-6);
  assert.ok(moved.length > lines.length * 0.9, '绝大多数线段的长度应大于 0（说明在平流）');
  for (const l of lines) {
    for (const k of ['x1', 'y1', 'x2', 'y2']) assert.ok(Number.isFinite(l[k]), `${k} = ${l[k]}`);
  }
  return `每帧 ${lines.length} 条线`;
});

test('粒子不会跑出画面（越界会重生）', () => {
  const h = buildHarness();
  h.tracker(0.5, 0.5);
  h.run(1);
  const calls = h.frame();
  for (const l of h.lines(calls)) {
    assert.ok(l.x2 > -40 && l.x2 < W + 40, `x=${l.x2.toFixed(1)} 越界`);
    assert.ok(l.y2 > -40 && l.y2 < H + 40, `y=${l.y2.toFixed(1)} 越界`);
  }
});

test('拖尾：每帧用半透明黑压一层（不是清屏）', () => {
  const h = buildHarness();
  h.tracker(0.5, 0.5);
  const calls = h.frame();
  const rect = calls.find((c) => c.fn === 'rect');
  assert.ok(rect, '每帧应该有一次 rect 覆盖');
  assert.equal(rect.w, W);
  assert.equal(rect.h, H);
  assert.ok(rect.fill && rect.fill.length === 4, 'fill 要带 alpha 才有拖尾');
  assert.ok(rect.fill[3] > 0 && rect.fill[3] < 60, `alpha=${rect.fill[3]} 应在 0..60`);
  return `alpha ${rect.fill[3]}/255`;
});

test('帧率无关：30fps 和 60fps 跑同样 2 秒，走过的总路程接近', () => {
  function pathLength(fps) {
    const h = buildHarness();
    h.tracker(0.5, 0.5);
    h.run(0.2, 1000 / fps); // 预热
    let total = 0;
    const n = Math.round((2000 * fps) / 1000);
    for (let i = 0; i < n; i += 1) {
      const calls = h.frame(1000 / fps);
      for (const l of h.lines(calls)) total += Math.hypot(l.x2 - l.x1, l.y2 - l.y1);
    }
    return total;
  }
  const a = pathLength(30);
  const b = pathLength(60);
  const diff = Math.abs(a - b) / b;
  assert.ok(diff < 0.15, `30fps 路程 ${(a / 1000).toFixed(1)}k vs 60fps ${(b / 1000).toFixed(1)}k，差 ${(diff * 100).toFixed(1)}%`);
  return `30fps ${(a / 1000).toFixed(1)}k px vs 60fps ${(b / 1000).toFixed(1)}k px（差 ${(diff * 100).toFixed(1)}%）`;
});

// ---------------------------------------------------------------- 3. 搅拌源
test('有空物体时，它当搅拌源并被标出来', () => {
  const h = buildHarness();
  h.tracker(0.25, 0.75, 'P5B_Tracker');
  const calls = h.frame();
  const hud = calls.filter((c) => c.fn === 'text').map((c) => c.text).join(' | ');
  assert.match(hud, /搅拌 P5B_Tracker/, hud);
});

test('没有空物体时，用自动李萨如搅拌源，不会报错', () => {
  const h = buildHarness();
  h.sandbox.p5inblender = { state: { objects: [] }, t: 0, frame: 0, w: W, h: H };
  const calls = h.run(1);
  const hud = calls.filter((c) => c.fn === 'text').map((c) => c.text).join(' | ');
  assert.match(hud, /搅拌 auto/, hud);
});

test('画布跟随插件分辨率', () => {
  const h = buildHarness();
  h.tracker(0.5, 0.5);
  h.frame();
  assert.equal(h.sandbox.width, W);
  assert.equal(h.sandbox.height, H);
});

// ------------------------------------------------------------------- 汇总
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} 通过`);
if (failed.length) {
  console.log(JSON.stringify(failed, null, 2));
  process.exit(1);
}
