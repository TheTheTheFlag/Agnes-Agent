/**
 * 贪吃蛇游戏 - 带指令队列缓冲机制
 * 
 * 核心设计：pendingMoves 队列
 * - 每次键盘输入存入队列
 * - 每帧游戏循环仅消费队首指令
 * - 防止快速连按时指令丢失
 * - 保证方向变更平滑
 */

const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
const scoreEl = document.getElementById('score');
const highScoreEl = document.getElementById('highScore');
const startBtn = document.getElementById('startBtn');
const pauseBtn = document.getElementById('pauseBtn');
const resetBtn = document.getElementById('resetBtn');

// 游戏配置
const GRID_SIZE = 20;
const CELL_SIZE = 20;
const CANVAS_SIZE = 400;
const GAME_SPEED = 150; // ms per frame

// 游戏状态
let snake = [];
let food = {};
let direction = 'right';
let nextDirection = 'right';
let score = 0;
let highScore = localStorage.getItem('snakeHighScore') || 0;
let gameOver = false;
let gameRunning = false;
let gameLoop = null;

// ====== 指令队列缓冲机制 ======
let pendingMoves = []; // 存储待处理的移动指令队列

// 方向映射
const DIRECTION_MAP = {
    ArrowUp: 'up', w: 'up', W: 'up',
    ArrowDown: 'down', s: 'down', S: 'down',
    ArrowLeft: 'left', a: 'left', A: 'left',
    ArrowRight: 'right', d: 'right', D: 'right'
};

// 相反方向检查
const OPPOSITE = {
    up: 'down', down: 'up', left: 'right', right: 'left'
};

// 更新最高分显示
highScoreEl.textContent = highScore;

/**
 * 初始化游戏
 */
function initGame() {
    snake = [
        { x: 5, y: 10 },
        { x: 4, y: 10 },
        { x: 3, y: 10 }
    ];
    direction = 'right';
    nextDirection = 'right';
    score = 0;
    gameOver = false;
    pendingMoves = []; // 清空指令队列
    scoreEl.textContent = score;
    spawnFood();
    draw();
}

/**
 * 生成食物
 */
function spawnFood() {
    let newFood;
    do {
        newFood = {
            x: Math.floor(Math.random() * (CANVAS_SIZE / CELL_SIZE)),
            y: Math.floor(Math.random() * (CANVAS_SIZE / CELL_SIZE))
        };
    } while (snake.some(seg => seg.x === newFood.x && seg.y === newFood.y));
    food = newFood;
}

/**
 * 处理键盘输入 - 存入指令队列
 * @param {string} key - 按键
 */
function handleInput(key) {
    const newDir = DIRECTION_MAP[key];
    if (!newDir) return;
    
    // 不允许直接反转方向（当前方向 vs 队首待处理方向）
    // 使用 direction（当前实际方向）而不是 nextDirection，确保不立即反转
    if (newDir !== OPPOSITE[direction]) {
        // 限制队列长度，避免过多指令堆积
        if (pendingMoves.length < 3) {
            pendingMoves.push(newDir);
        }
    }
}

/**
 * 获取下一个方向（从队列消费）
 * @returns {string} 下一个方向
 */
function getNextDirection() {
    if (pendingMoves.length > 0) {
        return pendingMoves.shift(); // 消费队首指令
    }
    return direction; // 无待处理指令，保持当前方向
}

/**
 * 更新游戏状态
 */
function update() {
    if (gameOver || !gameRunning) return;
    
    // 从队列获取下一个方向
    direction = getNextDirection();
    
    // 计算新蛇头位置
    const head = { ...snake[0] };
    switch (direction) {
        case 'up': head.y--; break;
        case 'down': head.y++; break;
        case 'left': head.x--; break;
        case 'right': head.x++; break;
    }
    
    // 检查碰撞
    if (head.x < 0 || head.x >= CANVAS_SIZE / CELL_SIZE ||
        head.y < 0 || head.y >= CANVAS_SIZE / CELL_SIZE ||
        snake.some(seg => seg.x === head.x && seg.y === head.y)) {
        gameOver = true;
        updateHighScore();
        draw();
        return;
    }
    
    // 移动蛇
    snake.unshift(head);
    
    // 检查是否吃到食物
    if (head.x === food.x && head.y === food.y) {
        score += 10;
        scoreEl.textContent = score;
        spawnFood();
    } else {
        snake.pop();
    }
    
    draw();
}

/**
 * 绘制游戏画面
 */
function draw() {
    // 清空画布
    ctx.fillStyle = '#0f0f23';
    ctx.fillRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);
    
    // 绘制网格线
    ctx.strokeStyle = '#1a1a3e';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= CANVAS_SIZE; i += CELL_SIZE) {
        ctx.beginPath();
        ctx.moveTo(i, 0);
        ctx.lineTo(i, CANVAS_SIZE);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(0, i);
        ctx.lineTo(CANVAS_SIZE, i);
        ctx.stroke();
    }
    
    // 绘制食物
    ctx.fillStyle = '#ff6b6b';
    ctx.beginPath();
    ctx.arc(
        food.x * CELL_SIZE + CELL_SIZE / 2,
        food.y * CELL_SIZE + CELL_SIZE / 2,
        CELL_SIZE / 2 - 2,
        0,
        Math.PI * 2
    );
    ctx.fill();
    
    // 绘制蛇
    snake.forEach((seg, index) => {
        const gradient = ctx.createRadialGradient(
            seg.x * CELL_SIZE + CELL_SIZE / 2,
            seg.y * CELL_SIZE + CELL_SIZE / 2,
            2,
            seg.x * CELL_SIZE + CELL_SIZE / 2,
            seg.y * CELL_SIZE + CELL_SIZE / 2,
            CELL_SIZE / 2
        );
        gradient.addColorStop(0, index === 0 ? '#7bed9f' : '#4ecca3');
        gradient.addColorStop(1, index === 0 ? '#4ecca3' : '#2d8a6e');
        ctx.fillStyle = gradient;
        ctx.fillRect(
            seg.x * CELL_SIZE + 1,
            seg.y * CELL_SIZE + 1,
            CELL_SIZE - 2,
            CELL_SIZE - 2
        );
    });
    
    // 绘制游戏结束提示
    if (gameOver) {
        ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
        ctx.fillRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);
        
        ctx.fillStyle = '#ff6b6b';
        ctx.font = 'bold 48px Arial';
        ctx.textAlign = 'center';
        ctx.fillText('GAME OVER', CANVAS_SIZE / 2, CANVAS_SIZE / 2 - 20);
        
        ctx.fillStyle = '#fff';
        ctx.font = '24px Arial';
        ctx.fillText(`得分: ${score}`, CANVAS_SIZE / 2, CANVAS_SIZE / 2 + 30);
    }
    
    // 绘制队列长度指示（调试用，实际可隐藏）
    if (pendingMoves.length > 0) {
        ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
        ctx.font = '12px Arial';
        ctx.textAlign = 'right';
        ctx.fillText(`队列: ${pendingMoves.length}`, CANVAS_SIZE - 10, CANVAS_SIZE - 10);
    }
}

/**
 * 更新最高分
 */
function updateHighScore() {
    if (score > highScore) {
        highScore = score;
        highScoreEl.textContent = highScore;
        localStorage.setItem('snakeHighScore', highScore);
    }
}

/**
 * 开始游戏
 */
function startGame() {
    if (gameRunning) return;
    initGame();
    gameRunning = true;
    gameLoop = setInterval(update, GAME_SPEED);
    startBtn.disabled = true;
    pauseBtn.textContent = '暂停';
}

/**
 * 暂停/继续游戏
 */
function togglePause() {
    if (!gameRunning) return;
    gameRunning = !gameRunning;
    if (gameRunning) {
        gameLoop = setInterval(update, GAME_SPEED);
        pauseBtn.textContent = '暂停';
    } else {
        clearInterval(gameLoop);
        pauseBtn.textContent = '继续';
    }
}

/**
 * 重新开始
 */
function resetGame() {
    clearInterval(gameLoop);
    gameRunning = false;
    gameLoop = null;
    startBtn.disabled = false;
    pauseBtn.textContent = '暂停';
    initGame();
}

// 事件监听
document.addEventListener('keydown', (e) => {
    if (e.key === ' ') {
        e.preventDefault();
        togglePause();
        return;
    }
    
    if (gameRunning || gameOver) {
        e.preventDefault();
        handleInput(e.key);
    }
});

startBtn.addEventListener('click', startGame);
pauseBtn.addEventListener('click', togglePause);
resetBtn.addEventListener('click', resetGame);

// 初始绘制
initGame();
