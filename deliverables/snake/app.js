/**
 * 贪吃蛇游戏 - 使用基础类实现
 * 基于 game-classes.js 中的面向对象架构
 */

// ==================== 全局实例 ====================
let canvasManager;
let snake;
let foodSpawner;
let gameLoop = null;

// 游戏状态
let score = 0;
let highScore = localStorage.getItem('snakeHighScore') || 0;
let gameOver = false;
let gameRunning = false;

// ==================== DOM 元素 ====================
const scoreEl = document.getElementById('score');
const highScoreEl = document.getElementById('highScore');
const startBtn = document.getElementById('startBtn');
const pauseBtn = document.getElementById('pauseBtn');
const resetBtn = document.getElementById('resetBtn');

// 初始化最高分显示
highScoreEl.textContent = highScore;

// ==================== 游戏初始化 ====================
/**
 * 初始化游戏（使用基础类）
 */
function initGame() {
    // 初始化画布管理器
    if (!canvasManager) {
        canvasManager = new CanvasManager('gameCanvas');
    }

    // 初始化蛇（蛇头在中央偏左，初始长度3）
    const startX = Math.floor(GameConfig.COLS / 2);
    const startY = Math.floor(GameConfig.ROWS / 2);
    snake = new Snake(startX, startY, 3);

    // 初始化食物生成器
    foodSpawner = new FoodSpawner(snake);
    foodSpawner.spawn();

    // 重置游戏状态
    score = 0;
    gameOver = false;
    scoreEl.textContent = score;

    // 绘制初始画面
    render();
}

// ==================== 游戏循环控制 ====================
/**
 * 启动游戏循环（定时器驱动）
 * @param {number} speed - 帧间隔（毫秒），默认使用 GameConfig.GAME_SPEED
 */
function startGameLoop(speed = GameConfig.GAME_SPEED) {
    // 停止现有循环（防止重复）
    stopGameLoop();
    // 使用 setInterval 创建定时器驱动的游戏循环
    gameLoop = setInterval(update, speed);
}

/**
 * 停止游戏循环
 */
function stopGameLoop() {
    if (gameLoop) {
        clearInterval(gameLoop);
        gameLoop = null;
    }
}

/**
 * 蛇身移动逻辑 - 计算并执行蛇的下一帧移动
 * 包含：
 * 1. 计算下一帧蛇头位置
 * 2. 边界碰撞检测（撞墙）
 * 3. 自身碰撞检测（撞自己）
 * 4. 食物碰撞检测（吃食物）
 * 5. 更新蛇身（移动/增长）
 */
function moveSnake() {
    // 1. 获取下一帧蛇头位置
    const nextHead = snake.getNextHead();

    // 2. 边界碰撞检测
    if (nextHead.x < 0 || nextHead.x >= GameConfig.COLS ||
        nextHead.y < 0 || nextHead.y >= GameConfig.ROWS) {
        endGame();
        return;
    }

    // 3. 自身碰撞检测（排除蛇尾，因为移动后蛇尾会消失）
    const willCollideWithSelf = snake.segments
        .slice(0, -1)  // 排除最后一节（移动后会变成蛇尾）
        .some(seg => seg.equals(nextHead));
    if (willCollideWithSelf) {
        endGame();
        return;
    }

    // 4. 检查是否吃到食物
    const ateFood = nextHead.equals(foodSpawner.getPosition());

    // 5. 更新蛇身
    snake.move(nextHead, ateFood);

    // 处理吃到食物后的逻辑
    if (ateFood) {
        score += 10;
        scoreEl.textContent = score;
        foodSpawner.spawn();
    }
}

// ==================== 游戏核心逻辑 ====================
/**
 * 更新游戏状态（游戏循环的核心更新函数）
 * 被定时器驱动，每隔 GameConfig.GAME_SPEED 毫秒执行一次
 */
function update() {
    if (gameOver || !gameRunning) return;
    
    // 执行蛇身移动逻辑
    moveSnake();
    
    // 渲染绘制游戏画面
    render();
}

/**
 * 游戏结束处理
 */
function endGame() {
    gameOver = true;
    gameRunning = false;
    stopGameLoop();

    // 更新最高分
    if (score > highScore) {
        highScore = score;
        highScoreEl.textContent = highScore;
        localStorage.setItem('snakeHighScore', highScore);
    }

    // 渲染最终画面
    render();
}

// ==================== 渲染 ====================
/**
 * 渲染游戏画面
 */
function render() {
    // 清空并绘制背景
    canvasManager.clear();
    canvasManager.drawGrid();

    // 绘制食物
    canvasManager.drawFood(foodSpawner.getPosition());

    // 绘制蛇
    canvasManager.drawSnake(snake);

    // 如果游戏结束，绘制结束画面
    if (gameOver) {
        canvasManager.drawGameOver(score, highScore);
    } else {
        canvasManager.clearReplayButton();
    }
}

// ==================== 游戏控制 ====================
/**
 * 开始游戏
 */
function startGame() {
    if (gameRunning) return;
    initGame();
    gameRunning = true;
    gameLoop = setInterval(update, GameConfig.GAME_SPEED);
    startBtn.disabled = true;
    pauseBtn.textContent = '暂停';
}

/**
 * 暂停/继续游戏
 */
function togglePause() {
    if (!gameRunning && !gameOver) {
        startGame();
        return;
    }
    if (!gameRunning) return;

    gameRunning = !gameRunning;
    if (gameRunning) {
        gameLoop = setInterval(update, GameConfig.GAME_SPEED);
        pauseBtn.textContent = '暂停';
    } else {
        clearInterval(gameLoop);
        pauseBtn.textContent = '继续';
    }
}

/**
 * 重新开始游戏
 */
function resetGame() {
    if (gameLoop) {
        clearInterval(gameLoop);
        gameLoop = null;
    }
    gameRunning = false;
    gameOver = false;
    startBtn.disabled = false;
    pauseBtn.textContent = '暂停';
    initGame();
}

// ==================== 键盘监听与方向控制 ====================
/**
 * 方向键与WASD键映射表
 */
const KEY_MAP = {
    ArrowUp: 'up', w: 'up', W: 'up',
    ArrowDown: 'down', s: 'down', S: 'down',
    ArrowLeft: 'left', a: 'left', A: 'left',
    ArrowRight: 'right', d: 'right', D: 'right'
};

/**
 * 相反方向映射（用于防止180度掉头检测）
 */
const OPPOSITE_DIR = {
    up: 'down',
    down: 'up',
    left: 'right',
    right: 'left'
};

/**
 * 处理键盘输入，控制蛇头转向
 * 防止180度掉头：
 * 1. 不能直接反向（如向上时不能直接向下）
 * 2. 不能在队列中连续两次相反方向
 * 
 * @param {string} key - 按键值
 * @returns {boolean} 是否成功处理
 */
function handleInput(key) {
    const newDir = KEY_MAP[key];
    if (!newDir) return false;
    
    // 获取当前方向（队列非空时取队列末尾方向，否则取当前方向）
    const currentDir = snake.pendingMoves.length > 0 
        ? snake.pendingMoves[snake.pendingMoves.length - 1] 
        : snake.direction;
    
    // 防护1: 不能直接反向（up↔down, left↔right）
    if (newDir === OPPOSITE_DIR[currentDir]) {
        console.log(`[输入拒绝] 不能${currentDir}时直接${newDir}`);
        return false;
    }
    
    // 防护2: 与当前方向相同则忽略
    if (newDir === currentDir) {
        return false;
    }
    
    // 尝试将新方向加入移动队列
    const queued = snake.queueMove(newDir);
    if (queued) {
        console.log(`[方向变更] ${currentDir} → ${newDir}`);
    }
    return queued;
}

/**
 * 获取当前蛇的移动方向状态（用于UI显示）
 * @returns {string} 当前方向描述
 */
function getCurrentDirectionText() {
    const dir = snake.pendingMoves.length > 0 
        ? snake.pendingMoves[snake.pendingMoves.length - 1] 
        : snake.direction;
    const arrow = {
        up: '↑', down: '↓', left: '←', right: '→'
    };
    return arrow[dir] || '→';
}

// ==================== 事件绑定 ====================
// 键盘事件监听器
document.addEventListener('keydown', (e) => {
    // 空格键暂停/继续游戏
    if (e.key === ' ') {
        e.preventDefault();
        togglePause();
        return;
    }
    
    // 方向键/WASD控制蛇移动（仅在游戏运行时响应）
    if (gameRunning) {
        e.preventDefault();
        handleInput(e.key);
    }
});

// 按钮事件
startBtn.addEventListener('click', startGame);
pauseBtn.addEventListener('click', togglePause);
resetBtn.addEventListener('click', resetGame);

// ==================== 启动 ====================
// 初始化游戏并绘制初始画面
initGame();
