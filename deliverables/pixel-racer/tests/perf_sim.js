#!/usr/bin/env node
/**
 * 像素狂飙 —— 渲染性能模拟对比（v1.2 vs v1.3）
 * 用计数型 Canvas 桩统计单帧绘制的 API 调用次数（fillRect / drawImage / fillText / 状态切换），
 * 量化离屏预渲染带来的绘制调用量下降幅度。
 *
 * 说明：桩环境提供 document/window/canvas 伪实现，真实走游戏主循环（rAF 回调逐帧驱动），
 *       通过派发 Space 键启动游戏并推进 ~240 帧（≈4 秒游戏进程），统计最后一帧的绘制调用。
 *
 * 运行：node tests/perf_sim.js   （在项目根目录）
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FILES = {
  'v1.2': path.join(__dirname, '..', 'dist', 'pixel-racer-v1.2', 'index.html'),
  'v1.3': path.join(__dirname, '..', 'dist', 'pixel-racer-v1.3', 'index.html')
};

/* ---------- 计数型 Canvas 桩环境 ---------- */
function makeSandbox(filePath) {
  const html = fs.readFileSync(filePath, 'utf8');
  const m = /<script>([\s\S]*?)<\/script>/.exec(html);
  if (!m) throw new Error('未找到 <script>: ' + filePath);

  let rafFn = null;
  let started = false;
  const counters = { calls: 0, fillRect: 0, drawImage: 0, fillText: 0, stateSet: 0 };
  const handlers = { keydown: [], keyup: [], blur: [], visibilitychange: [] };

  function resetCounters() { counters.calls = 0; counters.fillRect = 0; counters.drawImage = 0; counters.fillText = 0; counters.stateSet = 0; }

  function makeCtx() {
    const target = {};
    return new Proxy(target, {
      get(t, k) {
        if (k === '__counters') return counters;
        if (k === 'canvas') return { width: 480, height: 640 };
        return function () {
          counters.calls++;
          if (k === 'fillRect') counters.fillRect++;
          else if (k === 'drawImage') counters.drawImage++;
          else if (k === 'fillText') counters.fillText++;
        };
      },
      set(t, k, v) {
        if (k === 'fillStyle' || k === 'font' || k === 'globalAlpha' ||
            k === 'textAlign' || k === 'lineWidth' || k === 'strokeStyle' ||
            k === 'imageSmoothingEnabled') { counters.stateSet++; }
        t[k] = v; return true;
      }
    });
  }

  function makeCanvas() {
    return {
      width: 480, height: 640, style: {},
      getContext: function () { return makeCtx(); },
      addEventListener: function () {},
      setPointerCapture: function () {},
      getBoundingClientRect: function () { return { left: 0, top: 0, width: 480, height: 640 }; }
    };
  }

  const sandbox = {
    console, Math, JSON, parseInt, parseFloat, String, Number, Array, Object,
    Boolean, Date, Error, Promise, Set, Map, RegExp, isNaN,
    performance: { now: function () { return 16; } },
    localStorage: { getItem: function () { return null; }, setItem: function () {} },
    AudioContext: function () {},
    requestAnimationFrame: function (cb) { rafFn = cb; },
    document: {
      getElementById: function () { return makeCanvas(); },
      createElement: function (tag) {
        if (tag === 'canvas') return makeCanvas();
        return { style: {}, textContent: '', appendChild: function () {} };
      },
      addEventListener: function (type, fn) {
        (handlers[type] = handlers[type] || []).push(fn);
      },
      hidden: false
    },
    window: null
  };
  sandbox.window = {
    addEventListener: function (type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
    AudioContext: sandbox.AudioContext,
    requestAnimationFrame: sandbox.requestAnimationFrame,
    localStorage: sandbox.localStorage,
    performance: sandbox.performance,
    devicePixelRatio: 1
  };

  vm.createContext(sandbox);
  try {
    vm.runInContext(m[1], sandbox, { filename: path.basename(filePath) });
  } catch (e) {
    throw new Error('脚本执行失败: ' + filePath + ' → ' + e.message);
  }
  if (!rafFn) throw new Error('未捕获 rAF 回调: ' + filePath);

  return {
    rafFn, handlers, counters, resetCounters,
    dispatchKey(code, keyCode) {
      const ev = { code, keyCode, repeat: false, preventDefault() {} };
      (handlers.keydown || []).forEach(fn => fn(ev));
      if (started) { /* 简化：不单独处理 keyup */ }
    }
  };
}

/* ---------- 运行对比 ---------- */
const FRAMES = 240;   /* ~4 秒 @60fps */
const results = {};

for (const tag of Object.keys(FILES)) {
  const env = makeSandbox(FILES[tag]);

  /* 启动游戏（派发 Space） */
  env.dispatchKey('Space', 32);

  /* 推进 FRAMES 帧，让车流/金币/树自然生成 */
  for (let i = 1; i <= FRAMES; i++) {
    env.rafFn(i * 1000 / 60);
  }

  /* 重置计数，再渲染最后一帧，统计该帧绘制调用 */
  env.resetCounters();
  env.rafFn((FRAMES + 1) * 1000 / 60);

  results[tag] = {
    frames: FRAMES,
    calls: env.counters.calls,
    fillRect: env.counters.fillRect,
    drawImage: env.counters.drawImage,
    fillText: env.counters.fillText,
    stateSet: env.counters.stateSet
  };
}

/* ---------- 输出 ---------- */
console.log('== 渲染性能模拟对比（单帧绘制 API 调用次数，约 ' + FRAMES + ' 帧/4 秒游戏进程后统计） ==\n');
const pad = s => String(s).padEnd(10);
console.log(pad('版本') + pad('总调用') + pad('fillRect') + pad('drawImage') + pad('fillText') + pad('状态切换'));
for (const tag of Object.keys(results)) {
  const r = results[tag];
  console.log(pad(tag) + pad(r.calls) + pad(r.fillRect) + pad(r.drawImage) + pad(r.fillText) + pad(r.stateSet));
}

const v12 = results['v1.2'], v13 = results['v1.3'];
if (v12 && v13) {
  const dc = ((v12.calls - v13.calls) / v12.calls * 100).toFixed(1);
  const fr = ((v12.fillRect - v13.fillRect) / v12.fillRect * 100).toFixed(1);
  console.log('\n总绘制调用下降: ' + dc + '%   |   fillRect 调用下降: ' + fr + '%');
}

const THRESHOLD = 0.6; /* v1.3 总调用应不超过 v1.2 的 60% 才算达标 */
if (v13.calls <= v12.calls * THRESHOLD) {
  console.log('\n结论: PASS —— 性能优化达标（v1.3 单帧绘制调用 <= v1.2 的 60%）');
  process.exit(0);
} else {
  console.error('\n结论: FAIL —— 性能优化未达标');
  process.exit(1);
}
