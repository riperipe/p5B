// Connected Particles —— 由 Blender 空物体的位置驱动
//
// 原版（p5.js 官方示例）是鼠标按下开一条新路径、拖拽不断加粒子。
// 这一版把"鼠标"换成 Blender 场景里被跟踪的空物体：
//
//   · 每 SAMPLE_MS（500ms）读一次空物体的屏幕位置（p5inblender.state.objects）
//   · 位置有变化 → 加一个粒子，速度 = 位移 × 0.05（沿用原版算法）
//   · 空物体停住 → 相当于松开鼠标：路径在这里断开
//   · 再次移动 → 开一条新路径（对应原版的 mousePressed）
//
// 用法：3D 视图选中空物体 → p5inBlender 面板 → Tracking → Track Selected，
// 然后在视口里拖它，或者给它打关键帧、播时间线，都行。
//
// 插件里两种模式都能跑；这份沿用函数式（全局）写法，所以标了 @mode global。

// @mode global

// ------------------------------------------------------------------ 可调参数
const SAMPLE_MS = 500; // 采样间隔：每半秒更新一次
const TRACKER_NAME = 'P5B_Tracker'; // 空物体名字；置空 '' = 用第一个被跟踪的物体
const FADE_SECONDS = 5; // 粒子淡出时间
const DRAG = 0.95; // 速度衰减（按 60fps 折算）
const VELOCITY_SCALE = 0.05; // 位移 → 初速度
const MOVE_EPS = 1.5; // 像素位移小于它算"没动"
const PARTICLE_SIZE = 24; // 圆点直径（和原版一致）
const FRAME_MS = 1000 / 60; // 原版按 60fps 写的，这里拿它当基准
const DRAG_LOG = -Math.log(DRAG); // ln(1/0.95)，速度衰减的积分系数

// ------------------------------------------------------------------ 运行状态
let paths = []; // 一条路径 = 一串粒子

let nextSampleMs = 0; // 下一次采样的时间戳（固定节奏，不随帧长漂移）
let previousSamplePosition = null; // 上次采样到的位置
let moving = false; // 空物体现在是不是在动
let lastDrawMs = 0; // 上一帧时间，用来算真实帧间隔

let trackerPosition = null; // 每帧读到的实时位置（只用来画准星，不参与采样）
let trackerName = ''; // 当前驱动这份 sketch 的空物体名字

// --------------------------------------------------------------------- setup
function setup() {
  const device = window.p5inblender || {};
  createCanvas(device.w || 720, device.h || 400);
  pixelDensity(1);
  colorMode(HSB);

  describe(
    'An empty object in Blender is sampled twice a second. Every time it moves, a multicolored circle outlined in white is left behind and connected to the previous one by a white line. The circles and lines fade out over time.'
  );
}

// ---------------------------------------------------------------------- draw
function draw() {
  const now = millis();
  // 真实帧间隔（夹住后台卡顿的大跳变），粒子运动按它折算
  const dt = Math.min(Math.max(now - (lastDrawMs || now), 0), 100);
  lastDrawMs = now;

  background(0);

  // 跟随实例分辨率（全局模式下画布尺寸由插件在 state 里播报）
  const device = window.p5inblender || {};
  if (device.w && device.h && (device.w !== width || device.h !== height)) {
    resizeCanvas(device.w, device.h);
  }

  // 实时位置：每帧都读，画准星用
  trackerPosition = readTrackerPosition();

  // 到点就采一次样：按固定间隔排下一次，避免节奏随帧长慢慢漂
  if (now >= nextSampleMs) {
    takeSample();
    nextSampleMs += SAMPLE_MS;
    // 卡顿/后台切回来之后不补采样，直接重排
    if (nextSampleMs <= now) nextSampleMs = now + SAMPLE_MS;
  }

  for (const path of paths) path.update(dt);
  for (const path of paths) path.display();

  // 粒子掉光的路径直接丢掉，不然数组只涨不落
  paths = paths.filter((path) => path.particles.length > 0);

  drawSource();
  drawHud(device);
}

// ---------------------------------------------------------- 读空物体的位置
// 返回画布像素坐标；拿不到（没跟踪 / 在相机背面 / 数值异常）返回 null
function readTrackerPosition() {
  const device = window.p5inblender || {};
  const state = device.state || {};
  const objects = state.objects || [];
  if (!objects.length) return null;

  let target = null;
  if (TRACKER_NAME) {
    target = objects.find((object) => object.name === TRACKER_NAME) || null;
  }
  // 名字对不上（或没设）就退而用第一个被跟踪的物体
  if (!target) target = objects[0];
  if (!target || target.visible === false) return null;

  const x = target.x * width;
  const y = target.y * height;
  if (!isFinite(x) || !isFinite(y)) return null;

  trackerName = target.name || '';
  return createVector(x, y);
}

// 圆点直径跟着画布高度走：原版 720x400 上是 24
function particleSize() {
  return Math.max(8, Math.round((PARTICLE_SIZE * height) / 400));
}

// ------------------------------------------------------------ 每半秒采一次样
function takeSample() {
  if (trackerPosition === null) {
    // 空物体没了/不可见：当鼠标离开画布，断开路径
    previousSamplePosition = null;
    moving = false;
    return;
  }

  if (previousSamplePosition === null) {
    // 第一个采样点没有位移信息，只记住位置
    previousSamplePosition = trackerPosition.copy();
    return;
  }

  const delta = p5.Vector.sub(trackerPosition, previousSamplePosition);
  const distance = delta.mag();

  if (distance <= MOVE_EPS) {
    // 停住了 = 松手，下一次移动会开新路径
    moving = false;
    previousSamplePosition.set(trackerPosition.x, trackerPosition.y);
    return;
  }

  if (!moving) {
    // 静止之后重新动起来 = 原版的 mousePressed。
    // 路径的第一个粒子速度给 0（原版就是把这个位置重置成按下点）。
    // 顺带挡住一种情况：空物体被瞬移时位移会很大，照算速度会把粒子甩出画布。
    paths.push(new Path());
    moving = true;
    paths[paths.length - 1].addParticle(trackerPosition, createVector(0, 0));
    previousSamplePosition.set(trackerPosition.x, trackerPosition.y);
    return;
  }

  // 速度跟着位移走，和原版一样乘 0.05
  const velocity = delta.copy().mult(VELOCITY_SCALE);
  paths[paths.length - 1].addParticle(trackerPosition, velocity);

  previousSamplePosition.set(trackerPosition.x, trackerPosition.y);
}

// ------------------------------------------------------------------- 路径
class Path {
  constructor() {
    this.particles = [];
    this.created = 0; // 这条路径已经生成过多少个粒子（决定色相）
  }

  addParticle(position, velocity) {
    // 色相按"第几个粒子"往下走。
    // 原版用的是 this.particles.length：0.5s 一个粒子时数组长度很快饱和，
    // 色相就停在同一个值上，整条尾巴一个颜色；用计数器才能保持彩虹。
    const particleHue = (this.created * 30) % 360;
    this.created += 1;
    this.particles.push(new Particle(position, velocity, particleHue));
  }

  update(dt) {
    for (const particle of this.particles) {
      particle.update(dt);
    }
  }

  // 两个粒子之间连一条线，透明度跟着前一个粒子的剩余寿命
  connectParticles(particleA, particleB) {
    const opacity = particleA.lifeRemaining / particleA.life;
    stroke(255, opacity);
    line(
      particleA.position.x,
      particleA.position.y,
      particleB.position.x,
      particleB.position.y
    );
  }

  display() {
    // 倒着循环：删掉一个粒子时，下一个下标还对着它前面那个
    for (let i = this.particles.length - 1; i >= 0; i -= 1) {
      const particle = this.particles[i];

      // 寿命用完就删掉
      if (particle.lifeRemaining <= 0) {
        this.particles.splice(i, 1);
        continue;
      }

      particle.display();

      // 后面还有粒子就连线
      if (i < this.particles.length - 1) {
        this.connectParticles(particle, this.particles[i + 1]);
      }
    }
  }
}

// ---------------------------------------------------------------- 单个粒子
class Particle {
  constructor(position, velocity, hue) {
    this.position = position.copy();
    this.velocity = velocity.copy();
    this.hue = hue;
    this.life = FADE_SECONDS * 1000;
    this.lifeRemaining = this.life;
  }

  update(dt) {
    // 60fps 基准下的"帧"数
    const steps = dt / FRAME_MS;

    // 这一帧之后的速度倍率
    const decay = Math.pow(DRAG, steps);

    // 位移用速度的指数衰减积分算（不是 v*dt），
    // 这样 30fps 和 60fps 下同一个粒子漂的距离一样
    this.position.add(p5.Vector.mult(this.velocity, (1 - decay) / DRAG_LOG));

    // 减速
    this.velocity.mult(decay);

    // 淡出
    this.lifeRemaining = Math.max(0, this.lifeRemaining - dt);
  }

  display() {
    const opacity = this.lifeRemaining / this.life;
    noStroke();
    fill(this.hue, 80, 90, opacity);
    circle(this.position.x, this.position.y, particleSize());
  }
}

// ------------------------------------------------------------ 准星与状态文字
function drawSource() {
  if (trackerPosition === null) return;

  push();
  noFill();
  strokeWeight(1);
  stroke(0, 0, 100, 60);
  circle(trackerPosition.x, trackerPosition.y, particleSize() + 18);
  stroke(0, 0, 100, 130);
  line(trackerPosition.x - 10, trackerPosition.y, trackerPosition.x + 10, trackerPosition.y);
  line(trackerPosition.x, trackerPosition.y - 10, trackerPosition.x, trackerPosition.y + 10);
  pop();
}

function drawHud(device) {
  const objects = (device.state && device.state.objects) || [];
  const fontSize = Math.max(9, Math.round(height / 32));

  noStroke();
  textSize(fontSize);

  if (!objects.length) {
    textAlign(CENTER, CENTER);
    fill(0, 0, 100, 200);
    text('没有可跟踪的物体', width / 2, height / 2 - fontSize);
    fill(0, 0, 100, 110);
    text('选中空物体 → p5inBlender → Tracking → Track Selected', width / 2, height / 2 + fontSize);
    textAlign(LEFT, BASELINE);
    return;
  }

  let count = 0;
  for (const path of paths) count += path.particles.length;

  fill(0, 0, 100, 120);
  text(
    (trackerName || 'tracked') +
      '  ·  每 ' + SAMPLE_MS / 1000 + 's 采样' +
      '  ·  粒子 ' + count +
      '  ·  路径 ' + paths.length +
      '  ·  ' + (moving ? 'moving' : 'idle'),
    12,
    height - 12
  );
}
