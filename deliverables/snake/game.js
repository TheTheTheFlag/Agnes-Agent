/**
 * 贪吃蛇游戏 - Neon Snake
 * 作者：派蒙 AI Assistant
 * 日期：2026-09-12
 */

// ========== 常量配置 ==========
const CONFIG = {
  GRID_SIZE: 20,        // 格子大小（像素）
  GRID_WIDTH: 20,       // 格子宽度
  GRID_HEIGHT: 20,      // 格子高度
  INITIAL_SPEED: 150,   // 初始速度（毫秒/帧）
  SPEED_INCREMENT: 5,   // 每级加速（毫秒）
  FOOD_PER_LEVEL: 5,    // 每关所需食物数
  COLORS: {
    snakeHead: '#0f0',
    snakeBody: '#0a0',
    food: '#f0f',
    grid: '#111',
    background: '#000'
  }
};

// ========== 游戏状态 ==========
let canvas, ctx;
let snake = [];
let direction = 'RIGHT';
let nextDirection = 'RIGHT';
let food = { x: 0, y: 0 };
let score = 0;
let highScore = parseInt(localStorage.getItem('snakeHighScore')) || 0;
let level = 1;
let foodCount = 0;
let gameSpeed = CONFIG.INITIAL_SPEED;
let gameStatus = 'IDLE'; // IDLE, RUNNING, PAUSED, GAME_OVER
let lastRenderTime = 0;
let animationFrameId = null;

// ========== 初始化 ==========
function init() {
  canvas = document.getElementById('gameCanvas');
  ctx = canvas.getContext('2d');
  
  // 设置画布尺寸
  canvas.width = CONFIG.GRID_SIZE * CONFIG.GRID_WIDTH;
  canvas.height = CONFIG.GRID_SIZE * CONFIG.GRID_HEIGHT;
  
  // 绑定事件
  document.addEventListener('keydown', handleKey);
  document.getElementById('startBtn').addEventListener('click', startGame);
  
  // 触摸事件（移动端）
  let touchStartX = 0;
  let touchStartY = 0;
  
  canvas.addEventListener('touchstart', (e) => {
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
    e.preventDefault();
  }, { passive: false });
  
  canvas.addEventListener('touchend', (e) => {
    if (gameStatus !== 'RUNNING') return;
    
    const touchEndX = e.changedTouches[0].clientX;
    const touchEndY = e.changedTouches[0].clientY;
    const dx = touchEndX - touchStartX;
    const dy = touchEndY - touchStartY;
    
    if (Math.abs(dx) > Math.abs(dy)) {
      // 水平滑动
      if (dx > 0 && direction !== 'LEFT') nextDirection = 'RIGHT';
      else if (dx < 0 && direction !== 'RIGHT') nextDirection = 'LEFT';
    } else {
      // 垂直滑动
      if (dy > 0 && direction !== 'UP') nextDirection = 'DOWN';
      else if (dy < 0 && direction !== 'DOWN') nextDirection = 'UP';
    }
    e.preventDefault();
  }, { passive: false });
  
  // 更新显示
  updateDisplay();
  render();
}

// ========== 游戏控制 ==========
function startGame() {
  resetGame();
  gameStatus = 'RUNNING';
  hideOverlay();
  lastRenderTime = performance.now();
  requestAnimationFrame(gameLoop);
}

function resetGame() {
  // 初始化蛇（居中，长度3）
  const startX = Math.floor(CONFIG.GRID_WIDTH / 2);
  const startY = Math.floor(CONFIG.GRID_HEIGHT / 2);
  snake = [
    { x: startX, y: startY },
    { x: startX - 1, y: startY },
    { x: startX - 2, y: startY }
  ];
  
  direction = 'RIGHT';
  nextDirection = 'RIGHT';
  score = 0;
  level = 1;
  foodCount = 0;
  gameSpeed = CONFIG.INITIAL_SPEED;
  
  generateFood();
  updateDisplay();
  render();
}

function pauseGame() {
  if (gameStatus === 'RUNNING') {
    gameStatus = 'PAUSED';
    showOverlay('游戏暂停', '按空格键继续', '继续');
  } else if (gameStatus === 'PAUSED') {
    gameStatus = 'RUNNING';
    hideOverlay();
    lastRenderTime = performance.now();
    requestAnimationFrame(gameLoop);
  }
}

function gameOver() {
  gameStatus = 'GAME_OVER';
  
  // 更新最高分
  if (score > highScore) {
    highScore = score;
    localStorage.setItem('snakeHighScore', highScore);
  }
  
  updateDisplay();
  showOverlay('游戏结束', `最终得分：${score}`, '重新开始');
}

// ========== 输入处理 ==========
function handleKey(e) {
  // 空格键暂停/继续
  if (e.code === 'Space') {
    e.preventDefault();
    if (gameStatus === 'IDLE' || gameStatus === 'GAME_OVER') {
      startGame();
    } else {
      pauseGame();
    }
    return;
  }
  
  if (gameStatus !== 'RUNNING') return;
  
  // 方向控制（防止180度反转）
  switch (e.code) {
    case 'ArrowUp':
    case 'KeyW':
      if (direction !== 'DOWN') nextDirection = 'UP';
      break;
    case 'ArrowDown':
    case 'KeyS':
      if (direction !== 'UP') nextDirection = 'DOWN';
      break;
    case 'ArrowLeft':
    case 'KeyA':
      if (direction !== 'RIGHT') nextDirection = 'LEFT';
      break;
    case 'ArrowRight':
    case 'KeyD':
      if (direction !== 'LEFT') nextDirection = 'RIGHT';
      break;
  }
  
  e.preventDefault();
}

// ========== 游戏逻辑 ==========
function update() {
  // 更新方向
  direction = nextDirection;
  
  // 计算新头部位置
  const head = { ...snake[0] };
  switch (direction) {
    case 'UP': head.y--; break;
    case 'DOWN': head.y++; break;
    case 'LEFT': head.x--; break;
    case 'RIGHT': head.x++; break;
  }
  
  // 检测碰撞
  if (checkCollision(head)) {
    gameOver();
    return;
  }
  
  // 移动蛇
  snake.unshift(head);
  
  // 检测是否吃到食物
  if (head.x === food.x && head.y === food.y) {
    score += 10 * level;
    foodCount++;
    
    // 检查是否升级
    if (foodCount >= CONFIG.FOOD_PER_LEVEL) {
      level++;
      foodCount = 0;
      gameSpeed = Math.max(50, gameSpeed - CONFIG.SPEED_INCREMENT);
      showLevelUp();
    }
    
    generateFood();
    updateDisplay();
  } else {
    // 没吃到食物，移除尾部
    snake.pop();
  }
}

function checkCollision(pos) {
  // 撞墙
  if (pos.x < 0 || pos.x >= CONFIG.GRID_WIDTH || 
      pos.y < 0 || pos.y >= CONFIG.GRID_HEIGHT) {
    return true;
  }
  
  // 撞自己
  for (let i = 0; i < snake.length; i++) {
    if (pos.x === snake[i].x && pos.y === snake[i].y) {
      return true;
    }
  }
  
  return false;
}

function generateFood() {
  let newFood;
  let validPosition = false;
  
  while (!validPosition) {
    newFood = {
      x: Math.floor(Math.random() * CONFIG.GRID_WIDTH),
      y: Math.floor(Math.random() * CONFIG.GRID_HEIGHT)
    };
    
    // 检查是否与蛇身重叠
    validPosition = !snake.some(seg => seg.x === newFood.x && seg.y === newFood.y);
  }
  
  food = newFood;
}

// ========== 渲染 ==========
function render() {
  // 清空画布
  ctx.fillStyle = CONFIG.COLORS.background;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  
  // 绘制网格
  drawGrid();
  
  // 绘制食物
  drawFood();
  
  // 绘制蛇
  drawSnake();
}

function drawGrid() {
  ctx.strokeStyle = CONFIG.COLORS.grid;
  ctx.lineWidth = 0.5;
  
  for (let x = 0; x <= CONFIG.GRID_WIDTH; x++) {
    ctx.beginPath();
    ctx.moveTo(x * CONFIG.GRID_SIZE, 0);
    ctx.lineTo(x * CONFIG.GRID_SIZE, canvas.height);
    ctx.stroke();
  }
  
  for (let y = 0; y <= CONFIG.GRID_HEIGHT; y++) {
    ctx.beginPath();
    ctx.moveTo(0, y * CONFIG.GRID_SIZE);
    ctx.lineTo(canvas.width, y * CONFIG.GRID_SIZE);
    ctx.stroke();
  }
}

function drawSnake() {
  snake.forEach((seg, i) => {
    const x = seg.x * CONFIG.GRID_SIZE;
    const y = seg.y * CONFIG.GRID_SIZE;
    
    // 蛇头和蛇身不同颜色
    ctx.fillStyle = i === 0 ? CONFIG.COLORS.snakeHead : CONFIG.COLORS.snakeBody;
    
    // 发光效果
    ctx.shadowColor = CONFIG.COLORS.snakeHead;
    ctx.shadowBlur = i === 0 ? 15 : 5;
    
    // 绘制圆角矩形
    roundRect(ctx, x + 1, y + 1, CONFIG.GRID_SIZE - 2, CONFIG.GRID_SIZE - 2, 5);
    ctx.fill();
    
    // 绘制眼睛（头部）
    if (i === 0) {
      ctx.shadowBlur = 0;
      ctx.fillStyle = '#000';
      
      const eyeSize = 3;
      let eye1X, eye1Y, eye2X, eye2Y;
      
      switch (direction) {
        case 'UP':
          eye1X = x + 5; eye1Y = y + 5;
          eye2X = x + CONFIG.GRID_SIZE - 8; eye2Y = y + 5;
          break;
        case 'DOWN':
          eye1X = x + 5; eye1Y = y + CONFIG.GRID_SIZE - 8;
          eye2X = x + CONFIG.GRID_SIZE - 8; eye2Y = y + CONFIG.GRID_SIZE - 8;
          break;
        case 'LEFT':
          eye1X = x + 5; eye1Y = y + 5;
          eye2X = x + 5; eye2Y = y + CONFIG.GRID_SIZE - 8;
          break;
        case 'RIGHT':
          eye1X = x + CONFIG.GRID_SIZE - 8; eye1Y = y + 5;
          eye2X = x + CONFIG.GRID_SIZE - 8; eye2Y = y + CONFIG.GRID_SIZE - 8;
          break;
      }
      
      ctx.beginPath();
      ctx.arc(eye1X + eyeSize/2, eye1Y + eyeSize/2, eyeSize, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(eye2X + eyeSize/2, eye2Y + eyeSize/2, eyeSize, 0, Math.PI * 2);
      ctx.fill();
    }
  });
  
  // 重置阴影
  ctx.shadowBlur = 0;
}

function drawFood() {
  const x = food.x * CONFIG.GRID_SIZE;
  const y = food.y * CONFIG.GRID_SIZE;
  
  ctx.fillStyle = CONFIG.COLORS.food;
  ctx.shadowColor = CONFIG.COLORS.food;
  ctx.shadowBlur = 15;
  
  // 绘制圆形食物
  ctx.beginPath();
  ctx.arc(x + CONFIG.GRID_SIZE / 2, y + CONFIG.GRID_SIZE / 2, CONFIG.GRID_SIZE / 2 - 2, 0, Math.PI * 2);
  ctx.fill();
  
  ctx.shadowBlur = 0;
}

function roundRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.lineTo(x + width - radius, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
  ctx.lineTo(x + width, y + height - radius);
  ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  ctx.lineTo(x + radius, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
  ctx.lineTo(x, y + radius);
  ctx.quadraticCurveTo(x, y, x + radius, y);
  ctx.closePath();
}

// ========== 游戏循环 ==========
function gameLoop(currentTime) {
  if (gameStatus !== 'RUNNING') return;
  
  animationFrameId = requestAnimationFrame(gameLoop);
  
  const elapsed = currentTime - lastRenderTime;
  
  if (elapsed >= gameSpeed) {
    update();
    render();
    lastRenderTime = currentTime;
  }
}

// ========== UI 更新 ==========
function updateDisplay() {
  document.getElementById('score').textContent = score;
  document.getElementById('highScore').textContent = highScore;
  document.getElementById('level').textContent = level;
  document.getElementById('speed').textContent = (CONFIG.INITIAL_SPEED / gameSpeed).toFixed(1) + 'x';
}

function showOverlay(title, subtitle, btnText) {
  const overlay = document.getElementById('overlay');
  document.getElementById('overlayTitle').textContent = title;
  document.getElementById('overlaySubtitle').textContent = subtitle;
  document.getElementById('startBtn').textContent = btnText;
  overlay.classList.remove('hidden');
}

function hideOverlay() {
  document.getElementById('overlay').classList.add('hidden');
}

function showLevelUp() {
  // 闪烁效果
  const overlay = document.getElementById('overlay');
  const title = document.getElementById('overlayTitle');
  
  title.textContent = `🎉 关卡 ${level}！`;
  title.style.color = '#ff0';
  overlay.classList.remove('hidden');
  
  setTimeout(() => {
    overlay.classList.add('hidden');
    title.style.color = '#f0f';
  }, 1000);
}

// ========== 启动 ==========
window.addEventListener('load', init);
