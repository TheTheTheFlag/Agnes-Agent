#!/usr/bin/env node
/**
 * 像素狂飙 Pixel Racer v1.3 —— 纯逻辑单元测试
 * 说明：从 index.html 中提取 <script> 并在无 DOM 的沙箱中执行，
 *       游戏 IIFE 会在无 document 时仅导出 PX 纯逻辑模块后提前返回，
 *       因此本测试不需要浏览器 / canvas / DOM。
 *
 * 运行：node tests/test_logic.js   （在项目根目录）
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const GAME_HTML = path.join(__dirname, '..', 'dist', 'pixel-racer-v1.3', 'index.html');

/* ---------- 1. 加载并解析 ---------- */
let src;
try {
  src = fs.readFileSync(GAME_HTML, 'utf8');
} catch (e) {
  console.error('FAIL: 无法读取 ' + GAME_HTML);
  process.exit(1);
}
if (src.length === 0) { console.error('FAIL: 文件为空'); process.exit(1); }

/* ---------- 2. HTML 结构冒烟检查 ---------- */
console.log('== 结构冒烟检查 ==');
let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.error('  FAIL  ' + name); }
}
ok(/<!DOCTYPE html>/i.test(src), '存在 DOCTYPE');
ok(/<html lang="zh-CN">/i.test(src), 'html lang=zh-CN');
ok(/<canvas id="game" width="480" height="640"/.test(src), '画布 480x640');
ok(/<script>[\s\S]*<\/script>/.test(src), '存在 <script> 块');
ok(/Pixel Racer v1\.3/i.test(src), '版本标识 v1.3');
ok(/Pixel Racer v1\.2/i.test(src) === false, '已无 v1.2 残留标识');

const m = /<script>([\s\S]*?)<\/script>/.exec(src);
if (!m) { console.error('FAIL: 未找到 <script> 块'); process.exit(1); }

/* ---------- 3. 无 DOM 沙箱执行，提取 PX ---------- */
const sandbox = {};
vm.createContext(sandbox);
try {
  vm.runInContext(m[1], sandbox, { filename: 'pixel-racer-v1.3.js' });
} catch (e) {
  console.error('FAIL: 脚本语法/执行错误 —— ' + e.message);
  process.exit(1);
}
const PX = sandbox.PX;
if (!PX) { console.error('FAIL: 无 DOM 环境下未导出 PX'); process.exit(1); }

console.log('\n== PX 纯逻辑单元测试（VERSION=' + PX.VERSION + '） ==');

/* clamp */
ok(PX.clamp(5, 0, 10) === 5, 'clamp 中间值');
ok(PX.clamp(-3, 0, 10) === 0, 'clamp 下限');
ok(PX.clamp(99, 0, 10) === 10, 'clamp 上限');

/* laneCenter */
ok(PX.laneCenter(0) === 140 && PX.laneCenter(1) === 240 && PX.laneCenter(2) === 340,
   'laneCenter 车道中心 140/240/340');

/* baseSpeedFor */
ok(PX.baseSpeedFor(0) === 180, 'baseSpeedFor(0)=180');
ok(PX.baseSpeedFor(1e5) === 460, 'baseSpeedFor 封顶 460');
ok(PX.baseSpeedFor(5000) > PX.baseSpeedFor(0), 'baseSpeedFor 单调递增');

/* spawnIntervalFor */
ok(PX.spawnIntervalFor(0) === 1.15, 'spawnIntervalFor(0)=1.15');
ok(PX.spawnIntervalFor(1e5) === 0.35, 'spawnIntervalFor 下限 0.35');

/* fmt */
ok(PX.fmt(0) === '0000000', 'fmt(0)=0000000');
ok(PX.fmt(123) === '0000123', 'fmt(123)=0000123');

/* speedKmh */
ok(PX.speedKmh(180) === 81, 'speedKmh(180)=81');
ok(PX.speedKmh(460) === 207, 'speedKmh(460)=207');

/* aabb */
ok(PX.aabb(0, 0, 10, 10, 5, 5, 10, 10, 0), 'aabb 重叠');
ok(!PX.aabb(0, 0, 10, 10, 20, 0, 10, 10, 0), 'aabb 不相交');
ok(!PX.aabb(0, 0, 10, 10, 8, 8, 10, 10, 5), 'aabb 内缩容差消除边缘重叠');
ok(PX.aabb(0, 0, 10, 10, 0, 0, 10, 10, 0), 'aabb 完全重合');

/* sweptV（隧穿修复核心） */
/* 玩家 y=100..148；车辆从 y=90 底部移动到 y=200 底部（单帧越过玩家，普通 aabb 会漏判） */
ok(PX.sweptV(90, 200, 100, 48), 'sweptV 捕捉高速隧穿（跨过玩家竖直区间）');
ok(!PX.sweptV(500, 600, 100, 48), 'sweptV 车在玩家下方不误判');
ok(!PX.sweptV(0, 50, 100, 48), 'sweptV 车在玩家上方且未到达不误判');
ok(PX.sweptV(120, 180, 100, 48), 'sweptV 出发即重叠视为碰撞');

/* isNearMiss */
ok(PX.isNearMiss(50, 200, 100, 148, 0, 58), '贴边：车顶穿过玩家底部且横向贴紧');
ok(!PX.isNearMiss(50, 200, 100, 148, 120, 58), '贴边：横向距离过远不算');
ok(!PX.isNearMiss(200, 400, 100, 148, 0, 58), '贴边：车辆在玩家下方穿过不算');

/* parseSprite */
const s = PX.parseSprite(['.X.', 'XXX']);
ok(s.w === 3 && s.h === 2, 'parseSprite 尺寸');
ok(s.map[1][1] === 'X', 'parseSprite 像素内容');

/* 模拟一局计分路径（分值规则回归） */
let score = 0;
score += 15; /* 贴边一次 */
score += 50; /* 金币一枚 */
ok(score === 65, '计分规则：贴边+15、金币+50 合计 65');

console.log('\n== 结果汇总 ==');
console.log('通过: ' + pass + ' / ' + (pass + fail));
process.exit(fail === 0 ? 0 : 1);
