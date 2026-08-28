// 贪吃蛇游戏核心逻辑

class SnakeGame {
    constructor() {
        this.canvas = document.getElementById('gameCanvas');
        this.ctx = this.canvas.getContext('2d');
        
        // 游戏配置
        this.gridSize = 20;
        this.tileCount = this.canvas.width / this.gridSize;
        
        // 游戏状态
        this.snake = [];
        this.food = { x: 0, y: 0 };
        this.direction = { x: 0, y: 0 };
        this.nextDirection = { x: 0, y: 0 };
        this.score = 0;
        this.highScore = parseInt(localStorage.getItem('snakeHighScore')) || 0;
        this.gameRunning = false;
        this.gamePaused = false;
        this.gameLoop = null;
        
        // 速度设置
        this.speeds = {
            '150': 150,
            '100': 100,
            '60': 60,
            '40': 40
        };
        
        // DOM 元素
        this.scoreElement = document.getElementById('score');
        this.highScoreElement = document.getElementById('highScore');
        this.finalScoreElement = document.getElementById('finalScore');
        this.startBtn = document.getElementById('startBtn');
        this.pauseBtn = document.getElementById('pauseBtn');
        this.gameOverModal = document.getElementById('gameOver');
        this.overlay = document.getElementById('overlay');
        this.restartBtn = document.getElementById('restartBtn');
        this.speedSelect = document.getElementById('speedSelect');
        
        this.init();
    }
    
    init() {
        // 绑定事件
        this.startBtn.addEventListener('click', () => this.start());
        this.pauseBtn.addEventListener('click', () => this.togglePause());
        this.restartBtn.addEventListener('click', () => this.start());
        this.speedSelect.addEventListener('change', () => this.changeSpeed());
        document.addEventListener('keydown', (e) => this.handleKeyPress(e));
        
        // 显示最高分
        this.highScoreElement.textContent = this.highScore;
        
        // 初始绘制
        this.resetGame();
        this.draw();
    }
    
    resetGame() {
        // 初始化蛇（在中心位置，长度为3）
        const startX = Math.floor(this.tileCount / 2);
        const startY = Math.floor(this.tileCount / 2);
        this.snake = [
            { x: startX, y: startY },
            { x: startX - 1, y: startY },
            { x: startX - 2, y: startY }
        ];
        
        // 初始方向向右
        this.direction = { x: 1, y: 0 };
        this.nextDirection = { x: 1, y: 0 };
        
        // 重置分数
        this.score = 0;
        this.scoreElement.textContent = this.score;
        
        // 生成第一个食物
        this.generateFood();
    }
    
    start() {
        this.resetGame();
        this.gameRunning = true;
        this.gamePaused = false;
        
        this.startBtn.disabled = true;
        this.pauseBtn.disabled = false;
        this.pauseBtn.textContent = '暂停';
        
        this.gameOverModal.style.display = 'none';
        this.overlay.style.display = 'none';
        
        this.gameLoop = setInterval(() => this.update(), this.speeds[this.speedSelect.value]);
    }
    
    togglePause() {
        if (!this.gameRunning) return;
        
        this.gamePaused = !this.gamePaused;
        
        if (this.gamePaused) {
            this.pauseBtn.textContent = '继续';
            clearInterval(this.gameLoop);
        } else {
            this.pauseBtn.textContent = '暂停';
            this.gameLoop = setInterval(() => this.update(), this.speeds[this.speedSelect.value]);
        }
    }
    
    changeSpeed() {
        if (this.gameRunning && !this.gamePaused) {
            clearInterval(this.gameLoop);
            this.gameLoop = setInterval(() => this.update(), this.speeds[this.speedSelect.value]);
        }
    }
    
    // 游戏主循环
    update() {
        if (this.gamePaused) return;
        
        // 更新方向
        this.direction = { ...this.nextDirection };
        
        // 计算蛇头新位置
        const head = { ...this.snake[0] };
        head.x += this.direction.x;
        head.y += this.direction.y;
        
        // 碰撞检测
        if (this.checkCollision(head)) {
            this.gameOver();
            return;
        }
        
        // 将新头部加入蛇身
        this.snake.unshift(head);
        
        // 检查是否吃到食物
        if (head.x === this.food.x && head.y === this.food.y) {
            this.score += 10;
            this.scoreElement.textContent = this.score;
            
            // 检查是否创造最高分
            if (this.score > this.highScore) {
                this.highScore = this.score;
                this.highScoreElement.textContent = this.highScore;
                localStorage.setItem('snakeHighScore', this.highScore);
            }
            
            // 生成新食物
            this.generateFood();
        } else {
            // 没吃到食物，移除尾部
            this.snake.pop();
        }
        
        // 重绘
        this.draw();
    }
    
    // 方向控制
    handleKeyPress(e) {
        if (!this.gameRunning) return;
        
        switch (e.key) {
            case 'ArrowUp':
                if (this.direction.y !== 1) {
                    this.nextDirection = { x: 0, y: -1 };
                }
                break;
            case 'ArrowDown':
                if (this.direction.y !== -1) {
                    this.nextDirection = { x: 0, y: 1 };
                }
                break;
            case 'ArrowLeft':
                if (this.direction.x !== 1) {
                    this.nextDirection = { x: -1, y: 0 };
                }
                break;
            case 'ArrowRight':
                if (this.direction.x !== -1) {
                    this.nextDirection = { x: 1, y: 0 };
                }
                break;
            case ' ':
                this.togglePause();
                break;
        }
    }
    
    // 碰撞检测
    checkCollision(head) {
        // 撞墙检测
        if (head.x < 0 || head.x >= this.tileCount || head.y < 0 || head.y >= this.tileCount) {
            return true;
        }
        
        // 撞自己检测（从第二个节开始检查）
        for (let i = 1; i < this.snake.length; i++) {
            if (head.x === this.snake[i].x && head.y === this.snake[i].y) {
                return true;
            }
        }
        
        return false;
    }
    
    // 生成食物
    generateFood() {
        let newFood;
        let onSnake;
        
        do {
            onSnake = false;
            newFood = {
                x: Math.floor(Math.random() * this.tileCount),
                y: Math.floor(Math.random() * this.tileCount)
            };
            
            // 确保食物不在蛇身上
            for (const segment of this.snake) {
                if (segment.x === newFood.x && segment.y === newFood.y) {
                    onSnake = true;
                    break;
                }
            }
        } while (onSnake);
        
        this.food = newFood;
    }
    
    // 游戏结束
    gameOver() {
        this.gameRunning = false;
        clearInterval(this.gameLoop);
        
        this.startBtn.disabled = false;
        this.pauseBtn.disabled = true;
        
        this.finalScoreElement.textContent = this.score;
        this.gameOverModal.style.display = 'block';
        this.overlay.style.display = 'block';
    }
    
    // 绘制游戏画面
    draw() {
        // 清空画布
        this.ctx.fillStyle = '#1a1a2e';
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        
        // 绘制网格（可选，增强视觉效果）
        this.drawGrid();
        
        // 绘制食物
        this.drawFood();
        
        // 绘制蛇
        this.drawSnake();
        
        // 绘制暂停提示
        if (this.gamePaused) {
            this.drawPauseMessage();
        }
    }
    
    drawGrid() {
        this.ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
        this.ctx.lineWidth = 1;
        
        for (let i = 0; i <= this.tileCount; i++) {
            // 垂直线
            this.ctx.beginPath();
            this.ctx.moveTo(i * this.gridSize, 0);
            this.ctx.lineTo(i * this.gridSize, this.canvas.height);
            this.ctx.stroke();
            
            // 水平线
            this.ctx.beginPath();
            this.ctx.moveTo(0, i * this.gridSize);
            this.ctx.lineTo(this.canvas.width, i * this.gridSize);
            this.ctx.stroke();
        }
    }
    
    drawSnake() {
        this.snake.forEach((segment, index) => {
            const x = segment.x * this.gridSize;
            const y = segment.y * this.gridSize;
            
            // 蛇头用亮色，身体渐变色
            if (index === 0) {
                // 蛇头
                this.ctx.fillStyle = '#00d9a7';
                this.ctx.shadowBlur = 15;
                this.ctx.shadowColor = '#00d9a7';
            } else {
                // 蛇身（渐变色）
                const gradient = (index / this.snake.length);
                const r = Math.floor(0 + gradient * 30);
                const g = Math.floor(217 - gradient * 100);
                const b = Math.floor(167 - gradient * 100);
                this.ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
                this.ctx.shadowBlur = 0;
            }
            
            // 绘制圆角矩形
            const radius = 5;
            this.ctx.beginPath();
            this.ctx.roundRect(x + 1, y + 1, this.gridSize - 2, this.gridSize - 2, radius);
            this.ctx.fill();
            
            // 蛇头眼睛
            if (index === 0) {
                this.ctx.shadowBlur = 0;
                this.ctx.fillStyle = '#fff';
                
                // 根据方向绘制眼睛
                const eyeSize = 4;
                const eyeOffset = 5;
                
                if (this.direction.x === 1) { // 右
                    this.drawEye(x + this.gridSize - eyeOffset, y + eyeOffset, eyeSize);
                    this.drawEye(x + this.gridSize - eyeOffset, y + this.gridSize - eyeOffset, eyeSize);
                } else if (this.direction.x === -1) { // 左
                    this.drawEye(x + eyeOffset, y + eyeOffset, eyeSize);
                    this.drawEye(x + eyeOffset, y + this.gridSize - eyeOffset, eyeSize);
                } else if (this.direction.y === -1) { // 上
                    this.drawEye(x + eyeOffset, y + eyeOffset, eyeSize);
                    this.drawEye(x + this.gridSize - eyeOffset, y + eyeOffset, eyeSize);
                } else { // 下
                    this.drawEye(x + eyeOffset, y + this.gridSize - eyeOffset, eyeSize);
                    this.drawEye(x + this.gridSize - eyeOffset, y + this.gridSize - eyeOffset, eyeSize);
                }
            }
        });
        
        this.ctx.shadowBlur = 0;
    }
    
    drawEye(x, y, size) {
        this.ctx.beginPath();
        this.ctx.arc(x, y, size, 0, Math.PI * 2);
        this.ctx.fill();
    }
    
    drawFood() {
        const x = this.food.x * this.gridSize;
        const y = this.food.y * this.gridSize;
        
        // 食物发光效果
        this.ctx.shadowBlur = 20;
        this.ctx.shadowColor = '#ff6b6b';
        
        // 绘制圆形食物
        this.ctx.fillStyle = '#ff6b6b';
        this.ctx.beginPath();
        this.ctx.arc(x + this.gridSize / 2, y + this.gridSize / 2, this.gridSize / 2 - 2, 0, Math.PI * 2);
        this.ctx.fill();
        
        // 食物高光
        this.ctx.shadowBlur = 0;
        this.ctx.fillStyle = 'rgba(255, 255, 255, 0.5)';
        this.ctx.beginPath();
        this.ctx.arc(x + this.gridSize / 2 - 3, y + this.gridSize / 2 - 3, 4, 0, Math.PI * 2);
        this.ctx.fill();
    }
    
    drawPauseMessage() {
        this.ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        
        this.ctx.fillStyle = '#fff';
        this.ctx.font = 'bold 48px Arial';
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = 'middle';
        this.ctx.fillText('已暂停', this.canvas.width / 2, this.canvas.height / 2);
    }
}

// 页面加载完成后初始化游戏
document.addEventListener('DOMContentLoaded', () => {
    window.game = new SnakeGame();
});
