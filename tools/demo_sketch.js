// p5b_sketch.js —— p5inBlender demo
//
// 这一份同时演示三件事：
//   1. 被跟踪物体（Tracking → Track Selected）的屏幕坐标，画成带拖尾的光点
//   2. 模型线框（Tracking → Send Geometry），Blender 投影好直接画线
//   3. 上游纹理（Canvas → Texture / Feedback），当背景画，做自反馈
//
// 改完不用点 Run（Auto Run on Edit 开着时约 0.3 秒自动生效）。

let trails = {};

function sketch(p) {
  p.setup = function () {
    const d = p.p5inblender || {};
    p.createCanvas(d.w || 640, d.h || 360);
    p.frameRate(60);
    p.pixelDensity(1);
  };

  p.draw = function () {
    const D = p.p5inblender || {};
    const state = D.state || { objects: [], geo: [] };
    const objs = state.objects || [];
    const geo = state.geo || [];
    const tex = D.texture;

    // 1) 背景：有上游纹理就铺纹理（自反馈），否则用半透明覆盖做拖尾
    if (tex) {
      p.push();
      p.translate(p.width / 2, p.height / 2);
      p.rotate(0.0012 * (p.frameCount % 2000));
      p.scale(0.9975);
      p.drawingContext.globalAlpha = 0.96;
      p.drawingContext.drawImage(tex, -p.width / 2, -p.height / 2, p.width, p.height);
      p.drawingContext.globalAlpha = 1;
      p.pop();
    } else {
      p.noStroke();
      p.fill(6, 8, 14, 38);
      p.rect(0, 0, p.width, p.height);
    }

    // 2) 网格底纹（每帧重画，避免拖尾糊成一片）
    p.stroke(120, 200, 255, 12);
    p.strokeWeight(1);
    for (let x = 0; x < p.width; x += 48) p.line(x, 0, x, p.height);
    for (let y = 0; y < p.height; y += 48) p.line(0, y, p.width, y);

    // 3) 几何线框：Blender 已经投影到 0..1，这里只乘尺寸
    geo.forEach(function (g) {
      p.noFill();
      p.strokeWeight(1.2);
      p.stroke(0, 255, 190, 90);
      p.beginShape(p.LINES);
      (g.edges || []).forEach(function (e) {
        const va = (g.verts || [])[e[0]];
        const vb = (g.verts || [])[e[1]];
        if (!va || !vb) return;
        p.vertex(va[0] * p.width, va[1] * p.height);
        p.vertex(vb[0] * p.width, vb[1] * p.height);
      });
      p.endShape();

      p.noStroke();
      p.fill(0, 255, 210, 110);
      (g.verts || []).forEach(function (v) {
        p.circle(v[0] * p.width, v[1] * p.height, 2.5);
      });
    });

    // 4) 被跟踪物体：拖尾 + 脉冲光环 + 标签
    objs.forEach(function (o, i) {
      if (o.visible === false) return;
      const x = o.x * p.width;
      const y = o.y * p.height;
      const tr = trails[i] || (trails[i] = []);
      tr.push([x, y]);
      if (tr.length > 48) tr.shift();

      p.noFill();
      p.stroke(255, 170, 60, 70);
      p.strokeWeight(2);
      p.beginShape();
      for (let k = 0; k < tr.length; k++) p.vertex(tr[k][0], tr[k][1]);
      p.endShape();

      const pulse = 1 + 0.14 * Math.sin((D.t || 0) * 6 + i);
      p.stroke(255, 200, 90, 210);
      p.strokeWeight(1.5);
      p.circle(x, y, 34 * pulse);
      p.stroke(255, 200, 90, 80);
      p.circle(x, y, 58 * pulse);

      p.noStroke();
      p.fill(255, 255, 240);
      p.circle(x, y, 8);

      p.fill(255, 230, 200, 200);
      p.textSize(11);
      p.text(o.name, x + 22, y - 16);
    });

    // 5) HUD
    p.noStroke();
    p.fill(200, 230, 255, 190);
    p.textSize(11);
    p.text('frame ' + (state.frame || 0) +
           '   t=' + (D.t || 0).toFixed(2) + 's' +
           '   tracked ' + objs.length +
           '   geo ' + geo.length +
           '   ' + p.width + 'x' + p.height +
           (tex ? '   tex' : ''), 12, p.height - 12);
  };
}
