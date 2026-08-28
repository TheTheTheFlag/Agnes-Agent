"""
贪吃蛇游戏 - 渲染模块
功能：负责游戏的视觉渲染，包括蛇、食物、得分板、画布等
"""

import pygame
from typing import List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class Direction(Enum):
    """移动方向枚举"""
    UP = (0, -1)
    DOWN = (0, 1)
    LEFT = (-1, 0)
    RIGHT = (1, 0)


@dataclass
class Position:
    """位置数据结构"""
    x: int
    y: int
    
    def __eq__(self, other):
        if not isinstance(other, Position):
            return False
        return self.x == other.x and self.y == other.y
    
    def __hash__(self):
        return hash((self.x, self.y))
    
    def __repr__(self):
        return f"Position({self.x}, {self.y})"


@dataclass
class GameConfig:
    """游戏配置"""
    # 屏幕尺寸
    SCREEN_WIDTH: int = 800
    SCREEN_HEIGHT: int = 600
    
    # 网格配置
    GRID_COLS: int = 20
    GRID_ROWS: int = 15
    
    # 单元格尺寸
    CELL_SIZE: int = 40
    
    # 画布位置（居中）
    CANVAS_OFFSET_X: int = 100
    CANVAS_OFFSET_Y: int = 50
    
    # 计算画布尺寸
    @property
    def CANVAS_WIDTH(self) -> int:
        return self.GRID_COLS * self.CELL_SIZE
    
    @property
    def CANVAS_HEIGHT(self) -> int:
        return self.GRID_ROWS * self.CELL_SIZE
    
    # 颜色配置
    COLOR_BACKGROUND: Tuple[int, int, int] = (30, 30, 30)
    COLOR_GRID: Tuple[int, int, int] = (40, 40, 40)
    COLOR_SNAKE_HEAD: Tuple[int, int, int] = (0, 255, 0)
    COLOR_SNAKE_BODY: Tuple[int, int, int] = (0, 200, 0)
    COLOR_FOOD: Tuple[int, int, int] = (255, 0, 0)
    COLOR_TEXT: Tuple[int, int, int] = (255, 255, 255)
    COLOR_BORDER: Tuple[int, int, int] = (100, 100, 100)
    COLOR_GAME_OVER: Tuple[int, int, int] = (255, 50, 50)


class SnakeRenderer:
    """蛇的渲染器"""
    
    def __init__(self, screen: pygame.Surface, config: GameConfig):
        self.screen = screen
        self.config = config
        self.font = pygame.font.Font(None, 36)
    
    def render(self, segments: List[Position], direction: Direction):
        """渲染蛇身和蛇头"""
        head_color = self.config.COLOR_SNAKE_HEAD
        body_color = self.config.COLOR_SNAKE_BODY
        
        for i, segment in enumerate(segments):
            # 计算像素位置
            x = self.config.CANVAS_OFFSET_X + segment.x * self.config.CELL_SIZE
            y = self.config.CANVAS_OFFSET_Y + segment.y * self.config.CELL_SIZE
            
            # 蛇头用不同颜色
            color = head_color if i == 0 else body_color
            
            # 绘制蛇身（带圆角效果）
            rect = pygame.Rect(x + 1, y + 1, self.config.CELL_SIZE - 2, self.config.CELL_SIZE - 2)
            pygame.draw.rect(self.screen, color, rect, border_radius=5)
            
            # 如果是蛇头，绘制眼睛
            if i == 0:
                self._draw_eyes(x, y, direction)
    
    def _draw_eyes(self, x: int, y: int, direction: Direction):
        """绘制蛇的眼睛"""
        eye_size = 6
        pupil_size = 3
        
        # 根据方向调整眼睛位置
        if direction == Direction.RIGHT or direction == Direction.LEFT:
            eye1_pos = (x + 10, y + 10)
            eye2_pos = (x + 10, y + 24)
        else:  # UP or DOWN
            eye1_pos = (x + 10, y + 10)
            eye2_pos = (x + 24, y + 10)
        
        # 绘制眼白
        for eye_pos in [eye1_pos, eye2_pos]:
            pygame.draw.circle(self.screen, (255, 255, 255), eye_pos, eye_size)
            # 绘制瞳孔
            pygame.draw.circle(self.screen, (0, 0, 0), eye_pos, pupil_size)


class FoodRenderer:
    """食物的渲染器"""
    
    def __init__(self, screen: pygame.Surface, config: GameConfig):
        self.screen = screen
        self.config = config
        self.pulse_timer = 0
    
    def render(self, position: Position):
        """渲染食物"""
        x = self.config.CANVAS_OFFSET_X + position.x * self.config.CELL_SIZE
        y = self.config.CANVAS_OFFSET_Y + position.y * self.config.CELL_SIZE
        
        # 计算食物半径（带脉动效果）
        base_radius = self.config.CELL_SIZE // 2 - 2
        pulse = int(2 * pygame.time.get_ticks() % 500 / 250)  # 0-2的脉动
        radius = base_radius - pulse
        
        # 绘制食物（圆形）
        center = (x + self.config.CELL_SIZE // 2, y + self.config.CELL_SIZE // 2)
        pygame.draw.circle(self.screen, self.config.COLOR_FOOD, center, radius)
        
        # 添加高光效果
        highlight_offset = radius // 3
        pygame.draw.circle(self.screen, (255, 150, 150), 
                          (center[0] - highlight_offset, center[1] - highlight_offset),
                          radius // 3)


class Scoreboard:
    """得分板"""
    
    def __init__(self, screen: pygame.Surface, config: GameConfig):
        self.screen = screen
        self.config = config
        self.score_font = pygame.font.Font(None, 48)
        self.high_score_font = pygame.font.Font(None, 36)
        self.info_font = pygame.font.Font(None, 28)
    
    def render(self, score: int, high_score: int):
        """渲染得分板"""
        # 背景面板
        panel_rect = pygame.Rect(self.config.SCREEN_WIDTH - 180, 10, 170, 100)
        pygame.draw.rect(self.screen, (50, 50, 50), panel_rect, border_radius=10)
        pygame.draw.rect(self.screen, self.config.COLOR_BORDER, panel_rect, 2, border_radius=10)
        
        # 当前得分
        score_text = self.score_font.render(f"得分: {score}", True, self.config.COLOR_TEXT)
        self.screen.blit(score_text, (self.config.SCREEN_WIDTH - 160, 25))
        
        # 最高得分
        high_text = self.high_score_font.render(f"最高: {high_score}", True, self.config.COLOR_TEXT)
        self.screen.blit(high_text, (self.config.SCREEN_WIDTH - 160, 70))
    
    def render_controls(self):
        """渲染控制说明"""
        controls_y = self.config.SCREEN_HEIGHT - 60
        control_text = self.info_font.render("方向键/WASD:移动 | 空格:暂停 | ESC:退出", 
                                           True, self.config.COLOR_TEXT)
        self.screen.blit(control_text, (10, controls_y))


class CanvasRenderer:
    """画布渲染器 - 整合所有渲染组件"""
    
    def __init__(self, screen: pygame.Surface, config: GameConfig):
        self.screen = screen
        self.config = config
        self.snake_renderer = SnakeRenderer(screen, config)
        self.food_renderer = FoodRenderer(screen, config)
        self.scoreboard = Scoreboard(screen, config)
        self.current_score = 0
        self.current_high_score = 0
    
    def set_score(self, score: int, high_score: int):
        """设置当前得分"""
        self.current_score = score
        self.current_high_score = high_score
    
    def render(self, snake: List[Position], food_position: Position, 
               game_over: bool = False, is_paused: bool = False):
        """渲染整个游戏画面"""
        # 清空屏幕
        self.screen.fill(self.config.COLOR_BACKGROUND)
        
        # 绘制网格背景
        self._draw_grid()
        
        # 绘制边框
        self._draw_border()
        
        # 绘制游戏元素
        if snake and food_position:
            self.snake_renderer.render(snake, Direction.RIGHT)
            self.food_renderer.render(food_position)
        
        # 绘制得分板
        self.scoreboard.render(self.current_score, self.current_high_score)
        
        # 绘制游戏状态覆盖层
        if game_over:
            self._draw_game_over()
        elif is_paused:
            self._draw_paused()
        
        # 绘制控制说明
        self.scoreboard.render_controls()
    
    def _draw_grid(self):
        """绘制网格背景"""
        for x in range(self.config.GRID_COLS + 1):
            px = self.config.CANVAS_OFFSET_X + x * self.config.CELL_SIZE
            pygame.draw.line(self.screen, self.config.COLOR_GRID,
                           (px, self.config.CANVAS_OFFSET_Y),
                           (px, self.config.CANVAS_OFFSET_Y + self.config.CANVAS_HEIGHT))
        
        for y in range(self.config.GRID_ROWS + 1):
            py = self.config.CANVAS_OFFSET_Y + y * self.config.CELL_SIZE
            pygame.draw.line(self.screen, self.config.COLOR_GRID,
                           (self.config.CANVAS_OFFSET_X, py),
                           (self.config.CANVAS_OFFSET_X + self.config.CANVAS_WIDTH, py))
    
    def _draw_border(self):
        """绘制游戏区域边框"""
        border_rect = pygame.Rect(
            self.config.CANVAS_OFFSET_X - 2,
            self.config.CANVAS_OFFSET_Y - 2,
            self.config.CANVAS_WIDTH + 4,
            self.config.CANVAS_HEIGHT + 4
        )
        pygame.draw.rect(self.screen, self.config.COLOR_BORDER, border_rect, 2)
    
    def _draw_game_over(self):
        """绘制游戏结束界面"""
        # 半透明遮罩
        overlay = pygame.Surface((self.config.CANVAS_WIDTH, self.config.CANVAS_HEIGHT))
        overlay.set_alpha(150)
        overlay.fill((0, 0, 0))
        self.screen.blit(overlay, (self.config.CANVAS_OFFSET_X, self.config.CANVAS_OFFSET_Y))
        
        # 游戏结束文字
        font = pygame.font.Font(None, 72)
        text = font.render("GAME OVER", True, self.config.COLOR_GAME_OVER)
        text_rect = text.get_rect(center=(self.config.CANVAS_OFFSET_X + self.config.CANVAS_WIDTH // 2,
                                        self.config.CANVAS_OFFSET_Y + self.config.CANVAS_HEIGHT // 2 - 30))
        self.screen.blit(text, text_rect)
        
        # 得分信息
        score_font = pygame.font.Font(None, 36)
        score_text = score_font.render(f"最终得分: {self.current_score}", True, self.config.COLOR_TEXT)
        score_rect = score_text.get_rect(center=(self.config.CANVAS_OFFSET_X + self.config.CANVAS_WIDTH // 2,
                                               self.config.CANVAS_OFFSET_Y + self.config.CANVAS_HEIGHT // 2 + 30))
        self.screen.blit(score_text, score_rect)
        
        # 提示重新开始
        hint_font = pygame.font.Font(None, 28)
        hint_text = hint_font.render("按空格键重新开始", True, self.config.COLOR_TEXT)
        hint_rect = hint_text.get_rect(center=(self.config.CANVAS_OFFSET_X + self.config.CANVAS_WIDTH // 2,
                                              self.config.CANVAS_OFFSET_Y + self.config.CANVAS_HEIGHT // 2 + 80))
        self.screen.blit(hint_text, hint_rect)
    
    def _draw_paused(self):
        """绘制暂停界面"""
        # 半透明遮罩
        overlay = pygame.Surface((self.config.CANVAS_WIDTH, self.config.CANVAS_HEIGHT))
        overlay.set_alpha(100)
        overlay.fill((0, 0, 0))
        self.screen.blit(overlay, (self.config.CANVAS_OFFSET_X, self.config.CANVAS_OFFSET_Y))
        
        # 暂停文字
        font = pygame.font.Font(None, 72)
        text = font.render("PAUSED", True, self.config.COLOR_TEXT)
        text_rect = text.get_rect(center=(self.config.CANVAS_OFFSET_X + self.config.CANVAS_WIDTH // 2,
                                        self.config.CANVAS_OFFSET_Y + self.config.CANVAS_HEIGHT // 2))
        self.screen.blit(text, text_rect)


def create_game_screen() -> Tuple[pygame.Surface, GameConfig]:
    """创建游戏屏幕和配置"""
    pygame.init()
    config = GameConfig()
    screen = pygame.display.set_mode((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
    pygame.display.set_caption("贪吃蛇游戏")
    return screen, config


def main():
    """测试渲染模块"""
    screen, config = create_game_screen()
    renderer = CanvasRenderer(screen, config)
    
    # 测试数据
    test_snake = [Position(10, 7), Position(9, 7), Position(8, 7)]
    test_food = Position(15, 7)
    
    running = True
    clock = pygame.time.Clock()
    
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
        
        renderer.render(test_snake, test_food)
        pygame.display.flip()
        clock.tick(60)
    
    pygame.quit()


if __name__ == "__main__":
    main()
