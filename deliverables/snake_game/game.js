class SnakeGame {
    constructor() {
        this.canvas = document.getElementById('game-canvas');
        this.ctx = this.canvas.getContext('2d');
        this.currentScoreEl = document.getElementById('current-score');
        this.highScoreEl = document.getElementById('high-score');
        this.overlay = document.getElementById('game-overlay');
        this.overlayTitle = document.getElementById('overlay-title');
        this.overlayMessage = document.getElementById('overlay-message');
        this.finalScoreEl = document.getElementById('final-score');
        this.finalScoreValueEl = document.getElementById('final-score-value');
        this.startBtn = document.getElementById('start-btn');
        this.pauseBtn = document.getElementById('pause-btn');
        this.resetBtn = document.getElementById('reset-btn');
        
        this.gridSize = 20;
        this.tileCount = this.canvas.width / this.gridSize;
        
        this.snake = [];
        this.food = {};
        this.direction = { x: 0, y: 0 };
        this.nextDirection = { x: 0, y: 0 };
        this.score = 0;
        this.highScore = localStorage.getItem('snakeHighScore') || 0;
        this.gameRunning = false;
        this.gamePaused = false;
        this.gameLoop = null;
        
        this.init();
    }
    
    init() {
        this.highScoreEl.textContent = this.highScore;
        this.bindEvents();
        this.resetGame();
        this.draw();
    }
    
    bindEvents() {
        this.startBtn.addEventListener('click', () => this.startGame());
        this.pauseBtn.addEventListener('click', () => this.togglePause());
        this.resetBtn.addEventListener('click', () => this.resetGame());
        
        document.addEventListener('keydown', (e) => this.handleKeydown(e));
    }
    
    resetGame() {
        this.snake = [
            { x: 10, y: 10 },
            { x: 9, y: 10 },
            { x: 8, y: 10 }
        ];
        this.direction = { x: 1, y: 0 };
        this.nextDirection = { x: 1, y: 0 };
        this.score = 0;
        this.currentScoreEl.textContent = '0';
        this.spawnFood();
        this.showOverlay('准备开始', '按下方按钮开始游戏');
        this.toggleButtons(false);
    }
    
    startGame() {
        if (!this.gameRunning) {
            this.gameRunning = true;
            this.gamePaused = false;
            this.hideOverlay();
            this.toggleButtons(true);
            this.gameLoop = setInterval(() => this.update(), 150);
        }
    }
    
    togglePause() {
        if (this.gameRunning) {
            this.gamePaused = !this.gamePaused;
            this.pauseBtn.textContent = this.gamePaused ? '继续' : '暂停';
            
            if (this.gamePaused) {
                clearInterval(this.gameLoop);
                this.showOverlay('已暂停', '游戏已暂停');
            } else {
                this.hideOverlay();
                this.gameLoop = setInterval(() => this.update(), 150);
            }
        }
    }
    
    handleKeydown(e) {
        if (!this.gameRunning || this.gamePaused) return;
        
        switch(e.key) {
            case 'ArrowUp':
            case 'w':
            case 'W':
                if (this.direction.y !== 1) {
                    this.nextDirection = { x: 0, y: -1 };
                }
                break;
            case 'ArrowDown':
            case 's':
            case 'S':
                if (this.direction.y !== -1) {
                    this.nextDirection = { x: 0, y: 1 };
                }
                break;
            case 'ArrowLeft':
            case 'a':
            case 'A':
                if (this.direction.x !== 1) {
                    this.nextDirection = { x: -1, y: 0 };
                }
                break;
            case 'ArrowRight':
            case 'd':
            case 'D':
                if (this.direction.x !== -1) {
                    this.nextDirection = { x: 1, y: 0 };
                }
                break;
        }
    }
    
    update() {
        this.direction = { ...this.nextDirection };
        
        const head = {
            x: this.snake[0].x + this.direction.x,
            y: this.snake[0].y + this.direction.y
        };
        
        // 检查碰撞
        if (this.checkCollision(head)) {
            this.gameOver();
            return;
        }
        
        this.snake.unshift(head);
        
        // 检查是否吃到食物
        if (head.x === this.food.x && head.y === this.food.y) {
            this.score += 10;
            this.currentScoreEl.textContent = this.score;
            this.spawnFood();
        } else {
            this.snake.pop();
        }
        
        this.draw();
    }
    
    checkCollision(head) {
        // 墙壁碰撞
        if (head.x < 0 || head.x >= this.tileCount || 
            head.y < 0 || head.y >= this.tileCount) {
            return true;
        }
        
        // 自身碰撞
        for (let i = 0; i < this.snake.length; i++) {
            if (head.x === this.snake[i].x && head.y === this.snake[i].y) {
                return true;
            }
        }
        
        return false;
    }
    
    spawnFood() {
        do {
            this.food = {
                x: Math.floor(Math.random() * this.tileCount),
                y: Math.floor(Math.random() * this.tileCount)
            };
        } while (this.isSnakeAt(this.food.x, this.food.y));
    }
    
    isSnakeAt(x, y) {
        return this.snake.some(segment => segment.x === x && segment.y === y);
    }
    
    draw() {
        // 清空画布
        this.ctx.fillStyle = '#1a1a2e';
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        
        // 绘制网格
        this.drawGrid();
        
        // 绘制蛇
        this.drawSnake();
        
        // 绘制食物
        this.drawFood();
    }
    
    drawGrid() {
        this.ctx.strokeStyle = '#2a2a4e';
        this.ctx.lineWidth = 0.5;
        
        for (let i = 0; i <= this.tileCount; i++) {
            this.ctx.beginPath();
            this.ctx.moveTo(i * this.gridSize, 0);
            this.ctx.lineTo(i * this.gridSize, this.canvas.height);
            this.ctx.stroke();
            
            this.ctx.beginPath();
            this.ctx.moveTo(0, i * this.gridSize);
            this.ctx.lineTo(this.canvas.width, i * this.gridSize);
            this.ctx.stroke();
        }
    }
    
    drawSnake() {
        this.snake.forEach((segment, index) => {
            const gradient = this.ctx.createRadialGradient(
                segment.x * this.gridSize + this.gridSize / 2,
                segment.y * this.gridSize + this.gridSize / 2,
                0,
                segment.x * this.gridSize + this.gridSize / 2,
                segment.y * this.gridSize + this.gridSize / 2,
                this.gridSize / 2
            );
            
            if (index === 0) {
                gradient.addColorStop(0, '#764ba2');
                gradient.addColorStop(1, '#667eea');
            } else {
                gradient.addColorStop(0, '#667eea');
                gradient.addColorStop(1, '#764ba2');
            }
            
            this.ctx.fillStyle = gradient;
            this.ctx.beginPath();
            this.ctx.roundRect(
                segment.x * this.gridSize + 1,
                segment.y * this.gridSize + 1,
                this.gridSize - 2,
                this.gridSize - 2,
                5
            );
            this.ctx.fill();
            
            // 绘制眼睛
            if (index === 0) {
                this.drawEyes(segment);
            }
        });
    }
    
    drawEyes(head) {
        this.ctx.fillStyle = '#fff';
        
        const eyeSize = 4;
        const offset = 6;
        
        let eye1X, eye1Y, eye2X, eye2Y;
        
        if (this.direction.x === 1) {
            eye1X = head.x * this.gridSize + offset + 8;
            eye1Y = head.y * this.gridSize + offset;
            eye2X = head.x * this.gridSize + offset + 8;
            eye2Y = head.y * this.gridSize + this.gridSize - offset - 4;
        } else if (this.direction.x === -1) {
            eye1X = head.x * this.gridSize + offset;
            eye1Y = head.y * this.gridSize + offset;
            eye2X = head.x * this.gridSize + offset;
            eye2Y = head.y * this.gridSize + this.gridSize - offset - 4;
        } else if (this.direction.y === -1) {
            eye1X = head.x * this.gridSize + offset;
            eye1Y = head.y * this.gridSize + offset;
            eye2X = head.x * this.gridSize + this.gridSize - offset - 4;
            eye2Y = head.y * this.gridSize + offset;
        } else {
            eye1X = head.x * this.gridSize + offset;
            eye1Y = head.y * this.gridSize + offset + 8;
            eye2X = head.x * this.gridSize + this.gridSize - offset - 4;
            eye2Y = head.y * this.gridSize + offset + 8;
        }
        
        this.ctx.beginPath();
        this.ctx.arc(eye1X, eye1Y, eyeSize / 2, 0, Math.PI * 2);
        this.ctx.fill();
        
        this.ctx.beginPath();
        this.ctx.arc(eye2X, eye2Y, eyeSize / 2, 0, Math.PI * 2);
        this.ctx.fill();
    }
    
    drawFood() {
        const gradient = this.ctx.createRadialGradient(
            this.food.x * this.gridSize + this.gridSize / 2,
            this.food.y * this.gridSize + this.gridSize / 2,
            0,
            this.food.x * this.gridSize + this.gridSize / 2,
            this.food.y * this.gridSize + this.gridSize / 2,
            this.gridSize / 2
        );
        
        gradient.addColorStop(0, '#ff6b6b');
        gradient.addColorStop(1, '#ee5a5a');
        
        this.ctx.fillStyle = gradient;
        this.ctx.beginPath();
        this.ctx.arc(
            this.food.x * this.gridSize + this.gridSize / 2,
            this.food.y * this.gridSize + this.gridSize / 2,
            this.gridSize / 2 - 2,
            0,
            Math.PI * 2
        );
        this.ctx.fill();
        
        // 高光
        this.ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
        this.ctx.beginPath();
        this.ctx.arc(
            this.food.x * this.gridSize + this.gridSize / 2 - 3,
            this.food.y * this.gridSize + this.gridSize / 2 - 3,
            3,
            0,
            Math.PI * 2
        );
        this.ctx.fill();
    }
    
    gameOver() {
        this.gameRunning = false;
        clearInterval(this.gameLoop);
        
        if (this.score > this.highScore) {
            this.highScore = this.score;
            this.highScoreEl.textContent = this.highScore;
            localStorage.setItem('snakeHighScore', this.highScore);
        }
        
        this.finalScoreValueEl.textContent = this.score;
        this.finalScoreEl.style.display = 'block';
        this.showOverlay('游戏结束', '按重置按钮重新开始');
        this.toggleButtons(false);
    }
    
    showOverlay(title, message) {
        this.overlayTitle.textContent = title;
        this.overlayMessage.textContent = message;
        this.overlay.classList.remove('hidden');
    }
    
    hideOverlay() {
        this.overlay.classList.add('hidden');
    }
    
    toggleButtons(isRunning) {
        this.startBtn.disabled = isRunning;
        this.pauseBtn.disabled = !isRunning;
        this.pauseBtn.textContent = '暂停';
    }
}

// 初始化游戏
window.addEventListener('DOMContentLoaded', () => {
    new SnakeGame();
});
