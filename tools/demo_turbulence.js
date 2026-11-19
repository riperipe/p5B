// 流体扰动 —— 卷曲噪声（curl noise）流场
//
// 一句话原理：
//   噪声本身是一个"标量场" ψ（可以想成水面高度），
//   取它的旋度 v = (∂ψ/∂y, -∂ψ/∂x) 当作速度场。
//   旋度场天生无源无汇（散度恒等于 0），所以粒子只会被"搅"着转，
//   不会往某些点里塌陷、也不会从某些点喷出来 —— 这就是它看起来像流体的原因。
//
// 为什么不能直接拿两个噪声当速度分量？
//   vx = noise(x, y), vy = noise(x+100, y+100)：这样的场有大量正负散度（源和汇），
//   粒子会聚成一坨一坨的脏点，看起来像静电而不是水流。
//   本目录的 test_turbulence.mjs 会把这个差别量出来。
//
// 用法：把整份代码贴进 Blender 的 Text datablock，点 Run。
//   有被跟踪的空物体 → 它当"搅拌的手"；没有 → 自己绕圈搅。
//
// @mode global

// ---------------------------------------------------------------- 可调参数
const COUNT = 700; // 粒子数
const SCALE = 0.0032; // 噪声空间尺度：越小漩涡越大
const OCTAVES = 2; // 噪声叠加层数：越多细节越碎
const GAIN = 1200; // 旋度 → 像素/帧 的换算系数（实测流场中位数约 1.2 px/帧）
const TIME_SPEED = 0.12; // 流场自身演化的速度
const FADE = 14; // 每帧背景压暗量（0-255）：越小拖尾越长
const STIR_RADIUS = 110; // 搅拌作用半径
const STIR_FORCE = 2.6; // 搅拌推力（像素/帧）
const FRAME = 1000 / 60; // 60fps 基准

// ---------------------------------------------------------------- 运行状态
let particles = [];
let lastMs = 0;
let stir = { x: 0, y: 0, active: true, label: 'auto' };

// --------------------------------------------------------------------- setup
function setup() {
  const device = window.p5inblender || {};
  createCanvas(device.w || 720, device.h || 400);
  pixelDensity(1);
  colorMode(HSB);
  noiseDetail(1, 0.5); // 自己叠 octave，这里让每次 noise() 只算一层（省一半时间）

  for (let i = 0; i < COUNT; i += 1) particles.push(spawn());
  background(0);
}

// ---------------------------------------------------------------------- draw
function draw() {
  const now = millis();
  // 真实帧间隔，换算成 60fps 基准的"帧"数
  const dt = Math.min(Math.max(now - (lastMs || now), 0), 100) / FRAME;
  lastMs = now;

  const device = window.p5inblender || {};
  if (device.w && device.h && (device.w !== width || device.h !== height)) {
    resizeCanvas(device.w, device.h);
  }

  const t = now * 0.001 * TIME_SPEED; // 流场时间：让它自己慢慢变
  stir = readStir(device, now);

  // 拖尾：不清屏，只压暗一层（alpha 越小尾巴越长）
  noStroke();
  fill(0, 0, 0, FADE);
  rect(0, 0, width, height);

  // 平流 + 画短线段
  for (const p of particles) {
    advect(p, dt, t);
    if (p.justReborn) continue; // 刚重生的就不画线，免得拉出一道横跨画面的长线
    stroke(p.hue, 62, 100, 130);
    strokeWeight(1);
    line(p.px, p.py, p.x, p.y);
  }

  drawStir();
  drawHud(device);
}

// ---------------------------------------------------------- 流场（核心两段）
// 1) 标量势：多层噪声叠加（fbm），返回 0..1
function potential(x, y, t) {
  let sum = 0;
  let amp = 1;
  let freq = 1;
  let norm = 0;
  for (let o = 0; o < OCTAVES; o += 1) {
    sum += noise(x * SCALE * freq, y * SCALE * freq, t) * amp;
    norm += amp;
    amp *= 0.5;
    freq *= 2.17;
  }
  return sum / norm;
}

// 2) 旋度：中心差分求偏导，再交叉组合
function sampleField(x, y, t) {
  const e = 1.5; // 差分步长（像素）
  const dPsiDy = (potential(x, y + e, t) - potential(x, y - e, t)) / (2 * e);
  const dPsiDx = (potential(x + e, y, t) - potential(x - e, y, t)) / (2 * e);
  // v = (∂ψ/∂y, -∂ψ/∂x)
  return createVector(dPsiDy * GAIN, -dPsiDx * GAIN);
}

// ------------------------------------------------------------ 粒子的平流
function advect(p, dt, t) {
  const v = sampleField(p.x, p.y, t);

  p.px = p.x;
  p.py = p.y;
  p.x += v.x * dt;
  p.y += v.y * dt;

  // 搅拌源：向外推。注意这是"额外"加的，流场本身仍然是无散的
  if (stir.active) {
    const dx = p.x - stir.x;
    const dy = p.y - stir.y;
    const d2 = dx * dx + dy * dy;
    if (d2 < STIR_RADIUS * STIR_RADIUS && d2 > 0.01) {
      const d = Math.sqrt(d2);
      const push = (1 - d / STIR_RADIUS) * STIR_FORCE * dt;
      p.x += (dx / d) * push;
      p.y += (dy / d) * push;
    }
  }

  // 寿命到了、或者飘出去了 → 换个地方重生
  p.age += dt;
  p.justReborn = false;
  if (p.age > p.life || p.x < -30 || p.x > width + 30 || p.y < -30 || p.y > height + 30) {
    respawn(p);
  }
}

function spawn() {
  const hue = random() < 0.12 ? random(300, 340) : random(170, 255);
  return {
    x: random(width),
    y: random(height),
    px: 0,
    py: 0,
    hue,
    age: 0,
    life: random(60, 360),
    justReborn: true,
  };
}

function respawn(p) {
  p.x = random(width);
  p.y = random(height);
  p.px = p.x;
  p.py = p.y;
  p.age = 0;
  p.life = random(60, 360);
  p.justReborn = true;
}

// ------------------------------------------------------- 搅拌源在哪
function readStir(device, now) {
  const objects = (device.state && device.state.objects) || [];
  const target = objects.find((o) => o.visible !== false);
  if (target) {
    return { x: target.x * width, y: target.y * height, active: true, label: target.name };
  }
  if (mouseIsPressed) {
    return { x: mouseX, y: mouseY, active: true, label: 'mouse' };
  }
  // 什么都没动：自己画一条李萨如曲线搅
  const th = now * 0.001 * 0.7;
  return {
    x: width * (0.5 + 0.32 * Math.sin(th)),
    y: height * (0.5 + 0.3 * Math.sin(th * 1.7 + 0.6)),
    active: true,
    label: 'auto',
  };
}

// ---------------------------------------------------------------- 准星与 HUD
function drawStir() {
  if (!stir.active) return;
  noFill();
  stroke(0, 0, 100, 40);
  circle(stir.x, stir.y, STIR_RADIUS * 2);
  stroke(0, 0, 100, 90);
  circle(stir.x, stir.y, 10);
}

function drawHud(device) {
  const fontSize = Math.max(9, Math.round(height / 32));
  noStroke();
  textSize(fontSize);
  textAlign(LEFT, BASELINE);
  fill(0, 0, 100, 110);
  text(
    'curl noise  ·  粒子 ' + particles.length +
      '  ·  搅拌 ' + stir.label +
      '  ·  ' + Math.round(frameRate()) + ' fps',
    12,
    height - 12
  );
}
