/* =========================================================================
 * 贪吃蛇（Snake Game）核心逻辑
 * -------------------------------------------------------------------------
 * 功能：
 *   1. 蛇的移动（网格步进，防反向，方向输入队列）
 *   2. 食物生成（随机、不与蛇身重叠）
 *   3. 碰撞检测（撞墙 / 撞自身）
 *   4. 游戏循环（requestAnimationFrame + 固定 tick 间隔 accumulator 方式）
 *   5. 计分系统（每食物 +10，吃食物提速，本地最高分持久化 localStorage）
 *   6. 状态机：ready → running ⇄ paused → over
 *   7. 键盘控制（方向键 / WASD / Space）与触屏 D-pad、滑动控制
 * 运行方式：直接双击 index.html 在本地浏览器打开即可，无需服务器。
 * ========================================================================= */

(function () {
    'use strict';

    /* ------------------------------ 常量 ------------------------------ */
    var GRID_SIZE  = 24;        // 24 x 24 网格
    var CELL_SIZE  = 20;        // 每个格子 20px（与 canvas 480x480 对应）
    var TICK_MS    = 150;       // 初始移动间隔（毫秒）
    var MIN_TICK_MS = 80;       // 最快移动间隔（上限）
    var SPEED_STEP  = 3;        // 每个食物减少的间隔毫秒数
    var SCORE_PER_FOOD = 10;    // 每个食物得分
    var HIGH_SCORE_KEY = 'snakeHighScore';

    /* ------------------------------ DOM 引用 -------------------------- */
    var canvas      = document.getElementById('gameCanvas');
    var ctx         = canvas.getContext('2d');
    var elScore     = document.getElementById('currentScore');
    var elHigh      = document.getElementById('highScore');
    var elLength    = document.getElementById('snakeLength');
    var elFinal     = document.getElementById('finalScore');
    var elReason    = document.getElementById('gameOverReason');
    var elNewRecord = document.getElementById('newRecordMsg');

    var startScreen  = document.getElementById('startScreen');
    var pauseScreen  = document.getElementById('pauseScreen');
    var gameOverScreen = document.getElementById('gameOverScreen');

    var startBtn   = document.getElementById('startBtn');
    var resumeBtn  = document.getElementById('resumeBtn');
    var restartBtn = document.getElementById('restartBtn');

    /* ------------------------------ 游戏状态 -------------------------- */
    // 状态：'ready' | 'running' | 'paused' | 'over'
    var state = 'ready';

    // 蛇：头部在数组末尾（push 新头，shift 去尾），每个元素 {x, y}
    var snake = [];
    // 当前移动方向（单位向量）
    var dir = { x: 1, y: 0 };
    // 输入队列：缓存两个 tick 之间的连续转向，避免同帧内反向导致自杀
    var dirQueue = [];

    var food      = null;   // 食物坐标 {x, y}
    var score     = 0;
    var highScore = 0;
    var tickMs    = TICK_MS;
    var lastTime  = 0;      // 上一帧时间戳
    var acc       = 0;      // 累计经过的时间
    var rafId     = null;   // requestAnimationFrame id

    /* ------------------------------ 工具函数 -------------------------- */

    // 两个坐标是否相同
    function sameCell(a, b) {
        return a.x === b.x && a.y === b.y;
    }

    // 某个坐标是否与蛇身重叠；ignoreTail 为 true 时忽略尾部（尾部即将移动）
    function isOnSnake(x, y, ignoreTail) {
        var end = ignoreTail ? snake.length - 1 : snake.length;
        for (var i = 0; i < end; i++) {
            if (snake[i].x === x && snake[i].y === y) {
                return true;
            }
        }
        return false;
    }

    /* --------------------------- 食物生成 ----------------------------- */
    // 在空白格子上随机生成食物；若棋盘已满返回 null
    function spawnFood() {
        var free = [];
        for (var x = 0; x < GRID_SIZE; x++) {
            for (var y = 0; y < GRID_SIZE; y++) {
                if (!isOnSnake(x, y, false)) {
                    free.push({ x: x, y: y });
                }
            }
        }
        if (free.length === 0) {
            return null; // 蛇已占满全屏 -> 胜利
        }
        return free[Math.floor(Math.random() * free.length)];
    }

    /* ----------------------------- 初始化 ----------------------------- */
    function resetGame() {
        // 蛇初始：位于网格中部，水平向右，长度 3
        var cx = Math.floor(GRID_SIZE / 2);
        var cy = Math.floor(GRID_SIZE / 2);
        snake = [
            { x: cx - 2, y: cy },
            { x: cx - 1, y: cy },
            { x: cx,     y: cy }
        ];
        dir       = { x: 1, y: 0 };
        dirQueue  = [];
        score     = 0;
        tickMs    = TICK_MS;
        acc       = 0;
        lastTime  = 0;
        food      = spawnFood();
        updateScoreboard();
    }

    /* --------------------------- 输入处理 ----------------------------- */

    // 将键盘按键映射为方向；返回 null 表示无效输入
    function keyToDir(key) {
        switch (key) {
            case 'ArrowUp':
            case 'KeyW':    return { x: 0, y: -1 };
            case 'ArrowDown':
            case 'KeyS':    return { x: 0, y: 1 };
            case 'ArrowLeft':
            case 'KeyA':    return { x: -1, y: 0 };
            case 'ArrowRight':
            case 'KeyD':    return { x: 1, y: 0 };
            default:        return null;
        }
    }

    // 设置方向：忽略同向与 180° 反向；入队等待下次 tick 生效
    function setDirection(newDir) {
        if (!newDir) return;
        // 取队列最后一个方向作为“当前即将生效”的方向
        var effective = dirQueue.length > 0 ? dirQueue[dirQueue.length - 1] : dir;
        if (effective.x === newDir.x && effective.y === newDir.y) return;      // 同向忽略
        if (effective.x + newDir.x === 0 && effective.y + newDir.y === 0) return; // 反向忽略
        if (dirQueue.length < 3) {
            dirQueue.push(newDir);
        }
    }

    // 切换开始/暂停/继续/重开（Space 键共用入口）
    function togglePlay() {
        if (state === 'ready') {
            startGame();
        } else if (state === 'running') {
            pauseGame();
        } else if (state === 'paused') {
            resumeGame();
        } else if (state === 'over') {
            startGame();
        }
    }

    function startGame() {
        resetGame();
        state = 'running';
        hideAllOverlays();
        updateScoreboard();
        startLoop();
    }

    function pauseGame() {
        if (state !== 'running') return;
        state = 'paused';
        stopLoop();
        hideAllOverlays();
        pauseScreen.classList.remove('hidden');
    }

    function resumeGame() {
        if (state !== 'paused') return;
        state = 'running';
        hideAllOverlays();
        acc = 0;      // 重置累计时间，避免恢复后瞬间跳帧
        startLoop();
    }

    function gameOver(reason) {
        state = 'over';
        stopLoop();
        render(); // 绘制最终画面

        // 结束原因文案
        var reasonText = '撞到墙壁了……';
        if (reason === 'self') reasonText = '撞到自己了……';
        if (reason === 'win')  reasonText = '🏆 你填满了整个棋盘，完美通关！';
        elReason.textContent = reasonText;

        // 更新最高分
        if (score > highScore) {
            highScore = score;
            try {
                localStorage.setItem(HIGH_SCORE_KEY, String(highScore));
            } catch (e) { /* 隐私模式等场景下忽略 */ }
            elNewRecord.classList.remove('hidden');
        } else {
            elNewRecord.classList.add('hidden');
        }

        elFinal.textContent = score;
        updateScoreboard();

        hideAllOverlays();
        gameOverScreen.classList.remove('hidden');
    }

    /* --------------------------- 计分面板 ----------------------------- */
    function updateScoreboard() {
        elScore.textContent  = score;
        elHigh.textContent   = highScore;
        elLength.textContent = snake.length;
    }

    /* --------------------------- 游戏循环 ----------------------------- */
    function startLoop() {
        stopLoop();
        lastTime = performance.now();
        acc = 0;
        rafId = requestAnimationFrame(loop);
    }

    function stopLoop() {
        if (rafId !== null) {
            cancelAnimationFrame(rafId);
            rafId = null;
        }
    }

    function loop(now) {
        if (state !== 'running') return;
        rafId = requestAnimationFrame(loop);

        var delta = now - lastTime;
        lastTime = now;
        acc += delta;

        // 固定 tick 间隔推进（accumulator 方式，保证移动节奏稳定）
        while (acc >= tickMs) {
            acc -= tickMs;
            tick();
            if (state !== 'running') return; // 死亡后立即停止后续 tick
        }

        render();
    }

    /* --------------------------- 核心移动 ----------------------------- */
    function tick() {
        // 取队列中下一个方向并清空
        if (dirQueue.length > 0) {
            dir = dirQueue.shift();
        }

        var head = snake[snake.length - 1];
        var newHead = {
            x: head.x + dir.x,
            y: head.y + dir.y
        };

        // 1) 撞墙检测
        if (newHead.x < 0 || newHead.x >= GRID_SIZE ||
            newHead.y < 0 || newHead.y >= GRID_SIZE) {
            gameOver('wall');
            return;
        }

        // 2) 撞自身检测：若本 tick 会吃到食物，则尾部不动，不可忽略
        var willEat = food !== null && sameCell(newHead, food);
        if (isOnSnake(newHead.x, newHead.y, willEat)) {
            gameOver('self');
            return;
        }

        // 3) 移动：新头入队
        snake.push(newHead);

        if (willEat) {
            // 吃食物：尾巴不动（身体变长），加分、提速、重新生成食物
            score += SCORE_PER_FOOD;
            if (tickMs > MIN_TICK_MS) {
                tickMs = Math.max(MIN_TICK_MS, tickMs - SPEED_STEP);
            }
            food = spawnFood();
            if (food === null) {
                // 棋盘被蛇占满 -> 胜利结束
                gameOver('win');
                return;
            }
        } else {
            // 普通移动：去掉尾巴
            snake.shift();
        }

        updateScoreboard();
    }

    /* ----------------------------- 渲染 ------------------------------- */
    function render() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // 背景网格（淡色辅助线）
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
        ctx.lineWidth = 1;
        for (var i = 1; i < GRID_SIZE; i++) {
            ctx.beginPath();
            ctx.moveTo(i * CELL_SIZE, 0);
            ctx.lineTo(i * CELL_SIZE, canvas.height);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(0, i * CELL_SIZE);
            ctx.lineTo(canvas.width, i * CELL_SIZE);
            ctx.stroke();
        }

        // 食物（带脉冲呼吸动画）
        if (food) {
            var fx = food.x * CELL_SIZE + CELL_SIZE / 2;
            var fy = food.y * CELL_SIZE + CELL_SIZE / 2;
            var pulse = 0.85 + 0.15 * Math.sin(performance.now() / 150);
            var r = (CELL_SIZE / 2 - 2) * pulse;
            ctx.fillStyle = '#ff5252';
            ctx.beginPath();
            ctx.arc(fx, fy, r, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = 'rgba(255,255,255,0.55)';
            ctx.beginPath();
            ctx.arc(fx - r * 0.3, fy - r * 0.3, r * 0.3, 0, Math.PI * 2);
            ctx.fill();
        }

        // 蛇：头部比身体更亮，身体带圆角
        for (var j = 0; j < snake.length; j++) {
            var seg = snake[j];
            var px = seg.x * CELL_SIZE;
            var py = seg.y * CELL_SIZE;
            var pad = 1;
            var isHead = (j === snake.length - 1);

            ctx.fillStyle = isHead ? '#a5f3b0' : '#4caf50';
            ctx.beginPath();
            roundRect(px + pad, py + pad, CELL_SIZE - pad * 2, CELL_SIZE - pad * 2, 5);
            ctx.fill();

            // 头部眼睛：朝当前方向
            if (isHead) {
                ctx.fillStyle = '#1b2a1f';
                var ex = px + CELL_SIZE / 2 + dir.x * 4;
                var ey = py + CELL_SIZE / 2 + dir.y * 4;
                var ox = -dir.y, oy = dir.x; // 垂直于方向
                var eyeR = 2.4;
                ctx.beginPath();
                ctx.arc(ex + ox * 3.2, ey + oy * 3.2, eyeR, 0, Math.PI * 2);
                ctx.arc(ex - ox * 3.2, ey - oy * 3.2, eyeR, 0, Math.PI * 2);
                ctx.fill();
            }
        }
    }

    // 圆角矩形辅助函数
    function roundRect(x, y, w, h, r) {
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + w, y, x + w, y + h, r);
        ctx.arcTo(x + w, y + h, x, y + h, r);
        ctx.arcTo(x, y + h, x, y, r);
        ctx.arcTo(x, y, x + w, y, r);
        ctx.closePath();
    }

    /* --------------------------- 界面辅助 ----------------------------- */
    function hideAllOverlays() {
        startScreen.classList.add('hidden');
        pauseScreen.classList.add('hidden');
        gameOverScreen.classList.add('hidden');
    }

    /* --------------------------- 事件绑定 ----------------------------- */

    // 键盘控制：方向键 / WASD / Space（阻止默认滚动行为）
    document.addEventListener('keydown', function (e) {
        // 空格：开始 / 暂停 / 继续 / 重开
        if (e.code === 'Space') {
            e.preventDefault();
            togglePlay();
            return;
        }

        var d = keyToDir(e.code);
        if (d) {
            e.preventDefault(); // 阻止方向键滚动页面
            if (state === 'running') {
                setDirection(d);
            } else if (state === 'ready') {
                // 在开始界面直接按方向键：设定方向并立即开局
                startGame();
                setDirection(d);
            }
        }
    });

    // 触屏 / 鼠标 D-pad
    document.querySelectorAll('.dpad-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var d = keyToDir('Arrow' + btn.dataset.dir.charAt(0).toUpperCase() + btn.dataset.dir.slice(1));
            if (d) {
                if (state === 'running') {
                    setDirection(d);
                } else if (state === 'ready') {
                    startGame();
                    setDirection(d);
                }
            }
        });
    });

    // 按钮
    startBtn.addEventListener('click', startGame);
    resumeBtn.addEventListener('click', resumeGame);
    restartBtn.addEventListener('click', startGame);

    // 触摸滑动控制（移动端体验）
    var touchStart = null;
    canvas.addEventListener('touchstart', function (e) {
        var t = e.touches[0];
        touchStart = { x: t.clientX, y: t.clientY };
        e.preventDefault();
    }, { passive: false });

    canvas.addEventListener('touchmove', function (e) {
        if (!touchStart) return;
        var t = e.touches[0];
        var dx = t.clientX - touchStart.x;
        var dy = t.clientY - touchStart.y;
        if (Math.abs(dx) < 20 && Math.abs(dy) < 20) return;
        e.preventDefault();
        if (Math.abs(dx) > Math.abs(dy)) {
            setDirection({ x: dx > 0 ? 1 : -1, y: 0 });
        } else {
            setDirection({ x: 0, y: dy > 0 ? 1 : -1 });
        }
        touchStart = { x: t.clientX, y: t.clientY };
    }, { passive: false });

    /* ------------------------------ 启动 ------------------------------ */

    // 读取最高分（localStorage 可能被禁用，容错处理）
    try {
        highScore = parseInt(localStorage.getItem(HIGH_SCORE_KEY), 10) || 0;
    } catch (e) {
        highScore = 0;
    }

    // 初始化：绘制首帧（蛇 + 食物）+ 开始界面
    resetGame();
    updateScoreboard();
    render();
})();
