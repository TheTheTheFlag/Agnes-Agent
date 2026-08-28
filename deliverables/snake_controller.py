"""
贪吃蛇游戏 - 用户控制模块（完善版）
功能：键盘方向键和 WASD 按键响应，支持开始/暂停/重置/重新开始
"""

import pygame
from typing import Optional
from enum import Enum

from deliverables.snake_renderer import Direction, Position


class GameState(Enum):
    """游戏状态枚举"""
    STOPPED = "stopped"      # 未开始
    RUNNING = "running"      # 运行中
    PAUSED = "paused"        # 暂停
    GAME_OVER = "game_over"  # 游戏结束


class SnakeController:
    """
    贪吃蛇控制器
    处理键盘输入，控制蛇的移动方向和游戏状态
    """

    # 方向键映射 - 支持方向键和 WASD
    DIRECTION_KEYS = {
        pygame.K_UP: Direction.UP,
        pygame.K_DOWN: Direction.DOWN,
        pygame.K_LEFT: Direction.LEFT,
        pygame.K_RIGHT: Direction.RIGHT,
        pygame.K_w: Direction.UP,
        pygame.K_s: Direction.DOWN,
        pygame.K_a: Direction.LEFT,
        pygame.K_d: Direction.RIGHT,
        pygame.K_W: Direction.UP,
        pygame.K_S: Direction.DOWN,
        pygame.K_A: Direction.LEFT,
        pygame.K_D: Direction.RIGHT,
    }

    def __init__(self):
        self.current_direction: Direction = Direction.RIGHT
        self.next_direction: Optional[Direction] = None
        self.game_state: GameState = GameState.STOPPED
        self.score: int = 0
        self.high_score: int = 0

    def handle_event(self, event: pygame.event.Event) -> bool:
        """
        处理pygame事件

        Args:
            event: pygame事件对象

        Returns:
            bool: 是否消耗了该事件
        """
        if event.type != pygame.KEYDOWN:
            return False

        # ESC键退出游戏
        if event.key == pygame.K_ESCAPE:
            self.game_state = GameState.STOPPED
            return True

        # 空格键：开始/暂停/重置/重新开始
        if event.key == pygame.K_SPACE:
            self._handle_space_key()
            return True

        # 方向键：改变移动方向（支持方向键和WASD）
        if event.key in self.DIRECTION_KEYS:
            new_direction = self.DIRECTION_KEYS[event.key]
            self._change_direction(new_direction)
            return True

        return False

    def _handle_space_key(self):
        """处理空格键：根据当前状态开始/暂停/重置/重新开始"""
        if self.game_state == GameState.STOPPED:
            self._start_game()
        elif self.game_state == GameState.RUNNING:
            self._pause_game()
        elif self.game_state == GameState.PAUSED:
            self._resume_game()
        elif self.game_state == GameState.GAME_OVER:
            self._reset_game()

    def _start_game(self):
        """开始游戏"""
        self.game_state = GameState.RUNNING
        self.current_direction = Direction.RIGHT
        self.next_direction = None
        self.score = 0

    def _pause_game(self):
        """暂停游戏"""
        self.game_state = GameState.PAUSED

    def _resume_game(self):
        """恢复游戏"""
        self.game_state = GameState.RUNNING

    def _reset_game(self):
        """重置游戏（游戏结束后重新开始）"""
        self.game_state = GameState.STOPPED
        self.current_direction = Direction.RIGHT
        self.next_direction = None
        self.score = 0

    def _change_direction(self, new_direction: Direction):
        """
        改变蛇的移动方向

        Args:
            new_direction: 新的移动方向
        """
        # 防止180度转向（不能直接反向）
        opposite_directions = {
            Direction.UP: Direction.DOWN,
            Direction.DOWN: Direction.UP,
            Direction.LEFT: Direction.RIGHT,
            Direction.RIGHT: Direction.LEFT,
        }

        if new_direction != opposite_directions.get(self.current_direction):
            self.next_direction = new_direction

    def update_direction(self):
        """更新当前方向（在每帧游戏逻辑更新时调用）"""
        if self.next_direction is not None:
            self.current_direction = self.next_direction
            self.next_direction = None

    def get_current_direction(self) -> Direction:
        """获取当前移动方向"""
        return self.current_direction

    def get_next_direction(self) -> Optional[Direction]:
        """获取待应用的下一方向"""
        return self.next_direction

    def increase_score(self, points: int = 10):
        """增加得分"""
        self.score += points
        if self.score > self.high_score:
            self.high_score = self.score

    def reset_score(self):
        """重置得分"""
        self.score = 0

    def set_high_score(self, score: int):
        """设置最高分"""
        self.high_score = score

    def get_state_indicator(self) -> str:
        """获取游戏状态指示文字"""
        states = {
            GameState.STOPPED: "按空格键开始",
            GameState.RUNNING: "游戏中",
            GameState.PAUSED: "已暂停",
            GameState.GAME_OVER: "游戏结束 - 按空格重新开始",
        }
        return states.get(self.game_state, "")

    def is_game_running(self) -> bool:
        """检查游戏是否正在运行"""
        return self.game_state == GameState.RUNNING

    def is_game_paused(self) -> bool:
        """检查游戏是否暂停"""
        return self.game_state == GameState.PAUSED

    def is_game_over(self) -> bool:
        """检查游戏是否结束"""
        return self.game_state == GameState.GAME_OVER

    def is_game_stopped(self) -> bool:
        """检查游戏是否未开始"""
        return self.game_state == GameState.STOPPED


class GameControlsDisplay:
    """
    游戏控制显示辅助类
    用于在游戏界面上显示当前控制状态
    """

    CONTROLS_INFO = [
        ("方向键/WASD", "控制移动方向"),
        ("空格键", "开始/暂停/重新开始"),
        ("ESC", "退出游戏"),
    ]

    @classmethod
    def get_controls_text(cls) -> list:
        """获取控制说明文字列表"""
        return cls.CONTROLS_INFO.copy()

    @classmethod
    def format_control_line(cls, key: str, action: str) -> str:
        """格式化控制说明文字"""
        return f"{key}: {action}"

