/* =========================================================
   Match-3 Headless 行为测试（节点 a5）
   用最小 DOM stub + vm 加载 deliverables/match3/game.js，
   真实运行断言核心玩法逻辑，结果写入 _tests/headless_result.txt
   运行： node deliverables/match3/_tests/headless_test.js
   ========================================================= */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const gamePath = path.resolve(__dirname, '..', 'game.js');
const outPath = path.resolve(__dirname, 'headless_result.txt');
const code = fs.readFileSync(gamePath, 'utf8');

/* ---------------- 最小 DOM stub ---------------- */
function makeEl(id) {
  const classes = new Set();
  const el = {
    id: id || '',
    tagName: 'div',
    children: [],
    dataset: {},
    style: {},
    listeners: {},
    textContent: '',
    className: '',
    _classes: classes,
    classList: {
      add: (...cs) => cs.forEach((c) => classes.add(c)),
      remove: (...cs) => cs.forEach((c) => classes.delete(c)),
      contains: (c) => classes.has(c),
      toggle: (c) => (classes.has(c) ? classes.delete(c) : classes.add(c)),
    },
    appendChild(child) { el.children.push(child); return child; },
    addEventListener(type, fn) {
      (el.listeners[type] = el.listeners[type] || []).push(fn);
    },
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return ''; },
    set() { el.children.length = 0; },
  });
  Object.defineProperty(el, 'childElementCount', {
    get() { return el.children.length; },
  });
  return el;
}

const IDS = ['board', 'score', 'level', 'moves', 'target', 'overlay',
  'finalScore', 'restartBtn', 'overlayRestartBtn'];
const registry = {};
IDS.forEach((id) => { registry[id] = makeEl(id); });
registry.overlay.classList.add('hidden');   // 还原 index.html 初始态
registry.moves.textContent = '30';

const documentStub = {
  readyState: 'complete',                   // defer 脚本执行时机
  getElementById: (id) => registry[id] || null,
  createElement: (tag) => { const e = makeEl(''); e.tagName = tag; return e; },
  addEventListener: () => {},
};

const sandbox = {
  document: documentStub,
  window: {},
  console,
  setTimeout: (fn) => { fn(); return 0; },  // 立即触发，跳过动画等待
  clearTimeout: () => {},
};
const ctx = vm.createContext(sandbox);

/* ---------------- 断言工具 ---------------- */
const lines = [];
let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; lines.push('PASS  ' + name); }
  else { fail++; lines.push('FAIL  ' + name + (extra ? '  >> ' + extra : '')); }
}
function noHolesOnlyOnTop(b) {
  // gravity 语义：空位应全部集中在各列顶部，任何非空宝石下方不得存在空位
  for (let c = 0; c < b[0].length; c++) {
    let seenValue = false;
    for (let r = 0; r < b.length; r++) {
      if (b[r][c] === null) {
        if (seenValue) return false;   // 值之下还有空洞 → 不符合重力规则
      } else {
        seenValue = true;
      }
    }
  }
  return true;
}
function countNulls(b) {
  let n = 0;
  for (const row of b) for (const v of row) if (v === null) n++;
  return n;
}
function makeBoard() {
  // (r + 2c) % 6：横/纵都不会出现 3 连
  const b = [];
  for (let r = 0; r < 8; r++) {
    b[r] = [];
    for (let c = 0; c < 8; c++) b[r][c] = (r + 2 * c) % 6;
  }
  return b;
}
function findBestMove() {
  const b = ctx.board;
  const n = b.length;
  const has = () => ctx.findMatches().length > 0;
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      for (const [dr, dc] of [[0, 1], [1, 0]]) {
        const r2 = r + dr, c2 = c + dc;
        if (r2 >= n || c2 >= n) continue;
        const t = b[r][c]; b[r][c] = b[r2][c2]; b[r2][c2] = t;
        const ok = has();
        const u = b[r][c]; b[r][c] = b[r2][c2]; b[r2][c2] = u;
        if (ok) return [{ r, c }, { r: r2, c: c2 }];
      }
    }
  }
  return null;
}
function findInvalidMove() {
  const b = ctx.board;
  const n = b.length;
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      for (const [dr, dc] of [[0, 1], [1, 0]]) {
        const r2 = r + dr, c2 = c + dc;
        if (r2 >= n || c2 >= n) continue;
        const t = b[r][c]; b[r][c] = b[r2][c2]; b[r2][c2] = t;
        const ok = ctx.findMatches().length > 0;
        const u = b[r][c]; b[r][c] = b[r2][c2]; b[r2][c2] = u;
        if (!ok) return [{ r, c }, { r: r2, c: c2 }];
      }
    }
  }
  return null;
}

/* ---------------- 主流程 ---------------- */
(async function main() {
  lines.push('=== Match-3 headless 测试 ===');
  lines.push('被测文件: ' + gamePath);
  lines.push('');

  // 加载脚本（defer 后即执行，bootstrap 会自动 initBoard）
  vm.runInContext(code, ctx, { filename: 'game.js' });

  // 1) 全局函数齐全
  const fns = ['createBoard', 'initBoard', 'isAdjacent', 'swapTiles', 'findMatches',
    'clearMatches', 'applyGravity', 'refillBoard', 'resolveCascades', 'renderBoard',
    'hasValidMove', 'updateStats', 'addScore', 'getScoreFor', 'handleMoveDone',
    'checkLevelComplete', 'gameOver', 'restartGame'];
  const missing = fns.filter((f) => typeof ctx[f] !== 'function');
  check('全局暴露全部契约函数（' + fns.length + ' 个）', missing.length === 0, '缺失: ' + missing.join(','));

  // 2) 页面加载后棋盘渲染
  check('bootstrap 后 #board 生成 64 个 .cell', registry.board.children.length === 64,
    '实际 ' + registry.board.children.length);
  check('每个 .cell 内含 1 个 .gem 子元素',
    registry.board.children.every((c) => c.children.length === 1));
  check('cell 使用 .cell 基类', registry.board.children.every((c) => c.className.indexOf('cell') === 0));
  check('cell 的 dataset.index 覆盖 0..63',
    registry.board.children.every((c, i) => c.dataset.index === String(i)));
  check('gem 使用 .gem-N 类（N∈0..5）',
    registry.board.children.every((c) => /^gem gem-[0-5]$/.test(c.children[0].className)));

  // 3) 初始棋盘合法性
  ctx.initBoard();
  check('棋盘为 8x8', ctx.board.length === 8 && ctx.board.every((r) => r.length === 8));
  check('初始棋盘无任何三连', ctx.findMatches().length === 0);
  check('初始棋盘存在可行操作 hasValidMove()', ctx.hasValidMove() === true);
  check('初始无空位', countNulls(ctx.board) === 0);

  // 4) 纯逻辑：isAdjacent / swapTiles / hasValidMove 无副作用
  check('isAdjacent 相邻为真', ctx.isAdjacent({ r: 0, c: 0 }, { r: 0, c: 1 }) === true);
  check('isAdjacent 斜向为假', ctx.isAdjacent({ r: 0, c: 0 }, { r: 1, c: 1 }) === false);
  check('isAdjacent 相隔为假', ctx.isAdjacent({ r: 0, c: 0 }, { r: 0, c: 2 }) === false);
  const v00 = ctx.board[0][0], v01 = ctx.board[0][1];
  ctx.swapTiles({ r: 0, c: 0 }, { r: 0, c: 1 });
  check('swapTiles 原地交换两格', ctx.board[0][0] === v01 && ctx.board[0][1] === v00);
  const snap0 = JSON.stringify(ctx.board);
  ctx.hasValidMove();
  check('hasValidMove 不破坏棋盘状态', JSON.stringify(ctx.board) === snap0);

  // 5) findMatches 识别手工构造的横/纵三连、四连去重
  ctx.board = makeBoard();
  ctx.board[2][1] = 3; ctx.board[2][2] = 3; ctx.board[2][3] = 3;
  let m = ctx.findMatches();
  check('findMatches 识别横三连',
    m.indexOf(2 * 8 + 1) >= 0 && m.indexOf(2 * 8 + 2) >= 0 && m.indexOf(2 * 8 + 3) >= 0,
    '匹配: ' + m.join(','));

  ctx.board = makeBoard();
  ctx.board[0][5] = 4; ctx.board[1][5] = 4; ctx.board[2][5] = 4;
  m = ctx.findMatches();
  check('findMatches 识别纵三连',
    m.indexOf(0 * 8 + 5) >= 0 && m.indexOf(1 * 8 + 5) >= 0 && m.indexOf(2 * 8 + 5) >= 0,
    '匹配: ' + m.join(','));

  ctx.board = makeBoard();
  ctx.board[4][0] = 2; ctx.board[4][1] = 2; ctx.board[4][2] = 2; ctx.board[4][3] = 2;
  m = ctx.findMatches();
  check('findMatches 结果去重（四连=4 格）',
    m.length === 4 && new Set(m).size === 4, '长度 ' + m.length + ' -> ' + m.join(','));

  // 6) clearMatches → applyGravity → refillBoard
  ctx.board = makeBoard();
  ctx.board[7][0] = 5; ctx.board[7][1] = 5; ctx.board[7][2] = 5;
  const hitIdx = ctx.findMatches();
  const cleared = ctx.clearMatches(hitIdx);
  check('clearMatches 置空数量正确（>=3）', cleared >= 3, '实际 ' + cleared);
  check('clearMatches 后出现空位', countNulls(ctx.board) >= 3);
  const moved = ctx.applyGravity();
  check('applyGravity 返回发生移动', moved === true);
  check('applyGravity 后空位只在各列顶部', noHolesOnlyOnTop(ctx.board),
    ctx.board.map((row) => row.map((v) => (v === null ? '.' : v)).join('')).join('|'));
  const filled = ctx.refillBoard();
  check('refillBoard 补齐空位且无 null', filled >= 3 && countNulls(ctx.board) === 0);
  check('补齐后仍为 8x8', ctx.board.length === 8 && ctx.board.every((r) => r.length === 8));

  // 7) 无效交换自动回退
  ctx.initBoard();
  const bad = findInvalidMove();
  check('存在可用的无效交换样例（前置条件）', !!bad);
  if (bad) {
    const before = JSON.stringify(ctx.board);
    const ret = await ctx.attemptSwap(bad[0], bad[1]);
    check('无效交换 attemptSwap 返回 false', ret === false);
    check('无效交换后棋盘完全复原', JSON.stringify(ctx.board) === before);
  }

  // 8) 有效交换 → 消除 → 连锁 → 计分 → 步数
  ctx.initBoard();
  const good = findBestMove();
  check('存在可行交换样例（前置条件）', !!good);
  if (good) {
    const movesBefore = ctx.moves;
    const ret2 = await ctx.attemptSwap(good[0], good[1]);
    check('有效交换 attemptSwap 返回 true', ret2 === true);
    check('连锁结束后棋盘无空位', countNulls(ctx.board) === 0);
    check('连锁结束后仍为 8x8', ctx.board.length === 8 && ctx.board.every((r) => r.length === 8));
    check('连锁结束后无残留三连（已收敛到稳定）', ctx.findMatches().length === 0);
    check('分数增加', ctx.score > 0, 'score=' + ctx.score);
    check('步数 -1', ctx.moves === movesBefore - 1, 'moves=' + ctx.moves);
    check('#score 文本已同步', registry.score.textContent === String(ctx.score));
    check('#moves 文本已同步', registry.moves.textContent === String(ctx.moves));
  }

  // 9) resolveCascades 独立调用（构造连击盘面）
  ctx.initBoard();
  ctx.board[3][3] = 1; ctx.board[3][4] = 1; ctx.board[3][5] = 1;
  const before2 = ctx.score;
  const res = await ctx.resolveCascades();
  check('resolveCascades 至少执行 1 层连锁', res.chain >= 1, JSON.stringify(res));
  check('resolveCascades 后棋盘无空位、无残留三连',
    countNulls(ctx.board) === 0 && ctx.findMatches().length === 0);
  check('resolveCascades 累加分数', ctx.score > before2, 'score=' + ctx.score);
  check('getScoreFor 连锁倍数生效',
    ctx.getScoreFor([0, 1, 2], 2) === ctx.getScoreFor([0, 1, 2], 1) * 2,
    ctx.getScoreFor([0, 1, 2], 1) + ' vs ' + ctx.getScoreFor([0, 1, 2], 2));

  // 10) 关卡达成
  ctx.initBoard();
  ctx.moves = 5; ctx.score = 50; ctx.level = 1; ctx.targetScore = 10; // 分数已超过本关目标 10
  ctx.updateStats();
  ctx.handleMoveDone();
  check('达标后 level +1', ctx.level === 2, 'level=' + ctx.level);
  check('目标分随关卡递增（200*level）', ctx.targetScore === 400, 'target=' + ctx.targetScore);
  check('过关后步数重置', ctx.moves === 30, 'moves=' + ctx.moves);
  check('#target 文案随关卡更新', /第 2 关目标：400/.test(registry.target.textContent),
    registry.target.textContent);

  // 11) 步数耗尽 → 结算遮罩
  ctx.initBoard();
  check('重开后 #overlay 带 hidden', registry.overlay._classes.has('hidden') === true);
  ctx.moves = 1; ctx.score = 77; ctx.targetScore = 999999; ctx.updateStats();
  ctx.handleMoveDone();
  check('步数耗尽移除 hidden（显示遮罩）', registry.overlay._classes.has('hidden') === false);
  check('#finalScore 写入最终分', registry.finalScore.textContent === '77',
    registry.finalScore.textContent);

  // 12) 重开按钮绑定
  const btns = [registry.restartBtn, registry.overlayRestartBtn];
  check('两个重开按钮均绑定 click',
    btns.every((b) => b.listeners.click && b.listeners.click.length === 1));
  btns[1].listeners.click[0]();
  check('遮罩内“再来一局”可重开（恢复 hidden + 分数归零）',
    registry.overlay._classes.has('hidden') === true && ctx.score === 0 && ctx.moves === 30,
    'hidden=' + registry.overlay._classes.has('hidden') + ' score=' + ctx.score + ' moves=' + ctx.moves);
  ctx.gameOver();
  btns[0].listeners.click[0]();
  check('底部“开始/重新开始”可重开',
    registry.overlay._classes.has('hidden') === true && ctx.ended === false);

  // 13) 静态语法/规范检查
  check('game.js 无 import/export（普通脚本）',
    !/^\s*(import|export)\s/m.test(code));
  check('game.js 顶部常量符合契约',
    /var ROWS = 8/.test(code) && /var COLS = 8/.test(code) &&
    /var GEM_TYPES = 6/.test(code) && /var START_MOVES = 30/.test(code));

  lines.push('');
  lines.push('----------------------------------------');
  lines.push('总计: ' + (pass + fail) + ' 项断言，通过 ' + pass + '，失败 ' + fail);
  lines.push(fail === 0 ? '结论: 全部通过 ✅' : '结论: 存在失败项 ❌');

  const report = lines.join('\n') + '\n';
  fs.writeFileSync(outPath, report, 'utf8');
  console.log(report);
  process.exit(fail === 0 ? 0 : 1);
})().catch((err) => {
  const msg = '测试脚本异常: ' + (err && err.stack ? err.stack : err);
  fs.writeFileSync(outPath, lines.join('\n') + '\n' + msg + '\n', 'utf8');
  console.error(msg);
  process.exit(2);
});
