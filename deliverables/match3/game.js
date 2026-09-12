/* =========================================================
   宝石消消乐 (Match-3) — 完整实现
   ---------------------------------------------------------
   · 普通脚本（非 module），不使用 import/export
   · DOM 契约见 _notes/contract.md：
       #board(生成 64 个 .cell，内含 .gem/.gem-N)、#score、#level、#moves、
       #target、#overlay(.hidden)、#finalScore、#restartBtn、#overlayRestartBtn
   · class 契约：.cell .gem .gem-0~.gem-5 .selected .clearing .falling
                 .dropping .swap .shake .bump .hidden
   ========================================================= */
'use strict';

/* ---------- 常量 ---------- */
var ROWS = 8;              // CSS: .board { grid-template-rows: repeat(8, 1fr) }
var COLS = 8;              // CSS: .board { grid-template-columns: repeat(8, 1fr) }
var GEM_TYPES = 6;         // CSS: .gem-0 ~ .gem-5
var START_MOVES = 30;      // index.html 中 #moves 初始值
var BASE_POINTS = 10;      // 每个宝石基础分
var TARGET_PER_LEVEL = 200; // 关卡目标分 = TARGET_PER_LEVEL * level
var CLEAR_MS = 340;        // .clearing 动画时长
var FALL_MS = 260;         // .falling 过渡时长
var MAX_CASCADE = 30;      // 安全阀：单次操作最大连锁层数

/* ---------- 状态 ---------- */
var board = [];            // ROWS x COLS 二维数组，元素 0..5 或 null（空位）
var score = 0;             // -> #score
var level = 1;             // -> #level
var moves = START_MOVES;   // -> #moves
var targetScore = TARGET_PER_LEVEL; // 本关目标分 -> #target 文案
var selected = null;       // {r, c} 当前选中格
var busy = false;          // 动画/结算期间锁定输入
var ended = false;         // 本局是否已结束
var cellEls = [];          // 缓存的 64 个 .cell 元素（index = r*COLS+c）
var gemEls = [];           // 缓存的 64 个 .gem 元素

/* ---------- 小工具 ---------- */
function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}
function randGem() {
  return Math.floor(Math.random() * GEM_TYPES);
}
function toCoord(p) {
  if (p && Array.isArray(p)) return { r: p[0], c: p[1] };
  return { r: p.r, c: p.c };
}
function toIndex(item) {
  if (typeof item === 'number') return item;
  if (Array.isArray(item)) return item[0] * COLS + item[1];
  if (typeof item === 'string') {
    var parts = item.split(',');
    return parseInt(parts[0], 10) * COLS + parseInt(parts[1], 10);
  }
  return item.r * COLS + item.c;
}
function setText(id, value) {
  var el = document.getElementById(id);
  if (el) el.textContent = String(value);
}
function popClass(pos, cls, ms) {
  var idx = toIndex(pos);
  var el = cellEls[idx];
  if (!el || !el.classList) return;
  el.classList.add(cls);
  setTimeout(function () { if (el.classList) el.classList.remove(cls); }, ms || 300);
}

/* =========================================================
   1) 棋盘生成 / 初始化
   ========================================================= */
function createBoard() {
  var b = [];
  for (var r = 0; r < ROWS; r++) {
    b[r] = [];
    for (var c = 0; c < COLS; c++) {
      var v = randGem();
      var guard = 0;
      // 生成时即避免出现横/纵三连
      while (guard < 60 && (
        (c >= 2 && b[r][c - 1] === v && b[r][c - 2] === v) ||
        (r >= 2 && b[r - 1][c] === v && b[r - 2][c] === v)
      )) {
        v = randGem();
        guard++;
      }
      b[r][c] = v;
    }
  }
  return b;
}

function initBoard() {
  var tries = 0;
  do {
    board = createBoard();
    tries++;
  } while (tries < 60 && (findMatches().length > 0 || !hasValidMove()));

  score = 0;
  level = 1;
  moves = START_MOVES;
  targetScore = TARGET_PER_LEVEL * level;
  selected = null;
  busy = false;
  ended = false;

  var ov = document.getElementById('overlay');
  if (ov && ov.classList) ov.classList.add('hidden');

  updateStats();
  renderBoard();
}

/* =========================================================
   2) 基础棋盘操作（纯逻辑）
   ========================================================= */
function isAdjacent(a, b) {
  var p = toCoord(a), q = toCoord(b);
  return Math.abs(p.r - q.r) + Math.abs(p.c - q.c) === 1;
}

function swapTiles(a, b) {
  var p = toCoord(a), q = toCoord(b);
  if (!board[p.r] || !board[q.r]) return false;
  var t = board[p.r][p.c];
  board[p.r][p.c] = board[q.r][q.c];
  board[q.r][q.c] = t;
  return true;
}

/* 返回所有横/纵 3 连及以上的格子（数字索引，去重升序） */
function findMatches() {
  var hit = Object.create(null);
  var r, c, k, v, run;

  for (r = 0; r < ROWS; r++) {
    c = 0;
    while (c < COLS) {
      v = board[r][c];
      run = 1;
      while (c + run < COLS && board[r][c + run] === v) run++;
      if (v !== null && run >= 3) {
        for (k = 0; k < run; k++) hit[r * COLS + (c + k)] = true;
      }
      c += run;
    }
  }
  for (c = 0; c < COLS; c++) {
    r = 0;
    while (r < ROWS) {
      v = board[r][c];
      run = 1;
      while (r + run < ROWS && board[r + run][c] === v) run++;
      if (v !== null && run >= 3) {
        for (k = 0; k < run; k++) hit[(r + k) * COLS + c] = true;
      }
      r += run;
    }
  }

  var out = [];
  for (var key in hit) out.push(Number(key));
  out.sort(function (x, y) { return x - y; });
  return out;
}

/* 置空 + 播放消除动画（不负责下落） */
function clearMatches(matches) {
  var list = matches || [];
  var count = 0;
  for (var i = 0; i < list.length; i++) {
    var idx = toIndex(list[i]);
    var r = Math.floor(idx / COLS), c = idx % COLS;
    if (r < 0 || r >= ROWS || c < 0 || c >= COLS) continue;
    if (board[r][c] === null) continue;
    board[r][c] = null;
    count++;
    var cell = cellEls[idx];
    if (cell && cell.classList) {
      cell.classList.add('clearing');
      setTimeout(function () {
        if (cell.classList) cell.classList.remove('clearing');
      }, CLEAR_MS);
    }
  }
  return count;
}

/* 竖直下落补空位，返回是否发生移动 */
function applyGravity() {
  var moved = false;
  for (var c = 0; c < COLS; c++) {
    var write = ROWS - 1;
    for (var r = ROWS - 1; r >= 0; r--) {
      if (board[r][c] !== null) {
        if (write !== r) {
          board[write][c] = board[r][c];
          board[r][c] = null;
          moved = true;
        }
        write--;
      }
    }
  }
  return moved;
}

/* 顶部空位补新宝石，返回补充数量 */
function refillBoard() {
  var filled = 0;
  for (var r = 0; r < ROWS; r++) {
    for (var c = 0; c < COLS; c++) {
      if (board[r][c] === null) {
        board[r][c] = randGem();
        filled++;
      }
    }
  }
  return filled;
}

/* 连锁：消除 → 下落 → 补位 → 再检测，直到稳定（异步，等动画时序） */
async function resolveCascades() {
  var chain = 0;
  var cleared = 0;
  while (chain < MAX_CASCADE) {
    var matches = findMatches();
    if (matches.length === 0) break;
    chain++;
    var points = getScoreFor(matches, chain);
    cleared += clearMatches(matches);
    addScore(points);
    await sleep(CLEAR_MS);
    applyGravity();
    refillBoard();
    renderBoard();
    await sleep(FALL_MS);
  }
  return { chain: chain, cleared: cleared };
}

/* 是否存在可产生消除的相邻交换（不破坏棋盘状态） */
function hasValidMove() {
  for (var r = 0; r < ROWS; r++) {
    for (var c = 0; c < COLS; c++) {
      if (c + 1 < COLS && trySwapHasMatch(r, c, r, c + 1)) return true;
      if (r + 1 < ROWS && trySwapHasMatch(r, c, r + 1, c)) return true;
    }
  }
  return false;
}

function trySwapHasMatch(r1, c1, r2, c2) {
  var t = board[r1][c1];
  board[r1][c1] = board[r2][c2];
  board[r2][c2] = t;
  var ok = findMatches().length > 0;
  t = board[r1][c1];
  board[r1][c1] = board[r2][c2];
  board[r2][c2] = t;
  return ok;
}

/* =========================================================
   3) 渲染（唯一操作 #board 子节点的出口）
   ========================================================= */
function renderBoard() {
  var el = document.getElementById('board');
  if (!el) return;

  el.innerHTML = '';
  cellEls = [];
  gemEls = [];

  for (var r = 0; r < ROWS; r++) {
    for (var c = 0; c < COLS; c++) {
      var idx = r * COLS + c;
      var v = board[r][c];

      var cell = document.createElement('div');
      cell.className = 'cell';
      if (cell.dataset) {
        cell.dataset.index = String(idx);
        cell.dataset.row = String(r);
        cell.dataset.col = String(c);
      }

      var gem = document.createElement('div');
      gem.className = (v === null || v === undefined) ? 'gem' : 'gem gem-' + v;

      cell.appendChild(gem);
      bindCell(cell, r, c);

      el.appendChild(cell);
      cellEls.push(cell);
      gemEls.push(gem);

      if (selected && selected.r === r && selected.c === c && cell.classList) {
        cell.classList.add('selected');
      }
    }
  }
}

function bindCell(cell, r, c) {
  if (!cell.addEventListener) return;
  cell.addEventListener('click', function () { handleCellClick(r, c); });
}

/* =========================================================
   4) 交互
   ========================================================= */
function setSelection(r, c) {
  clearSelection();
  selected = { r: r, c: c };
  var el = cellEls[r * COLS + c];
  if (el && el.classList) el.classList.add('selected');
}

function clearSelection() {
  if (selected) {
    var old = cellEls[selected.r * COLS + selected.c];
    if (old && old.classList) old.classList.remove('selected');
  }
  selected = null;
}

function handleCellClick(r, c) {
  if (busy || ended) return;
  if (selected && selected.r === r && selected.c === c) {
    clearSelection();
    return;
  }
  if (!selected) {
    setSelection(r, c);
    return;
  }
  if (!isAdjacent(selected, { r: r, c: c })) {
    setSelection(r, c);          // 非相邻：改选新格
    return;
  }
  var a = { r: selected.r, c: selected.c };
  var b = { r: r, c: c };
  clearSelection();
  attemptSwap(a, b);
}

async function attemptSwap(a, b) {
  if (busy || ended) return false;
  busy = true;

  var p = toCoord(a), q = toCoord(b);
  swapTiles(p, q);
  renderBoard();
  popClass(p, 'swap', 220);
  popClass(q, 'swap', 220);

  if (findMatches().length === 0) {
    // 无效交换：回退 + 抖动
    swapTiles(p, q);
    renderBoard();
    popClass(p, 'shake', 260);
    popClass(q, 'shake', 260);
    busy = false;
    return false;
  }

  await resolveCascades();
  handleMoveDone();
  busy = false;
  return true;
}

/* =========================================================
   5) 计分 / 关卡 / 结算
   ========================================================= */
function getScoreFor(matches, chain) {
  var n = (matches && matches.length) || 0;
  if (n === 0) return 0;
  var chainMul = Math.max(1, chain | 0);
  var base = n * BASE_POINTS;
  var bonus = n >= 5 ? 60 : (n >= 4 ? 25 : 0); // 一次消 4/5 个的额外奖励
  return Math.round((base + bonus) * chainMul);
}

function addScore(points) {
  score += points || 0;
  var el = document.getElementById('score');
  if (el) {
    el.textContent = String(score);
    if (el.classList) {
      el.classList.add('bump');
      setTimeout(function () { if (el.classList) el.classList.remove('bump'); }, 260);
    }
  }
}

function updateStats() {
  setText('score', score);
  setText('level', level);
  setText('moves', moves);
  var t = document.getElementById('target');
  if (t) {
    t.textContent = '第 ' + level + ' 关目标：' + targetScore + ' 分（消除 3 个及以上相同宝石）';
  }
}

function handleMoveDone() {
  moves = Math.max(0, moves - 1);
  updateStats();
  if (score >= targetScore) {
    checkLevelComplete();
    return;
  }
  if (moves <= 0) gameOver();
}

function checkLevelComplete() {
  level += 1;
  targetScore = TARGET_PER_LEVEL * level;
  moves = START_MOVES;
  updateStats();
  var t = document.getElementById('target');
  if (t && t.classList) {
    t.classList.add('bump');
    setTimeout(function () { if (t.classList) t.classList.remove('bump'); }, 400);
  }
  return true;
}

function gameOver() {
  ended = true;
  var ov = document.getElementById('overlay');
  if (ov && ov.classList) ov.classList.remove('hidden');
  setText('finalScore', score);
}

function restartGame() {
  ended = false;
  busy = false;
  clearSelection();
  initBoard();
}

/* =========================================================
   6) 启动
   ========================================================= */
function bindEvents() {
  var b1 = document.getElementById('restartBtn');
  if (b1 && b1.addEventListener) b1.addEventListener('click', function () { restartGame(); });
  var b2 = document.getElementById('overlayRestartBtn');
  if (b2 && b2.addEventListener) b2.addEventListener('click', function () { restartGame(); });
}

function bootstrap() {
  bindEvents();
  initBoard();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
  } else {
    bootstrap();
  }
}
