# 贪吃蛇游戏 - DOM 契约与接口定义

## 目标
明确游戏各模块的接口边界，确保代码可测、可维护。

## 核心数据结构

### SnakeState
```typescript
interface SnakeState {
  body: Array<{x: number, y: number}>;  // 蛇身坐标数组，body[0] 为头部
  direction: 'UP' | 'DOWN' | 'LEFT' | 'RIGHT';
  nextDirection: 'UP' | 'DOWN' | 'LEFT' | 'RIGHT';  // 缓冲方向
}
```

### GameState
```typescript
type GameStatus = 'IDLE' | 'RUNNING' | 'PAUSED' | 'GAME_OVER';

interface GameState {
  snake: SnakeState;
  food: {x: number, y: number};
  score: number;
  highScore: number;
  status: GameStatus;
  level: number;
  speed: number;  // ms per tick
}
```

## 公共接口

### 初始化
```javascript
// 传入 canvas 元素和配置
init(canvas: HTMLCanvasElement, config?: GameConfig): void;

interface GameConfig {
  gridSize?: number;      // 格子大小，默认 20
  gridWidth?: number;     // 格子宽度，默认 40
  gridHeight?: number;    // 格子高度，默认 30
  initialSpeed?: number;  // 初始速度(ms)，默认 150
  speedIncrement?: number;// 每次加速(ms)，默认 5
  foodPerLevel?: number;  // 每关所需食物数，默认 5
}
```

### 控制接口
```javascript
// 键盘事件处理
handleKey(event: KeyboardEvent): void;

// 触屏事件处理
handleTouchStart(event: TouchEvent): void;
handleTouchMove(event: TouchEvent): void;
handleTouchEnd(event: TouchEvent): void;

// 状态控制
startGame(): void;
pauseGame(): void;
resumeGame(): void;
restartGame(): void;
```

### 游戏循环
```javascript
// 每帧调用（requestAnimationFrame）
update(deltaTime: number): void;
render(ctx: CanvasRenderingContext2D): void;
```

## 内部模块划分

### 1. InputManager
- 职责：键盘/触摸输入解析
- 接口：`handleKey()`, `handleTouch*()`
- 依赖：无

### 2. Snake
- 职责：蛇身管理、移动、碰撞检测
- 接口：`move()`, `grow()`, `checkCollision()`, `hasSelfCollision()`
- 依赖：GameState

### 3. FoodManager
- 职责：食物生成、检测
- 接口：`generateFood(snakeBody: Array)`, `checkEat(snakeHead: {x,y})`
- 依赖：GameState

### 4. ScoreManager
- 职责：计分、最高分持久化
- 接口：`addScore(points: number)`, `updateHighScore()`, `getHighScore()`
- 依赖：localStorage

### 5. Renderer
- 职责：Canvas 绘制
- 接口：`render()`, `renderBackground()`, `renderSnake()`, `renderFood()`, `renderUI()`
- 依赖：GameState

### 6. GameEngine
- 职责：主循环、状态机、模块协调
- 接口：`init()`, `startGame()`, `pauseGame()`, `restartGame()`
- 依赖：以上所有模块

## 事件总线
```javascript
// 游戏事件
const EVENTS = {
  SCORE_CHANGE: 'score:change',
  LEVEL_UP: 'level:up',
  GAME_OVER: 'game:over',
  PAUSE_TOGGLE: 'pause:toggle'
};

// 使用 CustomEvent 或简单回调
```

## 测试契约

### 单元测试
- `Snake.move()` 方向正确
- `Snake.checkCollision()` 撞墙/自撞正确
- `FoodManager.generateFood()` 不与蛇身重叠
- `ScoreManager` localStorage 读写正确

### 集成测试
- 完整游戏流程：开始 → 吃食物 → 撞墙 → 结束 → 重开
- 暂停/继续功能
- 最高分持久化

### 手动测试用例
1. 蛇向右移动，按上键，再按下键 → 蛇应向上，不应自杀
2. 吃食物后蛇身应增长
3. 游戏结束后按空格 → 重新开始
4. 关闭浏览器再打开 → 最高分保留

## DOM 结构契约
```html
<!-- index.html 必须包含 -->
<canvas id="gameCanvas" width="800" height="600"></canvas>
<div id="scoreDisplay">Score: 0</div>
<div id="highScoreDisplay">High Score: 0</div>
<div id="levelDisplay">Level: 1</div>
<div id="statusOverlay">按任意键开始</div>
<button id="restartBtn">重新开始</button>
```

## 扩展点预留
1. **障碍物系统**：预留 `obstacles: Array<{x,y}>` 接口
2. **特殊食物**：预留 `foodTypes` 配置，支持不同得分/效果
3. **多人模式**：预留 `players: Array<Player>` 接口
4. **地图编辑**：预留 `loadMap(filename)` 接口
