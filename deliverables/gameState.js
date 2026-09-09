{
  // ===================== 游戏状态数据结构定义 =====================
  // 本模块定义了贪吃蛇游戏的核心数据结构

  /**
   * 游戏状态数据结构
   * 
   * @typedef {Object} GameState
   * @property {Array<{x: number, y: number}>} snake       - 蛇身坐标列表，索引0为蛇头
   * @property {{x: number, y: number}}         food        - 食物位置坐标
   * @property {'UP' | 'DOWN' | 'LEFT' | 'RIGHT'} direction  - 当前移动方向
   * @property {number}                         score       - 当前分数
   * @property {boolean}                        gameOver    - 游戏是否结束标志
   * @property {boolean}                        isRunning   - 游戏是否正在运行
   */

  /**
   * 初始化游戏状态
   * 
   * @returns {GameState} 初始化的游戏状态对象
   */
  function initGameState() {
    return {
      // 蛇身坐标列表：初始蛇长为3，位于网格中心区域
      snake: [
        { x: 10, y: 10 },  // 蛇头
        { x: 9, y: 10 },   // 蛇身
        { x: 8, y: 10 }    // 蛇尾
      ],
      
      // 食物位置：初始生成在网格右侧区域
      food: { x: 15, y: 10 },
      
      // 方向状态：初始向右移动
      direction: 'RIGHT',
      
      // 当前分数：初始为0
      score: 0,
      
      // 游戏结束标志：初始为false（游戏未结束）
      gameOver: false,
      
      // 游戏运行标志：初始为true（游戏开始运行）
      isRunning: true
    };
  }

  /**
   * 方向常量定义
   */
  const DIRECTIONS = {
    UP: 'UP',
    DOWN: 'DOWN',
    LEFT: 'LEFT',
    RIGHT: 'RIGHT'
  };

  /**
   * 验证状态数据有效性
   * 
   * @param {GameState} state - 游戏状态对象
   * @returns {boolean} 状态是否有效
   */
  function isValidState(state) {
    if (!state) return false;
    
    // 检查蛇身列表
    if (!Array.isArray(state.snake) || state.snake.length === 0) {
      return false;
    }
    
    // 检查每个蛇身坐标
    for (const segment of state.snake) {
      if (!segment || typeof segment.x !== 'number' || typeof segment.y !== 'number') {
        return false;
      }
    }
    
    // 检查食物位置
    if (!state.food || typeof state.food.x !== 'number' || typeof state.food.y !== 'number') {
      return false;
    }
    
    // 检查方向
    if (!Object.values(DIRECTIONS).includes(state.direction)) {
      return false;
    }
    
    // 检查分数
    if (typeof state.score !== 'number' || state.score < 0) {
      return false;
    }
    
    // 检查结束标志
    if (typeof state.gameOver !== 'boolean') {
      return false;
    }
    
    return true;
  }

  // 导出模块
  module.exports = {
    initGameState,
    DIRECTIONS,
    isValidState
  };
