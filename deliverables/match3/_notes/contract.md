# Match-3 接口侦查 + 契约固化（节点 a1）

> 本节点**只读不写业务代码**。以下内容全部来自对已完成节点产物的实际读取：
> - `deliverables/match3/index.html`（53 行，已读取）
> - `deliverables/match3/style.css`（381 行，已读取）
> - `deliverables/match3/game.js` —— 侦查时**不存在**（glob `deliverables/match3/**/*` 仅返回 index.html 与 style.css）。
>   a1 节点随后落盘了一个**纯接口骨架** `game.js`（常量 + 函数名占位，无任何游戏逻辑），仅为消除 HTML 中 `./game.js` 的 404 并把全局名钉死在代码层。
>   **后续实现节点应整体替换该文件，不要追加同名函数**（避免重复声明）。

---

## (a) #board 初始状态结论 ★关键

**#board 是空容器，初始不含任何单元格。**

```html
35|    <main class="board-wrap">
36|      <div id="board" class="board" aria-label="游戏棋盘" role="grid"></div>
37|      <div id="overlay" class="overlay hidden">
```

无 `<div class="cell">` 子元素，也没有内联 style。

### 由此确定的实现策略

| 项 | 结论 |
|---|---|
| **renderBoard 策略** | **必须生成（generate）64 个单元格**：每次渲染清空 `#board.innerHTML` 后创建 8×8 = 64 个 `.cell` 元素并 `appendChild`（行优先 row-major，index = r*8+c）。**不是复用现有单元格**（DOM 里没有可复用的）。 |
| 布局由谁负责 | **CSS 已定义** `.board { display:grid; grid-template-columns: repeat(8,1fr); grid-template-rows: repeat(8,1fr); gap: var(--cell-gap); aspect-ratio:1/1 }`。JS **不要**写内联 grid 样式，也不要手算像素定位；只需按顺序 append 64 个 `.cell`。 |
| 单元格尺寸 | CSS 中 `.cell { aspect-ratio: 1/1 }`，JS 不需要设置宽高。 |
| 增量渲染建议 | 为性能与动画（`.falling` / `.dropping` 过渡），推荐"首次建 64 个固定 `.cell` 骨架 + 只更新其内部 `.gem`";等价地也可每次重建 64 个 cell——两者都能满足契约，只要**每次渲染后 #board.childElementCount === 64**。 |
| 索引方式 | `cell.dataset.index = r*8+c`（建议），并在 `.cell` 上用 `dataset.row` / `dataset.col` 便于 `isAdjacent` 计算。 |

---

## (b) DOM 契约（id / class 清单）

### 必须存在的 id（全部已在 index.html 中确认）

| id | 行 | 初始内容 | 语义 / 用法 |
|---|---|---|---|
| `#board` | 36 | **空** | 棋盘容器，8×8 由 CSS Grid 定义；JS 填充 64 个 `.cell` |
| `#score` | 16 | `0` | 当前分数，`textContent` 直接写数字 |
| `#level` | 20 | `1` | 当前关卡 |
| `#moves` | 24 | `30` | 剩余步数（初始 30） |
| `#target` | 31 | `消除 3 个或以上相同宝石` | 目标提示文案（关卡目标描述） |
| `#overlay` | 36 | class 含 `hidden` | 结算遮罩；**通过 toggle `hidden` 类显示/隐藏** |
| `#finalScore` | 39 | `0` | 遮罩内最终得分 |
| `#overlayRestartBtn` | 40 | — | 遮罩内"再来一局"按钮 |
| `#restartBtn` | 46 | — | 底部"开始 / 重新开始"按钮 |

> 注意：任务描述里列出的 `#restartBtn` 与 `#overlay` 之外，实际还有一个 `#finalScore` 和 `#overlayRestartBtn`，**两者也必须被 script 使用**（否则遮罩内分数不更新、重开按钮失效）。

### 必须使用的 class 名（CSS 已实现对应样式，JS 只负责增删）

| class | 作用 | 由谁添加/移除 |
|---|---|---|
| `.cell` | 单元格基类（必须）；`.cell` 是点击事件的委托目标 | renderBoard 生成时 |
| `.gem` + `.gem-0` ~ `.gem-5` | 宝石视觉，**共 6 种类型**（CSS 中 `.gem-0`…`.gem-5` 齐全） | renderBoard 生成时写入内层元素 |
| `.selected` | 选中高亮（`transform: scale(1.08)` + 金色描边） | JS 点击选中时 add/remove |
| `.clearing` | 消除动画 `clear-pop` 0.34s（含 `::before` 爆闪环），`pointer-events:none` | clearMatches 时 add，动画结束后 remove。**动画时长 340ms，需与 setInterval/transitionend 对齐** |
| `.falling` | 掉落过渡 `transform 0.26s` | applyGravity 位移时 |
| `.dropping` | 新宝石掉落动画 `drop-in` 0.30s | refillBoard 生成新宝石时 |
| `.swap` | 交换回弹 `swap-bounce` 0.2s | swapTiles 时 |
| `.shake` | 无效交换抖动 `shake` 0.24s | 无匹配交换回退时 |
| `.hint` | 可交换提示虚线框（可选功能） | JS 可选 |
| `.bump` | 数值跳动（`.stat-value.bump`，放大 + 金色） | 计分时对 `#score` add 后延时 remove |
| `.overlay.hidden` | 遮罩隐藏态（`opacity:0; visibility:hidden`） | JS toggle |
| `.btn` / `.btn-primary` | 按钮样式 | 已写在 HTML，JS 不需处理 |

**宝石类型常量**：`GEM_TYPES = 6`（`.gem-0` 至 `.gem-5`），共 6 种。棋盘尺寸 `ROWS = 8, COLS = 8`（由 CSS `repeat(8,1fr)` 固定，不可改为其它尺寸，否则视觉与逻辑不一致）。

### 已有可用钩子（选中/交互相关）

- `.cell` 已有 `cursor: pointer; touch-action: manipulation; user-select:none`，说明交互预期是 **click 委托**（也建议同时支持 touch 点击；不要依赖 hover）。
- 脚本引入方式：`<script src="./game.js" defer></script>`（第 51 行）→ **game.js 必须存在且为浏览器可直接加载的普通 script（非 module），defer 执行即 DOMContentLoaded 后可用，无需自行监听 DOMContentLoaded 即可安全 querySelector**。

---

## (c) 需要全局暴露的函数清单（11 个 + 计分/关卡）

以下函数需在 `game.js` 中以**全局函数**形式存在（普通 function 声明即自动挂到 window，供集成/测试节点调用）：

| # | 函数名 | 职责（约定签名） | 必须保证的语义 |
|---|---|---|---|
| 1 | `createBoard()` | 返回 `ROWS×COLS` 的二维数组，元素为 0..5 的宝石类型，且**初始无三连**（生成时避免开局即有 match） | 纯逻辑，返回数组，不碰 DOM |
| 2 | `initBoard()` | 初始化/重开一局：建棋盘、重置 score/level/moves、updateStats、renderBoard | 供 `#restartBtn` 与 `#overlayRestartBtn` 绑定 |
| 3 | `isAdjacent(a, b)` | 判断两个坐标（`{r,c}` 或 `[r,c]`，二者选一并固定）是否相邻（曼哈顿距离 = 1） | 纯逻辑，返回 boolean |
| 4 | `swapTiles(a, b)` | **原地交换**棋盘数组两格，返回是否成功 | 不校验合法性也可，但不得改变数组引用 |
| 5 | `findMatches()` | 扫描全盘，返回所有 3 连及以上（横/纵）的坐标集合 | 返回数组（或 Set），**去重** |
| 6 | `clearMatches(matches)` | 将匹配格置为 `null`/空值，加 `.clearing` 类播放动画 | 只标记空位，不负责下落 |
| 7 | `applyGravity()` | 现有宝石下落填补空位 | 返回是否发生移动（boolean），供循环判断 |
| 8 | `refillBoard()` | 顶部空位生成新随机宝石（可选加 `.dropping`） | 填满后棋盘无 null |
| 9 | `resolveCascades()` | **连锁循环**：find → clear → gravity → refill，直到无匹配；每次消除累加计分 | 必须等动画时序，异步（async/await 或 Promise） |
| 10 | `renderBoard()` | **生成 64 个 `.cell`** 并渲染到 `#board`（见 (a)）；更新 `#board.childElementCount === 64` | 唯一允许操作 `#board` 子节点的函数 |
| 11 | `hasValidMove()` | 检测是否还存在可产生消除的相邻交换（**不可破坏性地修改棋盘状态，或改后必须还原**） | 返回 boolean，用于死局判定 |

### 计分 / 关卡函数名（约定）

| 函数名 | 职责 |
|---|---|
| `updateStats()` | 把当前分数/关卡/步数刷新到 `#score` / `#level` / `#moves`（**DOM 写入的唯一出口**） |
| `addScore(points)` | 累加分数、触发 `#score` 的 `.bump` 动效（可选参数：连击倍数） |
| `countMatches(matches)` / `getScoreFor(matches)` | 由匹配数量与连锁层数计算本轮得分 |
| `handleMoveDone()`（或 `useMove()`） | 递减 `#moves`，检查关卡目标达成 / 步数耗尽 |
| `checkLevelComplete()` | 达标则 `level++`、更新 `#target` 文案、刷新目标 |
| `gameOver()` | 显示 `#overlay`（移除 `hidden`）、写入 `#finalScore` |
| `restartGame()` | 重置并重开（可由 `initBoard()` 承担） |

> 若后续节点实现了上述函数，**必须保持名称与语义不变**，以便其它节点/评审按名字调用。

---

## (d) 已存在的计分逻辑摘要

**当前仓库中不存在任何计分逻辑实现。**

- `game.js` **尚未创建**（glob 结果：目录下仅 `index.html`、`style.css`）。
- 与计分/关卡相关的**唯一现存信息是 HTML 中的初始静态值与 CSS 的视觉类**：

| 项 | 现存事实（来自 index.html / style.css） | 合并时必须保持 |
|---|---|---|
| 分数 | `#score` 初始 `0` | 数字字符串，无千分位也无单位；更新时直接 `textContent` |
| 分数动效 | `.stat-value.bump { transform:scale(1.22); color:#ffd479 }` | 加分时对 `#score` 短暂 add `bump` 类 |
| 关卡 | `#level` 初始 `1` | 整数递增 |
| 步数 | `#moves` 初始 `30` | 初始 30，向 0 递减；`#moves === 0` 且未达标即结束 |
| 目标文案 | `#target` 初始为 `消除 3 个或以上相同宝石` | 这是"消除 3 连"的基础玩法描述，可被关卡目标文案替换，但**不得留空** |
| 结束遮罩 | `#overlay` 初始带 `hidden`；`#finalScore` 初始 `0`；含 `#overlayRestartBtn` | 结束时 remove `hidden` 并写入最终分；重开须同时恢复 `hidden` |
| 宝石种类 | CSS 提供 `.gem-0` ~ `.gem-5`，共 **6 种** | 随机取值域必须是 `[0,5]`，否则宝石无渐变样式（透明） |
| 棋盘尺寸 | CSS `repeat(8,1fr)` × 8 行 | 逻辑尺寸固定 8×8，**不得改为其它值** |
| 动画时长（用于睡眠/对齐） | `clear-pop` 340ms、`drop-in` 300ms、`falling` 过渡 260ms、`shake` 240ms、`swap-bounce` 200ms、`overlay` 过渡 250ms | 级联 `await sleep(...)` 时按此取值，取整并用 `prefers-reduced-motion` 兜底（CSS 已有全局 0.01ms 降级） |

**合并守则（给后续节点）**：
1. 任何新计分实现都**不得**改变 `#score/#level/#moves/#target/#overlay/#finalScore` 的 id 与语义（详见 (b) 表）。
2. 所有 DOM 写入集中到 `updateStats()` / `renderBoard()` 两个出口，避免多处直接操作，便于合并冲突最小化。
3. 不得在 CSS 之外硬编码 8×8 或 6 种宝石的说法之外的值；`ROWS=8, COLS=8, GEM_TYPES=6` 作为常量声明在 game.js 顶部。
4. `game.js` 必须以普通脚本形式加载（HTML 用的是 `defer`，无 `type="module"`），因此**不能使用 `import/export` 语句**，否则页面会报错导致整体不可运行。

---

## (e) 集成验收清单（供后续节点自检）

- [ ] 页面加载后 `document.querySelectorAll('#board .cell').length === 64`
- [ ] `#board` 的 8 列布局来自 CSS，无 JS 内联 grid 样式
- [ ] 点击两个相邻 `.cell` → 产生 `.selected`；无匹配则回退并出现 `.shake`
- [ ] 有匹配时出现 `.clearing`，随后下落、补位、连锁
- [ ] `#score` 随消除增长并短暂带 `.bump`
- [ ] `#moves` 每次有效交换 -1；`#level` 达标后 +1；`#target` 文案随关卡更新
- [ ] 步数耗尽 → `#overlay` 去掉 `hidden`，`#finalScore` 显示最终分
- [ ] `#restartBtn` 与 `#overlayRestartBtn` 均能重开且 `#overlay` 重新加回 `hidden`
- [ ] 全局可达：`createBoard initBoard isAdjacent swapTiles findMatches clearMatches applyGravity refillBoard resolveCascades renderBoard hasValidMove`
