/**
 * 贪吃蛇游戏 —— 核心逻辑（独立模块）
 *
 * 覆盖功能：
 *  ✅ 蛇的移动（setInterval 固定步长推进 + 排队方向防止同帧自反）
 *  ✅ 键盘控制（方向键 + 空格暂停/开始）+ 移动端虚拟方向键
 *  ✅ 食物生成（基于空位集合随机挑选，保证不与蛇身重叠；支持通关判定）
 *  ✅ 碰撞检测（撞墙 / 撞自身）
 *  ✅ 得分更新（每个食物 +10，最高分 localStorage 持久化）
 *  ✅ 难度递增（每累计 50 分升一级，速度递减至 60ms 封顶）
 *  ✅ 游戏结束（提示文案 + 新纪录检测 + 状态机切换 + 失败原因区分）
 *  ✅ 重新开始（Reset 回到就绪态 / GameOver 后按 Start 或空格直接重开）
 *  ✅ 暂停/继续（空格键 / 暂停按钮）
 *  ✅ 通关（蛇占满整张地图时触发）
 *  ✅ 视觉细节：网格背景、蛇身圆角、蛇头眼睛、食物高光、分数脉冲动画
 *
 * 依赖 DOM 元素（需在 HTML 中存在）：
 *  #gameCanvas / #score / #highScore / #level / #startBtn / #pauseBtn
 *  / #resetBtn / #gameStatus / #speedDisplay / .d-pad-btn[data-dir]
 */
(function () {
    'use strict';

    // ============== 常量 ==============
    const GameState = {
        READY:     'ready',
        RUNNING:   'running',
        PAUSED:    'paused',
        GAME_OVER: 'gameover'
    };

    const CONFIG = {
        gridSize:        20,    // 每格像素
        initialSpeed:    150,   // 初始步长（毫秒/帧），越小越快
        minSpeed:        60,    // 最高速度（毫秒/帧，难度上限）
        speedDecrement:  10,    // 每升一级减少的毫秒数
        scorePerFood:    10,    // 每个食物得分
        levelUpScore:    50,    // 每 50 分升一级
        highScoreKey:    'snakeHighScore' // localStorage 键名
    };

    // ============== DOM 引用 ==============
    let canvas, ctx;
    let scoreEl, highScoreEl, levelEl;
    let startBtn, pauseBtn, resetBtn;
    let gameStatusEl, speedDisplayEl;

    // ============== 状态变量 ==============
    let gameState     = GameState.READY;
    let score         = 0;
    let highScore     = 0;
    let level         = 1;
    let currentSpeed  = CONFIG.initialSpeed;
    let snake         = [];
    let food          = null;
    let direction     = 'right';
    let nextDirection = 'right';
    let gameLoop      = null;

    // ============== 初始化 ==============
    function init() {
        canvas = document.getElementById('gameCanvas');
        if (!canvas) return;
        ctx = canvas.getContext('2d');

        scoreEl        = document.getElementById('score');
        highScoreEl    = document.getElementById('highScore');
        levelEl        = document.getElementById('level');
        startBtn       = document.getElementById('startBtn');
        pauseBtn       = document.getElementById('pauseBtn');
        resetBtn       = document.getElementById('resetBtn');
        gameStatusEl   = document.getElementById('gameStatus');
        speedDisplayEl = document.getElementById('speedDisplay');

        // 从 localStorage 读取最高分
        try {
            const stored = parseInt(localStorage.getItem(CONFIG.highScoreKey) || '0', 10);
            highScore = isNaN(stored) ? 0 : stored;
        } catch (e) {
            highScore = 0;
        }
        if (highScoreEl) highScoreEl.textContent = highScore;

        bindEvents();
        updateUI();
        drawWelcome();
    }

    // ============== 事件绑定 ==============
    function bindEvents() {
        if (startBtn) startBtn.addEventListener('click', startGame);
        if (pauseBtn) pauseBtn.addEventListener('click', togglePause);
        if (resetBtn) resetBtn.addEventListener('click', resetGame);

        document.addEventListener('keydown', handleKeyPress);

        // 移动端虚拟方向键
        document.querySelectorAll('.d-pad-btn[data-dir]').forEach(btn => {
            const handler = (e) => {
                e.preventDefault();
                changeDirection(btn.dataset.dir);
            };
            btn.addEventListener('touchstart', handler, { passive: false });
            btn.addEventListener('mousedown', handler);
        });
    }

    function handleKeyPress(e) {
        const key = e.key;

        // 方向键
        if (key === 'ArrowUp' || key === 'ArrowDown' || key === 'ArrowLeft' || key === 'ArrowRight') {
            e.preventDefault();
            const map = {
                ArrowUp: 'up', ArrowDown: 'down',
                ArrowLeft: 'left', ArrowRight: 'right'
            };
            changeDirection(map[key]);
            return;
        }

        // 空格：暂停 / 继续 / 开始
        if (key === ' ' || key === 'Spacebar') {
            e.preventDefault();
            if (gameState === GameState.RUNNING || gameState === GameState.PAUSED) {
                togglePause();
            } else if (gameState === GameState.READY || gameState === GameState.GAME_OVER) {
                startGame();
            }
        }
    }

    /**
     * 改变移动方向
     *  - 防止 180° 反向自撞
     *  - 仅在游戏中生效（运行中 / 暂停中可预先切换方向以便恢复时立即生效）
     */
    function changeDirection(newDir) {
        if (gameState !== GameState.RUNNING && gameState !== GameState.PAUSED) return;
        const opposite = { up: 'down', down: 'up', left: 'right', right: 'left' };
        if (opposite[direction] === newDir) return;
        nextDirection = newDir;
    }

    // ============== 游戏生命周期 ==============
    function startGame() {
        if (gameState === GameState.RUNNING) return;

        // 全新开始 或 死亡后重新开始：重新初始化游戏数据
        if (gameState === GameState.READY || gameState === GameState.GAME_OVER) {
            initGameState();
        }

        gameState = GameState.RUNNING;
        hideStatus();

        if (startBtn) {
            startBtn.disabled = true;
            startBtn.textContent = '开始';
        }
        if (pauseBtn) {
            pauseBtn.disabled = false;
            pauseBtn.textContent = '暂停';
        }
        startLoop();
    }

    function togglePause() {
        if (gameState === GameState.RUNNING) {
            gameState = GameState.PAUSED;
            stopLoop();
            showStatus('⏸ 游戏已暂停 — 按空格继续', 'paused');
            if (pauseBtn) pauseBtn.textContent = '继续';
        } else if (gameState === GameState.PAUSED) {
            gameState = GameState.RUNNING;
            hideStatus();
            if (pauseBtn) pauseBtn.textContent = '暂停';
            startLoop();
        }
    }

    function resetGame() {
        stopLoop();
        gameState     = GameState.READY;
        score         = 0;
        level         = 1;
        currentSpeed  = CONFIG.initialSpeed;
        direction     = 'right';
        nextDirection = 'right';
        snake         = [];
        food          = null;

        updateUI();
        hideStatus();

        if (startBtn) {
            startBtn.disabled = false;
            startBtn.textContent = '开始';
        }
        if (pauseBtn) {
            pauseBtn.disabled = true;
            pauseBtn.textContent = '暂停';
        }
        drawWelcome();
    }

    function initGameState() {
        const maxX = canvas.width  / CONFIG.gridSize;
        const maxY = canvas.height / CONFIG.gridSize;
        const midX = Math.floor(maxX / 2);
        const midY = Math.floor(maxY / 2);

        // 蛇身长 3，居中向右
        snake = [
            { x: midX,     y: midY },
            { x: midX - 1, y: midY },
            { x: midX - 2, y: midY }
        ];
        direction     = 'right';
        nextDirection = 'right';
        score         = 0;
        level         = 1;
        currentSpeed  = CONFIG.initialSpeed;

        spawnFood();
        updateUI();
    }

    // ============== 主循环 ==============
    function startLoop() {
        stopLoop();
        gameLoop = setInterval(tick, currentSpeed);
    }

    function stopLoop() {
        if (gameLoop !== null) {
            clearInterval(gameLoop);
            gameLoop = null;
        }
    }

    /**
     * 每帧逻辑：移动 → 碰撞检测 → 吃食物 → 渲染
     */
    function tick() {
        if (gameState !== GameState.RUNNING) return;

        // 应用本帧方向
        direction = nextDirection;
        const head = snake[0];
        const newHead = { x: head.x, y: head.y };

        switch (direction) {
            case 'up':    newHead.y -= 1; break;
            case 'down':  newHead.y += 1; break;
            case 'left':  newHead.x -= 1; break;
            case 'right': newHead.x += 1; break;
        }

        // 1) 撞墙检测
        const maxX = canvas.width  / CONFIG.gridSize;
        const maxY = canvas.height / CONFIG.gridSize;
        if (newHead.x < 0 || newHead.x >= maxX || newHead.y < 0 || newHead.y >= maxY) {
            handleGameOver('撞墙了！');
            return;
        }

        // 2) 撞自身检测
        for (let i = 0; i < snake.length; i++) {
            if (snake[i].x === newHead.x && snake[i].y === newHead.y) {
                handleGameOver('撞到自己了！');
                return;
            }
        }

        // 推进蛇身
        snake.unshift(newHead);

        // 3) 吃食物？
        if (food && newHead.x === food.x && newHead.y === food.y) {
            score += CONFIG.scorePerFood;
            spawnFood();
            updateLevel();
            updateUI();
            pulseScore();
        } else {
            snake.pop();
        }

        draw();
    }

    // ============== 食物生成 ==============
    /**
     * 基于空位集合随机生成食物，保证不与蛇身重叠
     * 当蛇身占满整张地图时触发通关
     */
    function spawnFood() {
        const maxX  = canvas.width  / CONFIG.gridSize;
        const maxY  = canvas.height / CONFIG.gridSize;
        const total = maxX * maxY;

        if (snake.length >= total) {
            handleWin();
            return;
        }

        // 收集蛇身占位（key 形式避免二维数组构造）
        const occupied = new Set();
        for (let i = 0; i < snake.length; i++) {
            occupied.add(snake[i].y * maxX + snake[i].x);
        }

        // 从空位中随机选一个
        const emptyCount = total - snake.length;
        let pick = Math.floor(Math.random() * emptyCount);
        for (let k = 0; k < total; k++) {
            if (!occupied.has(k)) {
                if (pick === 0) {
                    food = { x: k % maxX, y: Math.floor(k / maxX) };
                    return;
                }
                pick--;
            }
        }
    }

    // ============== 难度递增 ==============
    /**
     * 每累计 levelUpScore 分升一级
     * 每升一级步长减少 speedDecrement 毫秒（不低于 minSpeed 上限）
     */
    function updateLevel() {
        const newLevel = Math.floor(score / CONFIG.levelUpScore) + 1;
        if (newLevel > level) {
            level = newLevel;
            currentSpeed = Math.max(
                CONFIG.minSpeed,
                CONFIG.initialSpeed - (level - 1) * CONFIG.speedDecrement
            );
            // 速度变化时重启主循环以应用新步长
            if (gameState === GameState.RUNNING) {
                startLoop();
            }
        }
    }

    // ============== 游戏结束 / 通关 ==============
    function handleGameOver(reason) {
        stopLoop();
        gameState = GameState.GAME_OVER;

        let message = '💀 ';
        if (reason) message += reason + ' ';
        message += '游戏结束！最终得分: ' + score;

        if (score > highScore) {
            highScore = score;
            try { localStorage.setItem(CONFIG.highScoreKey, String(highScore)); } catch (e) { /* 忽略存储错误 */ }
            if (highScoreEl) highScoreEl.textContent = highScore;
            message += '  🎉 新纪录！';
        } else if (score === highScore && score > 0) {
            message += '  （追平最高分）';
        }

        showStatus(message + ' — 点击「再玩一局」或按空格键重新开始', 'gameover');

        if (startBtn) {
            startBtn.disabled = false;
            startBtn.textContent = '再玩一局';
        }
        if (pauseBtn) {
            pauseBtn.disabled = true;
            pauseBtn.textContent = '暂停';
        }
        updateUI();
    }

    function handleWin() {
        stopLoop();
        gameState = GameState.GAME_OVER;

        if (score > highScore) {
            highScore = score;
            try { localStorage.setItem(CONFIG.highScoreKey, String(highScore)); } catch (e) { /* ignore */ }
            if (highScoreEl) highScoreEl.textContent = highScore;
        }

        showStatus('🏆 恭喜通关！蛇已填满整张地图！最终得分: ' + score + ' — 点击「再玩一局」或按空格键重新开始', 'gameover');
        if (startBtn) {
            startBtn.disabled = false;
            startBtn.textContent = '再玩一局';
        }
        if (pauseBtn) pauseBtn.disabled = true;
        updateUI();
    }

    // ============== UI 辅助 ==============
    function updateUI() {
        if (scoreEl)        scoreEl.textContent        = score;
        if (highScoreEl)    highScoreEl.textContent    = highScore;
        if (levelEl)        levelEl.textContent        = level;
        if (speedDisplayEl) speedDisplayEl.textContent = currentSpeed + 'ms';
    }

    function pulseScore() {
        if (!scoreEl) return;
        scoreEl.classList.remove('pulse');
        // 强制 reflow 以重启动画
        void scoreEl.offsetWidth;
        scoreEl.classList.add('pulse');
        setTimeout(() => scoreEl.classList.remove('pulse'), 500);
    }

    function showStatus(msg, type) {
        if (!gameStatusEl) return;
        gameStatusEl.textContent = msg;
        gameStatusEl.className = 'game-status show status-' + type;
    }

    function hideStatus() {
        if (!gameStatusEl) return;
        gameStatusEl.className = 'game-status';
    }

    // ============== 绘制 ==============
    function drawWelcome() {
        if (!ctx) return;
        ctx.fillStyle = '#f0f0f0';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#999';
        ctx.font = '20px "Microsoft YaHei", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('点击「开始」按钮', canvas.width / 2, canvas.height / 2 - 14);
        ctx.fillText('或按空格键启动游戏', canvas.width / 2, canvas.height / 2 + 18);
    }

    function draw() {
        // 背景
        ctx.fillStyle = '#f0f0f0';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        drawGrid();
        drawFood();
        drawSnake();
    }

    function drawGrid() {
        ctx.strokeStyle = '#e6e6e6';
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        for (let x = 0; x <= canvas.width; x += CONFIG.gridSize) {
            ctx.moveTo(x + 0.5, 0);
            ctx.lineTo(x + 0.5, canvas.height);
        }
        for (let y = 0; y <= canvas.height; y += CONFIG.gridSize) {
            ctx.moveTo(0, y + 0.5);
            ctx.lineTo(canvas.width, y + 0.5);
        }
        ctx.stroke();
    }

    function drawSnake() {
        const g = CONFIG.gridSize;
        for (let i = 0; i < snake.length; i++) {
            const seg = snake[i];
            const x = seg.x * g;
            const y = seg.y * g;
            const gradient = ctx.createLinearGradient(x, y, x + g, y + g);
            if (i === 0) {
                // 蛇头：深绿渐变
                gradient.addColorStop(0, '#11998e');
                gradient.addColorStop(1, '#38ef7d');
            } else {
                // 蛇身：随长度逐渐变浅
                const t = Math.min(1, i / 30);
                const r1 = Math.round(0x56 * (1 - t) + 0xa8 * t);
                const g1 = Math.round(0xab * (1 - t) + 0xe0 * t);
                const b1 = Math.round(0x2f * (1 - t) + 0x63 * t);
                const r2 = Math.round(0xa8 * (1 - t) + 0xd0 * t);
                const g2 = Math.round(0xe0 * (1 - t) + 0xf0 * t);
                const b2 = Math.round(0x63 * (1 - t) + 0x80 * t);
                gradient.addColorStop(0, `rgb(${r1},${g1},${b1})`);
                gradient.addColorStop(1, `rgb(${r2},${g2},${b2})`);
            }
            ctx.fillStyle = gradient;
            roundRect(ctx, x + 1, y + 1, g - 2, g - 2, 4);
            ctx.fill();

            // 蛇头加眼睛
            if (i === 0) drawEyes(seg, direction);
        }
    }

    function drawEyes(head, dir) {
        const g = CONFIG.gridSize;
        const cx = head.x * g + g / 2;
        const cy = head.y * g + g / 2;
        const r  = Math.max(1.5, g * 0.09);

        let e1, e2;
        switch (dir) {
            case 'up':
                e1 = { x: cx - g * 0.22, y: cy - g * 0.18 };
                e2 = { x: cx + g * 0.22, y: cy - g * 0.18 };
                break;
            case 'down':
                e1 = { x: cx - g * 0.22, y: cy + g * 0.18 };
                e2 = { x: cx + g * 0.22, y: cy + g * 0.18 };
                break;
            case 'left':
                e1 = { x: cx - g * 0.18, y: cy - g * 0.22 };
                e2 = { x: cx - g * 0.18, y: cy + g * 0.22 };
                break;
            default: // right
                e1 = { x: cx + g * 0.18, y: cy - g * 0.22 };
                e2 = { x: cx + g * 0.18, y: cy + g * 0.22 };
                break;
        }
        ctx.fillStyle = '#1a3a2a';
        ctx.beginPath();
        ctx.arc(e1.x, e1.y, r, 0, Math.PI * 2);
        ctx.arc(e2.x, e2.y, r, 0, Math.PI * 2);
        ctx.fill();
    }

    function drawFood() {
        if (!food) return;
        const g = CONFIG.gridSize;
        const cx = food.x * g + g / 2;
        const cy = food.y * g + g / 2;
        const radius = g / 2 - 2;

        const gradient = ctx.createRadialGradient(
            cx - radius * 0.3, cy - radius * 0.3, radius * 0.1,
            cx, cy, radius
        );
        gradient.addColorStop(0,   '#ff8a80');
        gradient.addColorStop(0.6, '#ff5252');
        gradient.addColorStop(1,   '#c92a2a');

        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fill();
    }

    function roundRect(c, x, y, w, h, r) {
        if (w < 2 * r) r = w / 2;
        if (h < 2 * r) r = h / 2;
        c.beginPath();
        c.moveTo(x + r, y);
        c.arcTo(x + w, y,     x + w, y + h, r);
        c.arcTo(x + w, y + h, x,     y + h, r);
        c.arcTo(x,     y + h, x,     y,     r);
        c.arcTo(x,     y,     x + w, y,     r);
        c.closePath();
    }

    // ============== 启动 ==============
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
