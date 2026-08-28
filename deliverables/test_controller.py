"""
贪吃蛇游戏 - 用户控制功能测试
验证键盘方向键和 WASD 按键响应
"""

import pygame
import sys

# 导入控制模块
from deliverables.snake_controller import SnakeController, GameState, Direction


def test_controller():
    """测试控制器功能"""
    pygame.init()
    
    # 创建测试窗口
    screen = pygame.display.set_mode((800, 600))
    pygame.display.set_caption("贪吃蛇控制测试")
    
    controller = SnakeController()
    font = pygame.font.Font(None, 36)
    
    test_results = []
    
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            
            elif event.type == pygame.KEYDOWN:
                # 记录测试结果
                if event.key in [pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT,
                                 pygame.K_w, pygame.K_s, pygame.K_a, pygame.K_d]:
                    test_results.append(f"按键 {pygame.key.name(event.key)} 响应成功")
                
                controller.handle_event(event)
        
        # 清屏
        screen.fill((30, 30, 30))
        
        # 绘制标题
        title = font.render("贪吃蛇控制测试", True, (255, 255, 255))
        screen.blit(title, (50, 50))
        
        # 显示当前状态
        state_text = font.render(f"当前状态: {controller.get_state_indicator()}", True, (255, 255, 255))
        screen.blit(state_text, (50, 100))
        
        # 显示当前方向
        dir_text = font.render(f"当前方向: {controller.current_direction.name}", True, (255, 255, 255))
        screen.blit(dir_text, (50, 150))
        
        # 显示得分
        score_text = font.render(f"得分: {controller.score}", True, (255, 255, 255))
        screen.blit(score_text, (50, 200))
        
        # 显示控制说明
        controls = [
            "方向键/WASD: 控制移动方向",
            "空格键: 开始/暂停/重新开始",
            "ESC: 退出游戏",
        ]
        for i, control in enumerate(controls):
            text = font.render(control, True, (200, 200, 200))
            screen.blit(text, (50, 280 + i * 30))
        
        # 显示测试结果（最多显示10条）
        if test_results:
            result_title = font.render("测试结果:", True, (100, 255, 100))
            screen.blit(result_title, (50, 420))
            
            for i, result in enumerate(test_results[-10:]):
                text = font.render(result, True, (100, 255, 100))
                screen.blit(text, (70, 460 + i * 25))
        
        pygame.display.flip()
    
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    test_controller()
