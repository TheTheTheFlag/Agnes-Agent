# 网页版格斗游戏 · 设计规格（DESIGN.md）

> 项目代号：**Web Fighter**　产物：`deliverables/fighting-game/`
> 本文档是下游**唯一接口依据**，所有签名/常量以本文为准；`engine.js` 已实现部分**不得改动签名**，AI/UI 按本文补全。

---

## 1. 技术约束

| 项 | 约束 |
| --- | --- |
| 形态 | 纯静态单页，无构建步骤；`file://` 双击 `index.html` 即可运行 |
| 脚本引入 | `index.html` 用**普通 `<script src>`**，**按序**：`js/engine.js` → `js/ai.js` → `js/main.js` |
| 禁用 | **禁用 ES 模块脚本**（即 script 的 type 属性不得取值 module）、**禁用任何外部资源外链**（不使用第三方分发域名/远程引用）、无网络请求、无外部字体/图片/音频 |
| 资源 | 图形全部 Canvas 程序绘制；音效可选（WebAudio 合成，失败静默降级） |
| 命名空间 | 全局挂载 `window.FG`，三模块互不 `import`，仅通过 `window.FG` 通信 |
| 渲染 | Canvas 2D；逻辑分辨率 **960 × 540**，CSS 等比缩放适配窗口 |
| 兼容 | Chrome / Edge / Firefox / Safari 近两年版本 |

`index.html` 需提供的 DOM 结构（**id 必须一致**，供 `main.js`/`UI` 绑定）：

| DOM id | 元素 | 用途 |
| --- | --- | --- |
| `stage` | `<canvas>` | 主画布，`width=960 height=540` |
| `hp1` / `hp2` | `<div>` | P1 / P2 血条填充宽度（0~100%） |
| `name1` / `name2` | `<div>` | P1 / P2 角色名 |
| `timer` | `<div>` | 中央倒计时数字 |
| `overlay` | `<div>` | 覆盖层：开始/Round/FIGHT/KO/胜负文字 |
| `btnStart` | `<button>` | 开始对战 |
| `btnRestart` | `<button>` | 重新开始 |
| `[data-char]` | 多个 `<button>` | 选人按钮，`data-char="<角色id>"` |

---

## 2. 全局命名空间与接口

```js
window.FG = { Engine, AI, UI };
```

### 2.1 FG.Engine（engine.js，已实现，签名冻结）

```js
FG.Engine.createFighter({ id, x, facing, stats })   // → fighter
FG.Engine.stepFighter(fighter, input, opponent, dt) // dt 固定 1/60，原地更新 fighter，无返回
FG.Engine.detectHit(a, b)                           // → { hit, damage, knockback, hitstun }
FG.Engine.drawFighter(ctx, fighter)                 // 依据 fighter 状态绘制，无返回
FG.Engine.MOVES                                     // 招式表，见 2.3
```

**fighter 字段表**（`createFighter` 产物，`stepFighter` 原地修改）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | string | 角色 id（`balanced` / `heavy` / `speed`） |
| `x` | number | 水平位置（角色脚底中心，px） |
| `y` | number | 纵向位置（脚底 y，px） |
| `vx` / `vy` | number | 速度（px/帧） |
| `hp` / `maxHp` | number | 当前 / 最大血量 |
| `state` | string | `idle`/`walk`/`jump`/`attack`/`hitstun`/`blockstun`/`ko` |
| `stateTimer` | number | 当前状态已持续帧数 |
| `facing` | number | `1`=朝右，`-1`=朝左 |
| `hitbox` | object\|null | 当前生效判定框 `{x,y,w,h}`（相对脚底，y 向上为正） |
| `hitstun` | number | 剩余受击硬直帧 |
| `blocking` | boolean | 是否处于防御/格挡姿态 |
| `onGround` | boolean | 是否在地面 |
| `cooldowns` | object | 各招式冷却计数 `{ light, heavy, kick, special }` |
| `isAI` | boolean | 是否由 AI 驱动（true 时输入来自 `FG.AI.decide`） |

### 2.2 输入对象（人机共用）

```js
// 每帧为每个 fighter 构造，人类与 AI 走同一路径
{ left, right, jump, crouch,          // 布尔：方向/下蹲
  light, heavy, kick, special,        // 布尔：攻击键（上升沿为 true）
  block }                             // 布尔：防御（S 长按）
```

### 2.3 FG.Engine.MOVES（招式数据结构，已实现）

`window.FG.Engine.MOVES` 为对象，键为招式名，每条**必须**含以下字段（**不得新增/改名**）：

```js
FG.Engine.MOVES[name] = {
  name,      // string  招式显示名
  startup,   // number  启动帧（帧）
  active,    // number  有效帧（帧）
  recovery,  // number  收招帧（帧）
  damage,    // number  基础伤害
  range,     // number  水平判定范围（px，相对角色中心，朝向为正）
  knockback, // number  命中击退（px/帧）
  hitstun,   // number  命中后对手硬直帧数
  input      // string  触发指令，如 'J' / 'K' / 'L' / 'U'
};
```

### 2.4 FG.AI（ai.js，本节点补全）

```js
FG.AI.decide(self, opponent, dt)   // → input 对象（见 2.2）
FG.AI.setDifficulty(level)         // level: 'easy' | 'normal' | 'hard'
FG.AI.reset()                      // 重置内部历史快照/冷却，用于开局
```

`decide` 每逻辑帧对 `self.isAI === true` 的 fighter 调用一次，返回与人类**完全相同的 input 对象**；内部维护延迟快照队列（见 §5）。

### 2.5 FG.UI（main.js 内实现）

```js
FG.UI.init(dom)                    // 绑定 stage/hp1/hp2/name1/name2/timer/overlay 等
FG.UI.setHealth(side, hp, maxHp)   // side: 1|2 → 写 hp1/hp2 宽度%
FG.UI.setName(side, name)          // → 写 name1/name2
FG.UI.setTimer(seconds)            // → 写 timer 文本
FG.UI.showOverlay(text, kind)      // kind: 'round'|'fight'|'ko'|'win'|'lose'; text 为空则隐藏
FG.UI.onSelect(cb)                 // 绑定 [data-char] 按钮与 btnStart
FG.UI.onRestart(cb)                // 绑定 btnRestart
```

---

## 3. 角色数值表（≥3 名）

| 属性 | 均衡型 `balanced`（隆） | 重击型 `heavy`（铁拳） | 速攻型 `speed`（春丽） |
| --- | --- | --- | --- |
| `hp`(maxHp) | 1000 | 1200 | 850 |
| 移速 moveSpeed (px/帧) | 3.2 | 2.4 | 4.2 |
| 跳跃初速 jump (px/帧) | -15.0 | -13.5 | -16.5 |
| 伤害系数 | 1.00 | 1.25 | 0.85 |
| 受击重量（越小越飘） | 1.00 | 1.30 | 0.80 |
| 专属必杀 | **波动拳** 发射前飞气弹 (special) | **铁山靠** 霸体突进 (special) | **百裂脚** 多段连踢 (special) |
| 定位差异 | 攻守均衡，招式帧数标准 | 慢而重，靠伤害与格挡反击 | 快而脆，起手快、连段强 |

**角色行一览（3 名，一角色一行）**：

| 角色行 | 角色 id | 显示名 | 定位 |
| --- | --- | --- | --- |
| 1 | `balanced` | 隆 | 均衡型 |
| 2 | `heavy` | 铁拳 | 重击型 |
| 3 | `speed` | 春丽 | 速攻型 |

差异实现要点：`stats` 传入 `createFighter`；伤害 = `move.damage × 伤害系数` 取整；`heavy` 受击击退 ×0.77，`speed` 受击僵直更长。

---

## 4. 键位映射表

P1 键盘（P2/AI 复用同一 input 接口，AI 输入由 `FG.AI.decide` 产生）：

| 动作 | input 字段 | P1 按键 |
| --- | --- | --- |
| 左移 | `left` | `A` |
| 右移 | `right` | `D` |
| 跳跃 | `jump` | `W` |
| 蹲下/防御 | `crouch` / `block` | `S`（长按=防御） |
| 轻拳 | `light` | `J` |
| 重拳 | `heavy` | `K` |
| 踢腿 | `kick` | `L` |
| 必杀 | `special` | `U` |

- **AI 输入来源**：AI 为 `self.isAI=true` 的 fighter，每逻辑帧调用 `FG.AI.decide(self, opponent, 1/60)` 返回 input，与 P1 完全同构。
- 边缘触发：攻击键 `light/heavy/kick/special` 仅在**按下那一帧**为 true。
- 键位一览（8 个键全部出现）：A / D / W / S / J / K / L / U —— 即 `A`=left、`D`=right、`W`=jump、`S`=crouch/block、`J`=light、`K`=heavy、`L`=kick、`U`=special。

---

## 5. 固定 60FPS 逻辑步长

主循环（`main.js`）：`requestAnimationFrame` + 累积器，逻辑固定 `dt = 1/60`。

```js
const STEP = 1 / 60;            // 固定逻辑步长（秒）
const MAX_FRAME = 0.25;         // 单帧最大补偿上限（秒），防卡顿后追帧雪崩
let acc = 0, last = performance.now();

function loop(now) {
  let frame = (now - last) / 1000;
  last = now;
  if (frame > MAX_FRAME) frame = MAX_FRAME; // 上限钳制
  acc += frame;
  while (acc >= STEP) {                      // 固定步推进
    update(STEP);                            // 输入 → AI → stepFighter → 碰撞 → 计时/胜负
    acc -= STEP;
  }
  render();                                  // Canvas 渲染与逻辑解耦
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);
```

- `dt` 恒为 `1/60`，任何刷新率下招式/物理帧数一致。
- `MAX_FRAME` 上限：切后台/卡顿后最多补 15 帧，避免“快进”。

---

## 6. 胜负规则

| 规则 | 判定 |
| --- | --- |
| KO | 任一 `hp ≤ 0` → 该角色判负，回合结束，胜方记 1 分 |
| 时间到 | 单回合 **99 秒**倒计时（`99 × 60 = 5940` 帧）归零：`hp` 高者胜；**相等则平局**（DRAW，双方不计分或重开该回合） |
| 赛制 | **三局两胜**：先得 **2 分**者赢得整场（`ROUNDS_TO_WIN = 2`） |
| 结束流程 | KO/时间到 → 显示 `KO!`/`TIME UP` → 记分（`overlay` 更新圆点）→ 未满 2 分则下一回合（`Round N` + `FIGHT!`，锁输入约 1.5s）；满 2 分 → `overlay` 显示 `YOU WIN!`/`YOU LOSE!`，点击 `btnRestart` 重置 |

关键常量：`ROUND_TIME_SECONDS = 99`、`ROUND_TIME_FRAMES = 5940`、`ROUNDS_TO_WIN = 2`。

---

## 7. 画布坐标系与尺寸

| 项 | 值 |
| --- | --- |
| 逻辑尺寸 | **960 × 540**（`stage.width=960 height=540`） |
| 原点 | 画布**左上角** `(0,0)`；x **向右**为正，y **向下**为正 |
| 地面线 `GROUND_Y` | **460**（角色脚底所在 y；脚底不得低于此） |
| 左/右边界 | 角色中心 x ∈ `[40, 920]` |
| 重力 `GRAVITY` | 0.9 px/帧² |
| 高度约定 | 判定框 `y` 相对脚底，**向上为正**（写码时换算 `screenY = y - GROUND_Y`） |

HUD 布局：血条 `hp1` 左上、`hp2` 右上（镜像）；`timer` 顶部居中；`name1/name2` 血条内；`overlay` 居中大字；底部为按键提示。

---

## 招式表（Markdown，≥4 招，含 damage 与 startup/active/recovery）

> damage 为基础值，实际伤害 = `damage × 角色伤害系数` 取整；range 为水平判定范围 px；均含 `startup/active/recovery` 三个帧数。

| 招式 | input | startup | active | recovery | damage | range | knockback | hitstun |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 轻拳 (Jab) | `J` | 4 | 3 | 6 | 40 | 50 | 4 | 10 |
| 重拳 (Fierce) | `K` | 10 | 4 | 16 | 110 | 62 | 9 | 16 |
| 踢腿 (Kick) | `L` | 8 | 5 | 14 | 85 | 70 | 7 | 14 |
| 必杀 (Special) | `U` | 12 | 6 | 22 | 130 | 78 | 6 | 18 |

（以上为均衡型示例；重击型全体 ×1.25、速攻型 ×0.85，帧数表由 `FG.Engine.MOVES` 提供，`FG.Engine.detectHit` 消费这些字段。）

---

## 附录：模块职责与初始化顺序

1. `engine.js` 先加载，挂载 `FG.Engine`（含 `MOVES`）。
2. `ai.js` 加载，挂载 `FG.AI`（依赖 `FG.Engine`，仅读 `MOVES`/fighter）。
3. `main.js` 加载：`FG.UI.init(dom)` → 选人（`onSelect`）→ `btnStart` 启动主循环 → 每帧 `update` 内对 `isAI` 方调用 `FG.AI.decide`。
