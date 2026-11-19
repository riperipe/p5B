// p5.riso 套色试验 —— 全局模式（老 sketch 不用改就能跑）
// @mode global
// @lib https://cdn.jsdelivr.net/gh/antiboredom/p5.riso@master/lib/p5.riso.js
//
// 数据从 window.p5inblender 读：
//   p5inblender.state.geo[]      Blender 投影的线框
//   p5inblender.state.objects[]  被跟踪物体的屏幕坐标
//   p5inblender.texture          上游纹理（canvas 元素，可选）

let layers = null;
let screenBuf = null;

function setup() {
  const D = window.p5inblender || {};
  createCanvas(D.w || 640, D.h || 360);
  pixelDensity(1);
  layers = [new Riso('yellow'), new Riso('blue'), new Riso('fluorescentpink')];

  // 网点底纹只画一次，缓存起来（每帧重画 1600 个圆会掉到 8fps）
  screenBuf = createGraphics(width, height);
  screenBuf.noStroke();
  screenBuf.fill(255);
  for (let y = 8; y < height; y += 12) {
    for (let x = 8; x < width; x += 12) {
      screenBuf.ellipse(x, y, 6, 6);
    }
  }
}

function draw() {
  const D = window.p5inblender || {};
  const st = D.state || { objects: [], geo: [] };
  if (!layers) { background(250, 247, 240); return; }

  clearRiso();
  background(250, 247, 240);

  const pink = layers[2];
  const blue = layers[1];
  const yellow = layers[0];

  // 黄色网点底纹（贴缓存的图）
  if (screenBuf) {
    yellow.fill(255);
    yellow.image(screenBuf, 0, 0);
  }

  // 蓝色：Blender 投影过来的线框
  blue.fill(0);
  blue.stroke(255);
  blue.strokeWeight(1.1);
  (st.geo || []).forEach(function (g) {
    (g.edges || []).forEach(function (e) {
      const a = (g.verts || [])[e[0]];
      const b = (g.verts || [])[e[1]];
      if (!a || !b) return;
      blue.line(a[0] * width, a[1] * height, b[0] * width, b[1] * height);
    });
  });

  // 粉色：被跟踪物体，套印错位
  (st.objects || []).forEach(function (o) {
    if (o.visible === false) return;
    const x = o.x * width;
    const y = o.y * height;
    for (let k = 5; k >= 1; k--) {
      pink.fill(45 + k * 38);
      pink.ellipse(x + 3, y + 2, 24 * k, 24 * k);
    }
  });

  drawRiso();

  if ((window.p5inblender || {}).hud !== false) {
    noStroke();
    fill(40, 40, 46, 210);
    textSize(11);
    text('p5.riso  ·  frame ' + (st.frame || 0) +
         '  ·  tracked ' + ((st.objects || []).length) +
         '  ·  geo ' + ((st.geo || []).length) +
         '  ·  ' + width + 'x' + height, 12, height - 12);
  }
}
