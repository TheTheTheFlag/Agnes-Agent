// 游戏配置
const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
const scoreElement = document.getElementById('score');
const highScoreElement = document.getElementById('high-score');
const startBtn = document.getElementById('startBtn');
const restartBtn = document.getElementById('restartBtn');

// 游戏参数
const gridSize = 20;
const tileCount = canvas.width / gridSize;

// 游戏状态
let snake = [];
let food = {};
let dx = 0;
let dy = 0;
let score = 0;
let highScore = localStorage.getItem('snakeHighScore') || 0;
let gameRunning = false;
let gameLoop;

// 初始化显示最高分
highScoreElement.textContent = highScore;

// 初始化游戏
function initGame() {
    // 初始化蛇（从中心开始）
    snake = [
        { x: 10, y: 10 },
        { x: 9, y: 10 },
        { x: 8, y: 10 }
    ];
    
    // 初始方向向右
    dx = 1;
    dy = 0;
    
    // 初始化分数
    score = 0;
    scoreElement.textContent = score;
    
    // 生成第一个食物
    generateFood();
    
    // 绘制初始状态
    draw();
}

// 生成食物
function generateFood() {
    // 随机生成食物位置，确保不在蛇身上
    do {
        food = {
            x: Math.floor(Math.random() * tileCount),
            y: Math.floor(Math.random() * tileCount)
        };
    } while (isSnakeAt(food.x, food.y));
}

// 检查指定位置是否在蛇身上
function isSnakeAt(x, y) {
    return snake.some(segment => segment.x === x && segment.y === y);
}

// 游戏主循环
function gameUpdate() {
    if (!gameRunning) return;
    
    // 移动蛇头
    const head = { x: snake[0].x + dx, y: snake[0].y + dy };
    
    // 碰撞检测 - 撞墙
    if (head.x < 0 || head.x >= tileCount || head.y < 0 || head.y >= tileCount) {
        gameOver();
        return;
    }
    
    // 碰撞检测 - 撞自己
    if (isSnakeAt(head.x, head.y)) {
        gameOver();
        return;
    }
    
    // 将新头加入蛇身
    snake.unshift(head);
    
    // 检查是否吃到食物
    if (head.x === food.x && head.y === food.y) {
        score += 10;
        scoreElement.textContent = score;
        
        // 更新最高分
        if (score > highScore) {
            highScore = score;
            highScoreElement.textContent = highScore;
            localStorage.setItem('snakeHighScore', highScore);
        }
        
        // 生成新食物
        generateFood();
    } else {
        // 没有吃到食物，移除蛇尾
        snake.pop();
    }
    
    // 绘制游戏画面
    draw();
}

// 绘制游戏
function draw() {
    // 清空画布
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    
    // 绘制网格线
    ctx.strokeStyle = '#2a2a3e';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= tileCount; i++) {
        ctx.beginPath();
        ctx.moveTo(i * gridSize, 0);
        ctx.lineTo(i * gridSize, canvas.height);
        ctx.stroke();
        
        ctx.beginPath();
        ctx.moveTo(0, i * gridSize);
        ctx.lineTo(canvas.width, i * gridSize);
        ctx.stroke();
    }
    
    // 绘制食物
    ctx.fillStyle = '#ff6b6b';
    ctx.shadowBlur = 10;
    ctx.shadowColor = '#ff6b6b';
    ctx.beginPath();
    ctx.arc(
        food.x * gridSize + gridSize / 2,
        food.y * gridSize + gridSize / 2,
        gridSize / 2 - 2,
        0,
        Math.PI * 2
    );
    ctx.fill();
    ctx.shadowBlur = 0;
    
    // 绘制蛇
    snake.forEach((segment, index) => {
        // 蛇头用不同颜色
        if (index === 0) {
            ctx.fillStyle = '#667eea';
            ctx.shadowBlur = 10;
            ctx.shadowColor = '#667eea';
        } else {
            // 蛇身渐变色
            const gradient = 0.7 - (index / snake.length) * 0.3;
            ctx.fillStyle = `rgba(102, 126, 234, ${gradient})`;
            ctx.shadowBlur = 0;
        }
        
        ctx.fillRect(
            segment.x * gridSize + 1,
            segment.y * gridSize + 1,
            gridSize - 2,
            gridSize - 2
        );
        
        // 绘制蛇眼（仅蛇头）
        if (index === 0) {
            ctx.fillStyle = 'white';
            ctx.beginPath();
            ctx.arc(
                segment.x * gridSize + gridSize / 3,
                segment.y * gridSize + gridSize / 3,
                2,
                0,
                Math.PI * 2
            );
            ctx.arc(
                segment.x * gridSize + gridSize * 2 / 3,
                segment.y * gridSize + gridSize / 3,
                2,
                0,
                Math.PI * 2
            );
            ctx.fill();
        }
    });
    ctx.shadowBlur = 0;
}

// 游戏结束
function gameOver() {
    gameRunning = false;
    clearInterval(gameLoop);
    
    // 显示游戏结束效果
    ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    
    ctx.fillStyle = 'white';
    ctx.font = 'bold 40px Arial';
    ctx.textAlign = 'center';
    ctx.fillText('游戏结束', canvas.width / 2, canvas.height / 2 - 30);
    
    ctx.font = '24px Arial';
    ctx.fillText(`最终得分: ${score}`, canvas.width / 2, canvas.height / 2 + 20);
    
    // 显示重新开始按钮
    restartBtn.style.display = 'inline-block';
}

// 开始游戏
function startGame() {
    initGame();
    gameRunning = true;
    startBtn.style.display = 'none';
    restartBtn.style.display = 'none';
    
    // 每100毫秒更新一次
    gameLoop = setInterval(gameUpdate, 100);
}

// 键盘控制
document.addEventListener('keydown', (e) => {
    // 防止方向键滚动页面
    if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
        e.preventDefault();
    }
    
    // 根据当前方向判断，不允许180度掉头
    switch (e.key) {
        case 'ArrowUp':
            if (dy !== 1) {
                dx = 0;
                dy = -1;
            }
            break;
        case 'ArrowDown':
            if (dy !== -1) {
                dx = 0;
                dy = 1;
            }
            break;
        case 'ArrowLeft':
            if (dx !== 1) {
                dx = -1;
                dy = 0;
            }
            break;
        case 'ArrowRight':
            if (dx !== -1) {
                dx = 1;
                dy = 0;
            }
            break;
    }
});

// 按钮事件
startBtn.addEventListener('click', startGame);
restartBtn.addEventListener('click', startGame);

// 初始化游戏画面（未开始时）
initGame();
