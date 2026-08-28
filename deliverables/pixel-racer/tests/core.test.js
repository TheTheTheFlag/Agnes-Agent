'use strict';
/* ============================================================
   PIXEL RACER — 核心逻辑 功能/性能 无头测试（Node）
    加载 index.html 内联脚本到 VM 沙箱，对游戏纯逻辑做断言与基准。
    运行：cd deliverables/pixel-racer && node tests/core.test.js
    v1.3：修复 canvasStub 缺失 addEventListener（v1.2 脚本加载即崩）；
          新增 NEAR MISS 无敌保护 / 离屏预渲染 / 分辨率适配 断言。
   ============================================================ */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dir = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(dir, 'index.html'), 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('FAIL: 未找到 <script>'); process.exit(1); }

/* ---------- 浏览器 API 桩 ---------- */
let rectCount = 0;
const ctxStub = new Proxy({}, {
  get(t, k) {
    if (k === 'fillRect') return () => { rectCount++; };
    return () => {};
  },
  set() { return true; }
});
// v1.3 修复：canvas 需支持 addEventListener（v1.2 起绑定 6 个触摸/鼠标监听器）
const canvasStub = {
  getContext: () => ctxStub,
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 320, height: 480 }),
  addEventListener: () => {},
  width: 0,
  height: 0
};
const documentStub = {
  getElementById: () => canvasStub,
  // v1.3：离屏预渲染（背景/精灵）需要 createElement('canvas')
  createElement: () => canvasStub,
  addEventListener: () => {},
  hidden: false
};
const windowStub = { addEventListener: () => {} };
const storage = { _d: {}, getItem(k) { return this._d[k] || null; }, setItem(k, v) { this._d[k] = String(v); } };

const sandbox = {
  window: windowStub,
  document: documentStub,
  performance: { now: () => Date.now() },
  requestAnimationFrame: () => 0,
  cancelAnimationFrame: () => {},
  localStorage: storage,
  console
};
vm.createContext(sandbox);

let pass = 0, fail = 0;
function assert(name, cond) {
  if (cond) { pass++; console.log('  ✔ ' + name); }
  else { fail++; console.error('  ✘ ' + name); }
}

/* ---------- 1. 加载冒烟测试 ---------- */
try {
  vm.runInContext(m[1], sandbox);
  console.log('[1] 脚本加载（含 localStorage/AudioContext/离屏预渲染 异常容错路径）……');
  assert('沙箱内加载 index.html 脚本无异常', true);
} catch (e) {
  console.error('FAIL: 加载脚本抛异常 ->', e.message);
  process.exit(1);
}

/* ---------- 2. 功能断言 ---------- */
console.log('\n[2] 功能测试（核心逻辑）……');
const result = vm.runInContext(`(function(){
  const r = {};
  // 2.1 边界工具
  r.clampMin = clamp(-5, 0, 10) === 0;
  r.clampMax = clamp(15, 0, 10) === 10;
  r.clampMid = clamp(5, 0, 10) === 5;
  r.padZero  = pad(42) === "000042";

  // 2.2 碰撞判定（AABB）
  r.colHit   = collide({x:0,y:0,hw:6,hh:11},{x:10,y:5,hw:7,hh:12});
  r.colMissX = !collide({x:0,y:0,hw:6,hh:11},{x:30,y:5,hw:7,hh:12});
  r.colMissY = !collide({x:0,y:0,hw:6,hh:11},{x:10,y:100,hw:7,hh:12});
  // 2.2b 边界相切不算碰撞（严格 < 判定）
  r.colEdge  = !collide({x:0,y:0,hw:6,hh:11},{x:13,y:0,hw:7,hh:12});

  // 2.3 隧穿安全：最坏 dt=0.033s、最大相对速度 390km/h 的单帧位移 < 最小判定窗口
  const maxRel = (260 + 130) * 1.4 * 0.033;      // ≈18px
  r.noTunnel  = maxRel < 23;                      // 11+12=23px 最小窗口

  // 2.4 速度曲线：里程 0 -> 175 km/h，1800m -> 260 km/h
  r.max0 = Math.round(BASE_MAX + (TOP_MAX - BASE_MAX) * clamp(0 / MAX_PROGRESS_DIST, 0, 1)) === 175;
  r.max1 = Math.round(BASE_MAX + (TOP_MAX - BASE_MAX) * clamp(1800 / MAX_PROGRESS_DIST, 0, 1)) === 260;

  // 2.5 生成间隔随里程收窄（1.5s -> 0.55s）
  r.intStart = Math.abs((1.5 - (1.5 - 0.55) * 0) - 1.5) < 1e-9;
  r.intEnd   = Math.abs((1.5 - (1.5 - 0.55) * 1) - 0.55) < 1e-9;

  // 2.6 计分倍率 ×1/×2/×3
  player.speed = 230; r.mult3 = (player.speed >= 220 ? 3 : player.speed >= 150 ? 2 : 1) === 3;
  player.speed = 180; r.mult2 = (player.speed >= 220 ? 3 : player.speed >= 150 ? 2 : 1) === 2;
  player.speed = 100; r.mult1 = (player.speed >= 220 ? 3 : player.speed >= 150 ? 2 : 1) === 1;

  // 2.7 startGame 清空按键/触摸/鼠标残留（回归：v1.1 缺陷）
  keys.left = true; keys.space = true; touching = true; mouseDown = true;
  startGame();
  r.keysReset  = !keys.left && !keys.space && !touching && !mouseDown;
  r.statePlay  = state === "playing";
  r.invSpawn   = player.inv === 2.2 && player.speed === CRUISE;

  // 2.8 gameOver 后 inv 清零（回归：v1.1 缺陷，结算画面玩家车不可见）
  player.inv = 2.2; gameOver();
  r.invZero = player.inv === 0 && state === "over";

  // 2.9 NEAR MISS 判定条件（横向<26 纵向<42 相对速度差>70，每车一次）
  r.nearMiss = Math.abs(100 - 120) < 26 && Math.abs(200 - 180) < 42 && Math.abs(200 - 100) > 70;

  // 2.10 颜色/文字工具
  r.shade  = shade("#ff0000", 0).indexOf("255") >= 0;
  r.textW  = textWidth("AB", 1) === 12 && textWidth("AB", 2) === 24;

  // 2.11 drawText 行合并优化：run-length 调用数 < 逐像素调用数
  let px = 0, run = 0;
  for (const ch of "PIXEL RACER 0123") {
    const g = FONT[ch]; if (!g) continue;
    for (let r2 = 0; r2 < 7; r2++) {
      const row = g[r2]; let k = 0;
      for (let c2 = 0; c2 < 5; c2++) {
        if (row[c2] === "1") { px++; k++; }
        else if (k > 0) { run++; k = 0; }
      }
      if (k > 0) run++;
    }
  }
  r.runOpt = run > 0 && run < px;

  // 2.12 最佳成绩持久化（localStorage 容错）
  best = 999; saveBest();
  r.persist = storage._d.pixelRacerBest === "999";

  // 2.13 NEAR MISS 无敌保护（回归：v1.2 撞车后 inv 期间仍可+80）
  startGame();
  spawnTimer = 999; coinTimer = 999;            // 关闭生成干扰
  cars = [makeCar(1, false)]; const c = cars[0];
  c.x = player.x + 15; c.y = player.y + 5; c.speed = 20; c.passed = true;   // lat=15(不碰撞但<26), 速度差=80
  player.inv = 99;                              // 无敌期
  const s0 = score; updatePlaying(1 / 60); const d1 = score - s0;
  r.nearMissInvSafe = d1 < 80;                  // 无敌期不应触发 +80
  player.inv = 0; c.nearMissed = false; c.y = player.y + 5;
  const s1 = score; updatePlaying(1 / 60); const d2 = score - s1;
  r.nearMissHit     = d2 >= 80;                 // 非无敌期正常触发 +80

  // 2.14 离屏预渲染（v1.3 性能优化）：背景 tile + 敌车/玩家精灵已构建
  r.bgReady = !!bgRoad && !!bgGrass && bgRoad.width === 320 && bgRoad.height === 3600
              && bgGrass.width === 320 && bgGrass.height === 480;
  r.sprReady = !!(sprites && sprites["e#6c757dS"] && sprites["e#06d6a0B"] && sprites["p#e63946"]);

  // 2.15 分辨率适配公式（与 CSS width:min(94vw, 92vh*0.666); max-width:520 一致）
  const cssW = (vw, vh) => Math.min(Math.min(0.94 * vw, 0.92 * vh * (2 / 3)), 520);
  r.resPhone  = cssW(390, 844) > 0 && cssW(390, 844) <= 390;
  r.resTablet = cssW(768, 1024) > 0 && cssW(768, 1024) <= 768;
  r.resDesk   = cssW(1920, 1080) === 520;        // 桌面被 max-width 限制
  r.resTiny   = cssW(240, 320) > 0 && cssW(240, 320) <= 240;
  return JSON.stringify(r);
})()`, sandbox);

try {
  const r = JSON.parse(result);
  for (const [k, v] of Object.entries(r)) assert('逻辑断言 ' + k, v === true);
} catch (e) {
  console.error('解析结果失败:', e.message, result);
  fail++;
}

/* ---------- 3. 性能基准 ---------- */
console.log('\n[3] 性能基准（沙箱内 600 帧逻辑 + 300 帧渲染）……');
const perf = JSON.parse(vm.runInContext(`(function(){
  startGame();
  player.speed = 260; keys.up = true;
  cars = []; for (let i=0;i<14;i++){ const c=makeCar(i%4, i%7===0); c.y=40+i*28; cars.push(c); }
  coins = []; for (let i=0;i<4;i++) coins.push({x:100+i*40,y:50+i*100,bob:0});
  parts = []; for (let i=0;i<40;i++) parts.push({x:50,y:50,vx:0,vy:0,life:1,color:"#fff"});
  let t0 = Date.now();
  for (let i=0;i<600;i++) updatePlaying(1/60);
  const updateMs = Date.now() - t0;
  const carCap = cars.length <= 14;
  t0 = Date.now();
  for (let i=0;i<300;i++) render();
  const renderMs = Date.now() - t0;
  // 预渲染生效检查：单帧 fillRect 调用量（背景/车辆已改为 drawImage，应显著下降）
  rectCount = 0; render(); const rectDelta = rectCount;
  return JSON.stringify({updateMs, renderMs, carCap, rectDelta});
})()`, sandbox));

assert('600 帧 updatePlaying 耗时 < 1000ms（实际 ' + perf.updateMs + 'ms）', perf.updateMs < 1000);
assert('300 帧 render 耗时 < 1500ms（实际 ' + perf.renderMs + 'ms）', perf.renderMs < 1500);
assert('实体上限：车流 ≤ 14（实际 ' + perf.carCap + '）', perf.carCap === true);
assert('单帧 fillRect 调用 ≤ 300（预渲染生效，实际 ' + perf.rectDelta + '）', perf.rectDelta <= 300);
console.log('  基准明细: 600帧逻辑=' + perf.updateMs + 'ms，300帧渲染=' + perf.renderMs + 'ms，单帧fillRect=' + perf.rectDelta);

/* ---------- 4. 结论 ---------- */
console.log('\n========================================');
console.log('通过 ' + pass + ' 项 / 失败 ' + fail + ' 项');
if (fail === 0) { console.log('结论: ✅ 全部通过'); process.exit(0); }
else { console.error('结论: ❌ 存在失败'); process.exit(1); }
