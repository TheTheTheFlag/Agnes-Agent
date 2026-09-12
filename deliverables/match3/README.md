# 宝石消消乐 · Match-3（网页版消除游戏）

纯静态 HTML + CSS + 原生 JS 实现的三消游戏，**零依赖、零构建**，双击 `index.html` 即可开玩。

## 一、快速开始

```
双击打开 deliverables/match3/index.html
```

或用本地服务器（可选，体验更一致）：

```bash
cd deliverables/match3
python -m http.server 8080     # 然后访问 http://localhost:8080
```

> 直接 `file://` 打开也完全可用：没有 fetch、没有 ES module、没有外部 CDN。

## 二、玩法规则

1. 棋盘 **8 × 8**，共 **6 种宝石**。
2. 点击一个宝石选中（出现金色描边），再点击**相邻**（上下左右）宝石交换位置。
3. 交换后若形成**横向或纵向连续 3 个及以上**同类宝石即消除；否则自动回退并抖动提示。
4. 消除后上方宝石下落、顶部补入新宝石，若又形成新的三连则**连锁**继续消除（连锁层数越高倍率越高）。
5. 每次**有效交换消耗 1 步**；初始每关 30 步。
6. 步数耗尽仍未达标 → 弹出结算遮罩（显示最终分数），可点「再来一局」重开。

## 三、计分与关卡机制

| 项目 | 规则 |
|---|---|
| 基础分 | 每消除 1 颗宝石 **10 分** |
| 大消除奖励 | 单次消 4 颗 **+25**，消 5 颗及以上 **+60** |
| 连锁倍率 | 第 n 层连锁得分 ×n（连锁 2 连即双倍） |
| 单轮得分 | `(宝石数 × 10 + 奖励) × 连锁层数` |
| 关卡目标 | `目标分 = 200 × 关卡数`（第 1 关 200 分，第 2 关 400 分…） |
| 过关 | 分数 ≥ 目标分 → 关卡 +1、目标分递增、**步数重置为 30** |
| 失败 | 步数 = 0 且未达标 → 显示 `#overlay` 结算遮罩 |

界面实时同步：`#score`（分数，加分时跳动）、`#level`（关卡）、`#moves`（剩余步数）、`#target`（当前关目标文案）。

## 四、文件结构

```
deliverables/match3/
├── index.html                  # 页面结构（DOM 契约：见下）
├── style.css                   # 视觉样式、宝石渐变、各类动画
├── game.js                     # 全部游戏逻辑（普通脚本，无 import/export）
├── README.md                   # 本文件
├── _notes/
│   └── contract.md             # 接口侦查 + DOM/函数契约（设计与实现的约定）
└── _tests/
    ├── headless_test.js        # Node headless 行为测试（最小 DOM stub + vm）
    └── headless_result.txt     # 最近一次测试报告
```

### DOM 契约（改动 index.html / CSS 时需保持）

| id / class | 用途 |
|---|---|
| `#board` | 棋盘容器，8 列由 CSS Grid 定义；JS 生成 **64 个 `.cell`**（内含 `.gem.gem-N`） |
| `#score` `#level` `#moves` | 分数 / 关卡 / 剩余步数 |
| `#target` | 关卡目标文案 |
| `#overlay`（`.hidden` 控制显隐）`#finalScore` `#overlayRestartBtn` | 结算遮罩 |
| `#restartBtn` | 底部开始 / 重新开始 |
| `.selected` `.clearing` `.falling` `.dropping` `.swap` `.shake` `.bump` | 交互与动画类 |

### 全局函数（供评审 / 自动化测试调用）

`createBoard` `initBoard` `isAdjacent` `swapTiles` `findMatches` `clearMatches` `applyGravity`
`refillBoard` `resolveCascades` `renderBoard` `hasValidMove` `updateStats` `addScore`
`getScoreFor` `handleMoveDone` `checkLevelComplete` `gameOver` `restartGame`

## 五、自动化验证

```bash
node --check deliverables/match3/game.js             # 语法检查
node deliverables/match3/_tests/headless_test.js     # 行为测试（52 项断言）
```

测试用最小 DOM stub 在 Node 中真实加载 `game.js`，覆盖：初始棋盘无三连且必有解、三/四连识别与去重、
消除→下落→补位（空位只浮在列顶）、无效交换回退、连锁收敛与计分、步数/关卡/结算遮罩、双按钮重开绑定、
无 `import/export` 语法等。

**最近一次结果：52 项断言全部通过（详见 `_tests/headless_result.txt`）。**

## 六、验收清单对照

- [x] 加载后 `#board .cell` 共 64 个
- [x] 8 列布局来自 CSS，JS 不写内联布局样式
- [x] 相邻交换 → 无匹配自动回退并抖动
- [x] 有匹配 → 消除动画 → 下落 → 补位 → 连锁至稳定
- [x] 分数增长并触发跳动动效，`#moves` 每次有效交换 -1
- [x] 达标 `#level` +1、`#target` 文案更新、步数重置
- [x] 步数耗尽显示结算遮罩与最终分
- [x] 两个重开按钮均生效且遮罩重新隐藏

## 七、可选扩展方向

- 死局检测后的自动重排、可交换位置提示（`.hint`）
- 特殊宝石（四连生成直线消除、五连生成彩色炸弹）
- 音效 / 粒子特效、最高分本地存储（localStorage）
- 关卡时长模式（倒计时替代步数）
