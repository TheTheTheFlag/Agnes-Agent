// 游戏配置
const CANVAS_SIZE = 400;
const GRID_SIZE = 20;
const CELL_SIZE = CANVAS_SIZE / GRID_SIZE;
const INITIAL_SPEED = 150; // 毫秒

// 游戏状态
let snake = [];
let direction = 'right';
let nextDirection = 'right';
let food = null;
let score = 0;
let highScore = localStorage.getItem('snakeHighScore') || 0;
let gameLoop = null;
let isRunning = false;
let isPaused = false;
let speed = INITIAL_SPEED;

// DOM 元素
const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
const scoreElement = document.getElementById('score');
const highScoreElement = document.getElementById('high-score');
const startBtn = document.getElementById('startBtn');
const pauseBtn = document.getElementById('pauseBtn');
const resetBtn = document.getElementById('resetBtn');

// 初始化游戏
function initGame() {
    snake = [
        { x: 5, y: 10 },
        { x: 4, y: 10 },
        { x: 3, y: 10 }
    ];
    direction = 'right';
    nextDirection = 'right';
    score = 0;
    speed = INITIAL_SPEED;
    updateScore();
    generateFood();
    draw();
}

// 生成食物
function generateFood() {
    let newFood;
    do {
        newFood = {
            x: Math.floor(Math.random() * GRID_SIZE),
            y: Math.floor(Math.random() * GRID_SIZE)
        };
    } while (snake.some(segment => segment.x === newFood.x && segment.y === newFood.y));
    food = newFood;
}

// 绘制游戏
function draw() {
    // 清空画布
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);

    // 绘制网格
    ctx.strokeStyle = '#2a2a4e';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= GRID_SIZE; i++) {
        ctx.beginPath();
        ctx.moveTo(i * CELL_SIZE, 0);
        ctx.lineTo(i * CELL_SIZE, CANVAS_SIZE);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(0, i * CELL_SIZE);
        ctx.lineTo(CANVAS_SIZE, i * CELL_SIZE);
        ctx.stroke();
    }

    // 绘制蛇
    snake.forEach((segment, index) => {
        const gradient = ctx.createRadialGradient(
            segment.x * CELL_SIZE + CELL_SIZE / 2,
            segment.y * CELL_SIZE + CELL_SIZE / 2,
            0,
            segment.x * CELL_SIZE + CELL_SIZE / 2,
            segment.y * CELL_SIZE + CELL_SIZE / 2,
            CELL_SIZE / 2
        );
        
        if (index === 0) {
            gradient.addColorStop(0, '#764ba2');
            gradient.addColorStop(1, '#667eea');
        } else {
            gradient.addColorStop(0, '#9b5de5');
            gradient.addColorStop(1, '#00f2fe');
        }
        
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.roundRect(
            segment.x * CELL_SIZE + 1,
            segment.y * CELL_SIZE + 1,
            CELL_SIZE - 2,
            CELL_SIZE - 2,
            5
        );
        ctx.fill();

        // 绘制眼睛
        if (index === 0) {
            ctx.fillStyle = '#fff';
            const eyeSize = 3;
            const eyeOffset = 5;
            
            let eye1X, eye1Y, eye2X, eye2Y;
            switch (direction) {
                case 'up':
                    eye1X = segment.x * CELL_SIZE + eyeOffset;
                    eye1Y = segment.y * CELL_SIZE + eyeOffset;
                    eye2X = segment.x * CELL_SIZE + CELL_SIZE - eyeOffset - eyeSize;
                    eye2Y = segment.y * CELL_SIZE + eyeOffset;
                    break;
                case 'down':
                    eye1X = segment.x * CELL_SIZE + eyeOffset;
                    eye1Y = segment.y * CELL_SIZE + CELL_SIZE - eyeOffset - eyeSize;
                    eye2X = segment.x * CELL_SIZE + CELL_SIZE - eyeOffset - eyeSize;
                    eye2Y = segment.y * CELL_SIZE + CELL_SIZE - eyeOffset - eyeSize;
                    break;
                case 'left':
                    eye1X = segment.x * CELL_SIZE + eyeOffset;
                    eye1Y = segment.y * CELL_SIZE + eyeOffset;
                    eye2X = segment.x * CELL_SIZE + eyeOffset;
                    eye2Y = segment.y * CELL_SIZE + CELL_SIZE - eyeOffset - eyeSize;
                    break;
                case 'right':
                    eye1X = segment.x * CELL_SIZE + CELL_SIZE - eyeOffset - eyeSize;
                    eye1Y = segment.y * CELL_SIZE + eyeOffset;
                    eye2X = segment.x * CELL_SIZE + CELL_SIZE - eyeOffset - eyeSize;
                    eye2Y = segment.y * CELL_SIZE + CELL_SIZE - eyeOffset - eyeSize;
                    break;
            }
            
            ctx.beginPath();
            ctx.arc(eye1X + eyeSize/2, eye1Y + eyeSize/2, eyeSize/2, 0, Math.PI * 2);
            ctx.fill();
            ctx.beginPath();
            ctx.arc(eye2X + eyeSize/2, eye2Y + eyeSize/2, eyeSize/2, 0, Math.PI * 2);
            ctx.fill();
        }
    });

    // 绘制食物
    if (food) {
        const foodGradient = ctx.createRadialGradient(
            food.x * CELL_SIZE + CELL_SIZE / 3,
            food.y * CELL_SIZE + CELL_SIZE / 3,
            0,
            food.x * CELL_SIZE + CELL_SIZE / 2,
            food.y * CELL_SIZE + CELL_SIZE / 2,
            CELL_SIZE / 2
        );
        foodGradient.addColorStop(0, '#ff6b6b');
        foodGradient.addColorStop(1, '#ee5a5a');
        
        ctx.fillStyle = foodGradient;
        ctx.beginPath();
        ctx.arc(
            food.x * CELL_SIZE + CELL_SIZE / 2,
            food.y * CELL_SIZE + CELL_SIZE / 2,
            CELL_SIZE / 2 - 2,
            0,
            Math.PI * 2
        );
        ctx.fill();

        // 食物高光
        ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
        ctx.beginPath();
        ctx.arc(
            food.x * CELL_SIZE + CELL_SIZE / 3,
            food.y * CELL_SIZE + CELL_SIZE / 3,
            3,
            0,
            Math.PI * 2
        );
        ctx.fill();
    }
}

// 移动蛇
function moveSnake() {
    direction = nextDirection;
    
    const head = { ...snake[0] };
    
    switch (direction) {
        case 'up':
            head.y--;
            break;
        case 'down':
            head.y++;
            break;
        case 'left':
            head.x--;
            break;
        case 'right':
            head.x++;
            break;
    }

    // 碰撞检测 - 墙壁
    if (head.x < 0 || head.x >= GRID_SIZE || head.y < 0 || head.y >= GRID_SIZE) {
        gameOver();
        return;
    }

    // 碰撞检测 - 自身
    if (snake.some(segment => segment.x === head.x && segment.y === head.y)) {
        gameOver();
        return;
    }

    snake.unshift(head);

    // 吃到食物
    if (head.x === food.x && head.y === food.y) {
        score += 10;
        updateScore();
        generateFood();
        
        // 加速
        if (speed > 50) {
            speed -= 2;
        }
    } else {
        snake.pop();
    }
}

// 更新分数
function updateScore() {
    scoreElement.textContent = score;
    if (score > highScore) {
        highScore = score;
        highScoreElement.textContent = highScore;
        localStorage.setItem('snakeHighScore', highScore);
    }
}

// 游戏结束
function gameOver() {
    isRunning = false;
    clearInterval(gameLoop);
    
    // 显示游戏结束效果
    ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
    ctx.fillRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);
    
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 40px Arial';
    ctx.textAlign = 'center';
    ctx.fillText('游戏结束', CANVAS_SIZE / 2, CANVAS_SIZE / 2 - 20);
    
    ctx.font = '20px Arial';
    ctx.fillText(`最终分数: ${score}`, CANVAS_SIZE / 2, CANVAS_SIZE / 2 + 20);
    
    startBtn.textContent = '开始游戏';
}

// 开始游戏
function startGame() {
    if (!isRunning) {
        initGame();
        isRunning = true;
        isPaused = false;
        gameLoop = setInterval(() => {
            if (!isPaused) {
                moveSnake();
                draw();
            }
        }, speed);
        startBtn.textContent = '游戏中...';
    }
}

// 暂停游戏
function togglePause() {
    if (isRunning) {
        isPaused = !isPaused;
        pauseBtn.textContent = isPaused ? '继续' : '暂停';
    }
}

// 重置游戏
function resetGame() {
    clearInterval(gameLoop);
    isRunning = false;
    isPaused = false;
    startBtn.textContent = '开始游戏';
    pauseBtn.textContent = '暂停';
    initGame();
}

// 键盘控制
document.addEventListener('keydown', (e) => {
    switch (e.key) {
        case 'ArrowUp':
            if (direction !== 'down') nextDirection = 'up';
            break;
        case 'ArrowDown':
            if (direction !== 'up') nextDirection = 'down';
            break;
        case 'ArrowLeft':
            if (direction !== 'right') nextDirection = 'left';
            break;
        case 'ArrowRight':
            if (direction !== 'left') nextDirection = 'right';
            break;
        case ' ':
            e.preventDefault();
            togglePause();
            break;
    }
});

// 按钮事件
startBtn.addEventListener('click', startGame);
pauseBtn.addEventListener('click', togglePause);
resetBtn.addEventListener('click', resetGame);

// 初始化
highScoreElement.textContent = highScore;
initGame();
