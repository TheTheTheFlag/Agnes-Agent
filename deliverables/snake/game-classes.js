/**
 * 贪吃蛇游戏 - 面向对象基础类实现
 * 子任务：画布初始化、网格设置、蛇对象初始化（位置/长度/方向）
 */

// ==================== 游戏配置类 ====================
class GameConfig {
    static GRID_SIZE = 20;        // 网格尺寸（单元格数量）
    static CELL_SIZE = 20;        // 单个单元格像素大小
    static CANVAS_SIZE = 400;     // 画布总尺寸
    static GAME_SPEED = 150;      // 游戏帧间隔（毫秒）

    // 计算网格列数和行数
    static get COLS() { return this.CANVAS_SIZE / this.CELL_SIZE; }
    static get ROWS() { return this.CANVAS_SIZE / this.CELL_SIZE; }
}

// ==================== 方向枚举 ====================
const Direction = {
    UP: 'up',
    DOWN: 'down',
    LEFT: 'left',
    RIGHT: 'right',

    // 方向向量映射
    VECTORS: {
        'up': { x: 0, y: -1 },
        'down': { x: 0, y: 1 },
        'left': { x: -1, y: 0 },
        'right': { x: 1, y: 0 }
    },

    // 相反方向映射（用于防止180度反转）
    OPPOSITES: {
        'up': 'down',
        'down': 'up',
        'left': 'right',
        'right': 'left'
    },

    // 获取指定方向的向量（静态方法）
    getVector(dir) {
        return Direction.VECTORS[dir] || Direction.VECTORS['right'];
    },

    // 判断两方向是否相反（静态方法）
    isOpposite(dir1, dir2) {
        return Direction.OPPOSITES[dir1] === dir2;
    }
};

// ==================== 坐标类 ====================
class Position {
    constructor(x = 0, y = 0) {
        this.x = x;
        this.y = y;
    }

    // 创建副本
    clone() {
        return new Position(this.x, this.y);
    }

    // 根据方向移动
    move(direction) {
        const vec = Direction.getVector(direction);
        return new Position(this.x + vec.x, this.y + vec.y);
    }

    // 与另一个坐标比较是否相同
    equals(other) {
        return this.x === other.x && this.y === other.y;
    }

    // 转换为网格坐标对象
    toGridObj() {
        return { x: this.x, y: this.y };
    }
}

// ==================== 蛇类 ====================
class Snake {
    constructor(startX, startY, initialLength = 3) {
        this.segments = [];           // 蛇身段数组
        this.direction = Direction.RIGHT;  // 当前方向
        this.nextDirection = Direction.RIGHT; // 下一帧方向
        this.pendingMoves = [];       // 待处理的移动指令队列
        this.maxQueueLength = 3;      // 最大队列长度

        // 初始化蛇身 - 从起点向左延伸
        this._initSegments(startX, startY, initialLength);
    }

    /**
     * 初始化蛇身段
     * @param {number} startX - 起始X坐标（蛇头位置）
     * @param {number} startY - 起始Y坐标
     * @param {number} length - 初始长度
     */
    _initSegments(startX, startY, length) {
        this.segments = [];
        for (let i = 0; i < length; i++) {
            // 蛇身从右向左排列：head在最右侧
            this.segments.push(new Position(startX - i, startY));
        }
    }

    /**
     * 获取蛇头位置
     */
    get head() {
        return this.segments[0];
    }

    /**
     * 获取蛇身长度
     */
    get length() {
        return this.segments.length;
    }

    /**
     * 添加强制方向的输入指令（存入队列）
     * 防止180度掉头的多层防护：
     * 1. 检查当前方向 - 不能直接反转
     * 2. 检查队列末尾方向 - 快速连按时防止第二次反转
     * 3. 限制队列长度 - 防止过多指令堆积
     * 
     * @param {string} newDir - 新方向
     * @returns {boolean} 是否成功添加
     */
    queueMove(newDir) {
        // 获取实际要比较的基准方向（队列非空时用队列末尾）
        const baseDir = this.pendingMoves.length > 0 
            ? this.pendingMoves[this.pendingMoves.length - 1] 
            : this.direction;
        
        // 防护1: 不允许直接反转方向
        if (Direction.isOpposite(newDir, baseDir)) {
            return false;
        }

        // 防护2: 与基准方向相同则忽略
        if (newDir === baseDir) {
            return false;
        }

        // 防护3: 限制队列长度，防止指令堆积
        if (this.pendingMoves.length >= this.maxQueueLength) {
            return false;
        }

        this.pendingMoves.push(newDir);
        return true;
    }

    /**
     * 从队列消费下一个方向
     */
    consumeNextDirection() {
        if (this.pendingMoves.length > 0) {
            this.direction = this.pendingMoves.shift();
        }
        return this.direction;
    }

    /**
     * 获取下一帧的蛇头位置
     */
    getNextHead() {
        return this.head.move(this.direction);
    }

    /**
     * 移动蛇身
     * @param {Position} newHead - 新蛇头位置
     * @param {boolean} grow - 是否增长（吃到食物）
     */
    move(newHead, grow = false) {
        // 消费队列中的方向
        this.consumeNextDirection();

        // 在头部添加新位置
        this.segments.unshift(newHead);

        // 如果没有吃到食物，移除尾部
        if (!grow) {
            this.segments.pop();
        }
    }

    /**
     * 检查是否与自身碰撞
     * @param {Position} pos - 要检查的位置
     */
    checkSelfCollision(pos) {
        return this.segments.some(seg => seg.equals(pos));
    }

    /**
     * 重置蛇到初始状态
     */
    reset(startX, startY, length = 3) {
        this.segments = [];
        this.direction = Direction.RIGHT;
        this.nextDirection = Direction.RIGHT;
        this.pendingMoves = [];
        this._initSegments(startX, startY, length);
    }

    /**
     * 获取所有蛇身段的网格对象数组
     */
    toGridArray() {
        return this.segments.map(seg => seg.toGridObj());
    }
}

// ==================== 画布管理器类 ====================
class CanvasManager {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) {
            throw new Error(`Canvas element with id "${canvasId}" not found`);
        }
        this.ctx = this.canvas.getContext('2d');
        this.width = GameConfig.CANVAS_SIZE;
        this.height = GameConfig.CANVAS_SIZE;

        // 设置画布尺寸
        this.canvas.width = this.width;
        this.canvas.height = this.height;
    }

    /**
     * 清空画布
     */
    clear(color = '#0f0f23') {
        this.ctx.fillStyle = color;
        this.ctx.fillRect(0, 0, this.width, this.height);
    }

    /**
     * 绘制网格
     */
    drawGrid() {
        const cellSize = GameConfig.CELL_SIZE;
        this.ctx.strokeStyle = '#1a1a3e';
        this.ctx.lineWidth = 0.5;

        // 绘制垂直线
        for (let i = 0; i <= this.width; i += cellSize) {
            this.ctx.beginPath();
            this.ctx.moveTo(i, 0);
            this.ctx.lineTo(i, this.height);
            this.ctx.stroke();
        }

        // 绘制水平线
        for (let i = 0; i <= this.height; i += cellSize) {
            this.ctx.beginPath();
            this.ctx.moveTo(0, i);
            this.ctx.lineTo(this.width, i);
            this.ctx.stroke();
        }
    }

    /**
     * 绘制食物
     * @param {Position} pos - 食物位置
     */
    drawFood(pos) {
        const cellSize = GameConfig.CELL_SIZE;
        const centerX = pos.x * cellSize + cellSize / 2;
        const centerY = pos.y * cellSize + cellSize / 2;
        const radius = cellSize / 2 - 2;

        this.ctx.fillStyle = '#ff6b6b';
        this.ctx.beginPath();
        this.ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
        this.ctx.fill();
    }

    /**
     * 绘制蛇
     * @param {Snake} snake - 蛇对象
     */
    drawSnake(snake) {
        const cellSize = GameConfig.CELL_SIZE;

        snake.segments.forEach((seg, index) => {
            const x = seg.x * cellSize + 1;
            const y = seg.y * cellSize + 1;
            const size = cellSize - 2;

            // 创建渐变效果
            const gradient = this.ctx.createRadialGradient(
                x + cellSize / 2, y + cellSize / 2, 2,
                x + cellSize / 2, y + cellSize / 2, cellSize / 2
            );

            // 蛇头和蛇身使用不同的颜色
            if (index === 0) {
                gradient.addColorStop(0, '#7bed9f');
                gradient.addColorStop(1, '#4ecca3');
            } else {
                gradient.addColorStop(0, '#4ecca3');
                gradient.addColorStop(1, '#2d8a6e');
            }

            this.ctx.fillStyle = gradient;
            this.ctx.fillRect(x, y, size, size);
        });
    }

    /**
     * 绘制游戏结束画面
     * @param {number} score - 最终得分
     * @param {number} highScore - 最高分
     */
    drawGameOver(score, highScore) {
        // 半透明遮罩
        this.ctx.fillStyle = 'rgba(0, 0, 0, 0.85)';
        this.ctx.fillRect(0, 0, this.width, this.height);

        // 绘制装饰边框
        this.ctx.strokeStyle = '#ff6b6b';
        this.ctx.lineWidth = 3;
        this.ctx.strokeRect(20, 60, this.width - 40, this.height - 120);

        // GAME OVER 文字
        this.ctx.fillStyle = '#ff6b6b';
        this.ctx.font = 'bold 42px Arial';
        this.ctx.textAlign = 'center';
        this.ctx.fillText('GAME OVER', this.width / 2, 115);

        // 绘制装饰线
        this.ctx.strokeStyle = '#4ecca3';
        this.ctx.lineWidth = 2;
        this.ctx.beginPath();
        this.ctx.moveTo(60, 135);
        this.ctx.lineTo(this.width - 60, 135);
        this.ctx.stroke();

        // 得分文字
        this.ctx.fillStyle = '#fff';
        this.ctx.font = '28px Arial';
        this.ctx.fillText(`得分: ${score}`, this.width / 2, 175);

        // 最高分文字（如果是新纪录则高亮显示）
        if (score >= highScore && score > 0) {
            this.ctx.fillStyle = '#ffd93d';
            this.ctx.font = 'bold 22px Arial';
            this.ctx.fillText('🏆 新纪录!', this.width / 2, 210);
        } else {
            this.ctx.fillStyle = '#888';
            this.ctx.font = '18px Arial';
            this.ctx.fillText(`最高分: ${highScore}`, this.width / 2, 210);
        }

        // 重玩按钮背景
        const btnX = this.width / 2 - 70;
        const btnY = 250;
        const btnW = 140;
        const btnH = 45;

        // 保存按钮区域供点击检测
        this.replayButton = { x: btnX, y: btnY, width: btnW, height: btnH };

        // 按钮渐变背景
        const gradient = this.ctx.createLinearGradient(btnX, btnY, btnX, btnY + btnH);
        gradient.addColorStop(0, '#4ecca3');
        gradient.addColorStop(1, '#2d8a6e');
        
        this.ctx.fillStyle = gradient;
        this.ctx.beginPath();
        this.ctx.roundRect(btnX, btnY, btnW, btnH, 8);
        this.ctx.fill();

        // 按钮文字
        this.ctx.fillStyle = '#fff';
        this.ctx.font = 'bold 18px Arial';
        this.ctx.fillText('再来一局', this.width / 2, btnY + 30);

        // 提示文字
        this.ctx.fillStyle = '#666';
        this.ctx.font = '14px Arial';
        this.ctx.fillText('点击按钮或按空格键重玩', this.width / 2, 320);
    }

    /**
     * 检查点击是否在重玩按钮上
     * @param {number} x - 点击X坐标
     * @param {number} y - 点击Y坐标
     * @returns {boolean} 是否点击了重玩按钮
     */
    isClickOnReplayButton(x, y) {
        if (!this.replayButton) return false;
        const btn = this.replayButton;
        return x >= btn.x && x <= btn.x + btn.width &&
               y >= btn.y && y <= btn.y + btn.height;
    }

    /**
     * 清除重玩按钮引用
     */
    clearReplayButton() {
        this.replayButton = null;
    }

    /**
     * 绘制初始画面
     */
    drawInitial() {
        this.clear();
        this.drawGrid();

        // 绘制提示文字
        this.ctx.fillStyle = '#fff';
        this.ctx.font = '20px Arial';
        this.ctx.textAlign = 'center';
        this.ctx.fillText('点击开始按钮', this.width / 2, this.height / 2);
        this.ctx.fillText('或按空格键', this.width / 2, this.height / 2 + 30);
    }
}

// ==================== 食物生成器类 ====================
class FoodSpawner {
    constructor(snake) {
        this.position = null;
        this.snake = snake;
    }

    /**
     * 生成新食物位置（确保不在蛇身上）
     */
    spawn() {
        let newPos;
        let attempts = 0;
        const maxAttempts = 100;

        do {
            newPos = new Position(
                Math.floor(Math.random() * GameConfig.COLS),
                Math.floor(Math.random() * GameConfig.ROWS)
            );
            attempts++;
        } while (
            this.snake.checkSelfCollision(newPos) &&
            attempts < maxAttempts
        );

        this.position = newPos;
        return newPos;
    }

    /**
     * 获取食物位置
     */
    getPosition() {
        return this.position;
    }
}

// ==================== 导出类供全局使用 ====================
window.GameConfig = GameConfig;
window.Direction = Direction;
window.Position = Position;
window.Snake = Snake;
window.CanvasManager = CanvasManager;
window.FoodSpawner = FoodSpawner;
