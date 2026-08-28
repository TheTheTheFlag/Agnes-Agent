"""
贪吃蛇游戏 - 完整版本
整合渲染、控制、游戏逻辑
包含完整的游戏循环、边界处理、游戏结束判定与重新开始功能
"""

import pygame
import sys
import random
from typing import List, Optional
from dataclasses import dataclass


# 导入渲染模块
sys.path.insert(0, '.')
from deliverables.snake_renderer import (
    GameConfig, Position, Direction, CanvasRenderer, create_game_screen
)
from deliverables.snake_controller import SnakeController, GameState


@dataclass
class SnakeBody:
    """蛇身数据结构"""
    segments: List[Position]
    direction: Direction = Direction.RIGHT
    grow_count: int = 0  # 待增长的长度


class SnakeGame:
    """
    贪吃蛇游戏主类
    整合渲染、控制、游戏逻辑
    """
    
    def __init__(self):
        # 初始化Pygame
        pygame.init()
        
        # 创建游戏屏幕
        self.screen, self.config = create_game_screen()
        self.clock = pygame.time.Clock()
        
        # 初始化游戏组件
        self.renderer = CanvasRenderer(self.screen, self.config)
        self.controller = SnakeController()
        
        # 游戏状态
        self.snake: Optional[SnakeBody] = None
        self.food: Optional[Position] = None
        self.game_speed: int = 150  # 毫秒/帧（初始速度）
        self.move_timer: int = 0
        self.running: bool = True
        
        # 初始化游戏对象
        self._init_game_objects()
        
        # 窗口标题
        pygame.display.set_caption("贪吃蛇游戏")
    
    def _init_game_objects(self):
        """初始化游戏对象"""
        # 初始化蛇（从中心开始，长度为3）
        start_x = self.config.GRID_COLS // 2
        start_y = self.config.GRID_ROWS // 2
        self.snake = SnakeBody(
            segments=[
                Position(start_x, start_y),
                Position(start_x - 1, start_y),
                Position(start_x - 2, start_y),
            ],
            direction=Direction.RIGHT,
            grow_count=0
        )
        
        # 生成第一个食物
        self._spawn_food()
        
        # 重置控制器状态
        self.controller.reset_score()
        self.controller.game_state = GameState.STOPPED
    
    def _spawn_food(self):
        """在随机位置生成食物"""
        while True:
            x = random.randint(0, self.config.GRID_COLS - 1)
            y = random.randint(0, self.config.GRID_ROWS - 1)
            position = Position(x, y)
            
            # 确保食物不生成在蛇身上
            if position not in self.snake.segments:
                self.food = position
                return
    
    def _move_snake(self):
        """移动蛇"""
        if self.snake is None or self.food is None:
            return
        
        # 更新方向
        self.controller.update_direction()
        current_dir = self.controller.get_current_direction()
        
        # 获取新的头部位置
        head = self.snake.segments[0]
        dx, dy = current_dir.value
        new_head = Position(head.x + dx, head.y + dy)
        
        # 检查碰撞
        if self._check_collision(new_head):
            self.controller.game_state = GameState.GAME_OVER
            return
        
        # 移动蛇身
        self.snake.segments.insert(0, new_head)
        
        # 检查是否吃到食物
        if new_head == self.food:
            self.snake.grow_count += 1
            self.controller.increase_score()
            self._spawn_food()
            
            # 加速（最小间隔50ms）
            self.game_speed = max(50, self.game_speed - 2)
        else:
            # 如果没有吃到食物且不需要增长，移除尾部
            if self.snake.grow_count > 0:
                self.snake.grow_count -= 1
            else:
                self.snake.segments.pop()
    
    def _check_collision(self, head: Position) -> bool:
        """
        检查是否发生碰撞
        
        Args:
            head: 蛇头位置
        
        Returns:
            bool: 是否碰撞
        """
        # 撞墙检测 - 上边界
        if head.y < 0:
            return True
        # 撞墙检测 - 下边界
        if head.y >= self.config.GRID_ROWS:
            return True
        # 撞墙检测 - 左边界
        if head.x < 0:
            return True
        # 撞墙检测 - 右边界
        if head.x >= self.config.GRID_COLS:
            return True
        
        # 撞自身检测
        if head in self.snake.segments:
            return True
        
        return False
    
    def handle_events(self):
        """处理pygame事件"""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                # 处理控制器事件
                self.controller.handle_event(event)
                
                # 游戏结束后按空格键重新开始
                if event.key == pygame.K_SPACE and self.controller.is_game_over():
                    self._restart_game()
                # 游戏未开始时按空格键开始
                elif event.key == pygame.K_SPACE and self.controller.is_game_stopped():
                    self._start_game()
    
    def _start_game(self):
        """开始游戏"""
        self.controller.game_state = GameState.RUNNING
    
    def _restart_game(self):
        """重新开始游戏"""
        self._init_game_objects()
    
    def update(self, delta_time: int):
        """
        更新游戏状态
        
        Args:
            delta_time: 距上一帧的毫秒数
        """
        if not self.controller.is_game_running():
            return
        
        self.move_timer += delta_time
        if self.move_timer >= self.game_speed:
            self.move_timer = 0
            self._move_snake()
    
    def render(self):
        """渲染游戏画面"""
        if self.snake is None or self.food is None:
            return
        
        # 更新得分板
        self.renderer.set_score(
            self.controller.score,
            self.controller.high_score
        )
        
        # 渲染游戏画面
        self.renderer.render(
            snake=self.snake.segments,
            food_position=self.food,
            game_over=self.controller.is_game_over(),
            is_paused=self.controller.is_game_paused()
        )
    
    def run(self):
        """运行游戏主循环"""
        last_time = pygame.time.get_ticks()
        
        while self.running:
            current_time = pygame.time.get_ticks()
            delta_time = current_time - last_time
            last_time = current_time
            
            # 限制帧率（最高60fps）
            self.clock.tick(60)
            
            # 处理事件
            self.handle_events()
            
            # 更新游戏状态
            self.update(delta_time)
            
            # 渲染画面
            self.render()
            
            # 更新显示
            pygame.display.flip()
            
            # 处理暂停状态
            if self.controller.is_game_paused():
                # 降低暂停时的刷新率以节省资源
                self.clock.tick(10)
    
    def quit(self):
        """退出游戏"""
        pygame.quit()
        sys.exit()


def main():
    """游戏主函数"""
    game = SnakeGame()
    try:
        game.run()
    finally:
        game.quit()


if __name__ == "__main__":
    main()
