// 离线测试台：用模拟的 p5 API 跑 connected_particles_tracker.js
// 跑法：node p5inBlender/tools/test_connected_particles.mjs
//
// 验证点（都是这次改动的行为契约）：
//   1. 每 500ms 才采一次空物体位置，不是每帧
//   2. 空物体不动 → 不产生粒子
//   3. 空物体移动 → 每次采样一个粒子，速度 = 位移 * 0.05
//   4. 停住再动 → 开一条新路径
//   5. 粒子 5 秒后消失，路径清理干净
//   6. 帧率无关：20 / 50 / 100fps 下同一个粒子的漂移量一致
//   7. 没有空物体 / 不可见 → 不采样、不报错，HUD 给提示
//   8. 瞬移后重新起笔不会把粒子甩出画布
//   9. 坐标里不出现 NaN

import fs from 'node:fs';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('./connected_particles_tracker.js', import.meta.url));
const W = 640;
const H = 360;

class Vec {
  constructor(x = 0, y = 0) { this.x = x; this.y = y; }
  copy() { return new Vec(this.x, this.y); }
  set(x, y) { this.x = x; this.y = y; return this; }
  add(v) { this.x += v.x; this.y += v.y; return this; }
  sub(v) { this.x -= v.x; this.y -= v.y; return this; }
  mult(n) { this.x *= n; this.y *= n; return this; }
  mag() { return Math.hypot(this.x, this.y); }
  static sub(a, b) { return new Vec(a.x - b.x, a.y - b.y); }
  static mult(v, n) { return new Vec(v.x * n, v.y * n); }
}

function buildHarness() {
  let clock = 0;
  const calls = [];
  const style = { fill: null, stroke: null };

  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.width = 0;
  sandbox.height = 0;
  sandbox.millis = () => clock;
  sandbox.frameCount = 0;
  sandbox.p5 = { Vector: Vec };
  sandbox.createVector = (x, y) => new Vec(x, y);
  sandbox.createCanvas = (w, h) => { sandbox.width = w; sandbox.height = h; return { width: w, height: h }; };
  sandbox.resizeCanvas = (w, h) => { sandbox.width = w; sandbox.height = h; };
  sandbox.pixelDensity = () => {};
  sandbox.describe = (t) => calls.push({ fn: 'describe', text: t });
  sandbox.background = (v) => calls.push({ fn: 'background', v });
  sandbox.colorMode = () => {};
  sandbox.push = () => {};
  sandbox.pop = () => {};
  sandbox.noFill = () => { style.fill = null; };
  sandbox.noStroke = () => { style.stroke = null; };
  sandbox.fill = (...a) => { style.fill = a; };
  sandbox.stroke = (...a) => { style.stroke = a; };
  sandbox.strokeWeight = () => {};
  sandbox.circle = (x, y, d) => calls.push({ fn: 'circle', x, y, d, fill: style.fill, stroke: style.stroke });
  sandbox.line = (x1, y1, x2, y2) => calls.push({ fn: 'line', x1, y1, x2, y2, stroke: style.stroke });
  sandbox.text = (t, x, y) => calls.push({ fn: 'text', text: t, x, y });
  sandbox.textSize = () => {};
  sandbox.textAlign = () => {};
  sandbox.CENTER = 'center';
  sandbox.LEFT = 'left';
  sandbox.BASELINE = 'baseline';
  sandbox.HSB = 'hsb';
  sandbox.CORNER = 'corner';

  vm.runInNewContext(fs.readFileSync(SRC, 'utf8'), sandbox, { filename: SRC });
  sandbox.setup();

  const harness = {
    sandbox,
    get clock() { return clock; },
    state(objects, extra = {}) {
      sandbox.p5inblender = {
        state: { objects, frame: 0, t: clock / 1000, ...extra },
        t: clock / 1000,
        frame: 0,
        w: W,
        h: H,
        texture: null,
      };
    },
    // 空物体在画布上的归一化位置 -> state
    tracker(u, v, { name = 'P5B_Tracker', visible = true } = {}) {
      harness.state([{ name, x: u, y: v, depth: 1, visible }]);
    },
    frame(ms = 1000 / 60) {
      clock += ms;
      calls.length = 0;
      sandbox.frameCount += 1;
      sandbox.draw();
      return calls.slice();
    },
    run(seconds, ms = 1000 / 60) {
      const frames = Math.round((seconds * 1000) / ms);
      let last = [];
      for (let i = 0; i < frames; i += 1) last = harness.frame(ms);
      return last;
    },
    particles(calls) {
      // 粒子的圆是"有 fill"的；准星那个圆是 noFill + stroke
      return calls.filter((c) => c.fn === 'circle' && c.fill);
    },
    hud(calls) {
      const t = calls.filter((c) => c.fn === 'text').map((c) => c.text).join(' | ');
      return t;
    },
  };
  return harness;
}

const results = [];
function test(name, fn) {
  try {
    fn();
    results.push({ name, ok: true });
    console.log(`PASS  ${name}`);
  } catch (err) {
    results.push({ name, ok: false, err: err.message });
    console.log(`FAIL  ${name}\n      ${err.message}`);
  }
}

// ---------------------------------------------------------------- 1. 采样节奏
test('每 500ms 采一次：2.5 秒匀速拖动只产生 5 个粒子', () => {
  const h = buildHarness();
  h.tracker(0.5, 0.5);
  h.run(0.2);

  // 匀速：每秒 40px，即每次采样位移 20px
  let px = 0.5 * W;
  const dtMs = 1000 / 60;
  let produced = 0;
  const t0 = h.clock;
  while (h.clock - t0 < 2500) {
    px += 40 * (dtMs / 1000);
    h.tracker(px / W, 0.5);
    h.frame(dtMs);
  }
  // 从 harness 里读 HUD 上的粒子数
  const hud = h.hud(h.frame(dtMs));
  produced = Number((hud.match(/粒子 (\d+)/) || [])[1]);
  assert.equal(produced, 5, `期望 5 个粒子，实际 ${produced}（${hud}）`);
});

// ---------------------------------------------------------------- 2. 静止
test('空物体不动：不产生粒子', () => {
  const h = buildHarness();
  h.tracker(0.5, 0.5);
  const calls = h.run(3);
  assert.equal(h.particles(calls).length, 0, '静止时不该有粒子');
  assert.match(h.hud(calls), /idle/);
  assert.match(h.hud(calls), /每 0.5s 采样/);
});

// ---------------------------------------------------------------- 3. 粒度
test('一次 500ms 内的小幅抖动只算一次采样', () => {
  const h = buildHarness();
  h.tracker(0.5, 0.5);
  h.run(0.1);
  // 60 帧内（1 秒）抖动 3 次，但跨越两个采样点
  for (let i = 0; i < 60; i += 1) {
    h.tracker(0.5 + ((i % 2) ? 0.02 : 0), 0.5);
    h.frame();
  }
  const calls = h.run(0.1);
  const n = h.particles(calls).length;
  // 抖动一直"在动"，但只按 500ms 采样，1 秒最多 2~3 个
  assert.ok(n >= 1 && n <= 3, `粒子数应在 1..3，实际 ${n}`);
});

// ---------------------------------------------------------------- 4. 断开重连
test('停住再动：开一条新路径', () => {
  const h = buildHarness();
  h.tracker(0.3, 0.5);
  h.run(0.1);
  // 动 1.2 秒
  for (let i = 0; i < 72; i += 1) { h.tracker(0.3 + i * 0.002, 0.5); h.frame(); }
  let hud = h.hud(h.run(0.02));
  const pathsA = Number((hud.match(/路径 (\d+)/) || [])[1]);
  // 停 1.2 秒（同一位置）
  h.tracker(0.44, 0.5);
  h.run(1.2);
  // 再动 1.2 秒
  for (let i = 0; i < 72; i += 1) { h.tracker(0.44 + i * 0.002, 0.5); h.frame(); }
  hud = h.hud(h.run(0.02));
  const pathsB = Number((hud.match(/路径 (\d+)/) || [])[1]);
  assert.equal(pathsA, 1, `第一段路径数应为 1，实际 ${pathsA}`);
  assert.equal(pathsB, 2, `停住再动应变成 2 条路径，实际 ${pathsB}`);
});

// ---------------------------------------------------------------- 5. 淡出
test('停止移动 5 秒后粒子全部消失、路径被回收', () => {
  const h = buildHarness();
  h.tracker(0.3, 0.5);
  h.run(0.1);
  for (let i = 0; i < 120; i += 1) { h.tracker(0.3 + i * 0.001, 0.5); h.frame(); }
  assert.ok(h.particles(h.frame()).length > 0, '拖动后应该有粒子');
  const calls = h.run(6);
  assert.equal(h.particles(calls).length, 0, '6 秒后不该还有粒子');
  assert.match(h.hud(calls), /粒子 0/, h.hud(calls));
  assert.match(h.hud(calls), /路径 0/, h.hud(calls));
});

// ---------------------------------------------------------------- 6. 帧率无关
test('20/50/100fps 下同一个粒子的漂移量一致（±2%）', () => {
  // 第一帧放在 t=0，采样点正好落在 500/1000/1500ms 上，
  // 三个帧率吃到的位移和生成时刻完全相同，差别只剩逐帧积分本身。
  function drift(fps) {
    const h = buildHarness();
    const ms = 1000 / fps;
    const x0 = 0.4 * W;
    const speed = 40; // px/s
    // 0~500ms 不动，500~1500ms 匀速，之后停住让粒子自己衰减
    const xAt = (t) => (t <= 500 ? x0 : x0 + (speed * (Math.min(t, 1500) - 500)) / 1000);

    h.tracker(x0 / W, 0.5);
    h.frame(0); // t=0，第一次采样：只记基准位置
    while (h.clock < 1500 - 1e-9) {
      const t = h.clock + ms;
      h.tracker(xAt(Math.min(t, 1500)) / W, 0.5);
      h.frame(ms);
    }
    // 1500ms 采样时生成了第二个粒子（速度 = 20px * 0.05），之后停住跑 1 秒
    h.tracker(xAt(1500) / W, 0.5);
    while (h.clock < 2500 - 1e-9) h.frame(ms);

    const ps = h.particles(h.frame(ms));
    assert.equal(ps.length, 2, `${fps}fps 期望 2 个粒子，实际 ${ps.length}`);
    // display() 是倒着遍历的，ps[0] 是最新那个（有速度的）
    assert.ok(ps[0].fill[3] > ps[1].fill[3], '最新粒子应该比首粒子不透明');
    return ps[0].x - xAt(1500);
  }
  const d20 = drift(20);
  const d50 = drift(50);
  const d100 = drift(100);
  const spread = (Math.max(d20, d50, d100) - Math.min(d20, d50, d100)) / Math.abs(d50);
  assert.ok(
    spread < 0.02,
    `漂移 ${(spread * 100).toFixed(2)}% 偏差（20fps=${d20.toFixed(3)}, 50fps=${d50.toFixed(3)}, 100fps=${d100.toFixed(3)}）`
  );
  // 顺带核对量级：速度 1px/帧，无限时间总漂移 = 1/ln(1/0.95) ≈ 19.49px；
  // 这里只观察 1 秒，剩 0.95^60 ≈ 4.6%，所以约 18.6px
  assert.ok(Math.abs(d50 - 18.65) < 0.25, `50fps 档 1 秒漂移 ${d50.toFixed(2)} 应接近 18.65`);
});

// ---------------------------------------------------------------- 7. 异常输入
test('没有跟踪物体：不崩、HUD 提示、不产生粒子', () => {
  const h = buildHarness();
  h.state([]);
  const calls = h.run(2);
  assert.equal(h.particles(calls).length, 0);
  assert.match(h.hud(calls), /没有可跟踪的物体/);
  assert.match(h.hud(calls), /Track Selected/);
});

test('空物体跑到相机背后（visible=false）：当作松手，不产生粒子', () => {
  const h = buildHarness();
  h.tracker(0.5, 0.5);
  h.run(0.1);
  for (let i = 0; i < 60; i += 1) { h.tracker(0.5 + i * 0.002, 0.5, { visible: false }); h.frame(); }
  const calls = h.run(0.2);
  assert.equal(h.particles(calls).length, 0);
});

test('名字对不上时退而用第一个被跟踪的物体', () => {
  const h = buildHarness();
  h.state([
    { name: 'SomeOtherEmpty', x: 0.2, y: 0.5, depth: 1, visible: true },
    { name: 'P5B_Tracker', x: 0.8, y: 0.5, depth: 1, visible: true },
  ]);
  h.run(0.1);
  for (let i = 0; i < 60; i += 1) {
    h.state([
      { name: 'SomeOtherEmpty', x: 0.2, y: 0.5, depth: 1, visible: true },
      { name: 'P5B_Tracker', x: 0.8, y: 0.5, depth: 1, visible: true },
    ]);
    h.frame();
  }
  const hud = h.hud(h.run(0.1));
  assert.match(hud, /P5B_Tracker/, hud);
});

test('空物体瞬移后重新起笔：粒子不会被甩出画布', () => {
  const h = buildHarness();
  h.tracker(0.2, 0.5);
  h.run(0.1);
  for (let i = 0; i < 72; i += 1) { h.tracker(0.2 + i * 0.002, 0.5); h.frame(); }
  h.tracker(0.344, 0.5);
  h.run(1.2); // 停住，路径断开
  h.tracker(0.85, 0.5); // 瞬移到画面右边
  h.run(1.2);
  const ps = h.particles(h.frame());
  assert.ok(ps.length >= 1, '瞬移后至少应该有一个新粒子的落点');
  for (const p of ps) {
    assert.ok(p.x > -8 && p.x < W + 8, `粒子 x=${p.x.toFixed(1)} 跑出画布`);
    assert.ok(p.y > -8 && p.y < H + 8, `粒子 y=${p.y.toFixed(1)} 跑出画布`);
  }
});

test('坐标里没有 NaN / Infinity', () => {
  const h = buildHarness();
  h.tracker(0.5, 0.5);
  h.run(0.1);
  for (let i = 0; i < 200; i += 1) { h.tracker(0.1 + (i % 50) * 0.015, 0.2 + (i % 30) * 0.01); h.frame(); }
  const calls = [...h.particles(h.frame()), ...h.frame().filter((c) => c.fn === 'line')];
  for (const c of calls) {
    for (const k of ['x', 'y', 'x1', 'y1', 'x2', 'y2', 'd']) {
      if (k in c) assert.ok(Number.isFinite(c[k]), `${c.fn}.${k} = ${c[k]}`);
    }
  }
});

test('画布跟随插件分辨率（640x360）', () => {
  const h = buildHarness();
  h.tracker(0.5, 0.5);
  h.frame();
  assert.equal(h.sandbox.width, W);
  assert.equal(h.sandbox.height, H);
  const bg = h.frame().find((c) => c.fn === 'background');
  assert.equal(bg.v, 0, '每帧都要清成黑底');
});

// ------------------------------------------------------------------- 汇总
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} 通过`);
if (failed.length) {
  console.log(JSON.stringify(failed, null, 2));
  process.exit(1);
}
