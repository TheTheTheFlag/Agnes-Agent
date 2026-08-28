@echo off
rem ============================================
rem  像素飙车 Pixel Racer - Windows 一键启动
rem  双击本文件即可用默认浏览器打开游戏
rem ============================================
cd /d "%~dp0"
if exist "pixel-racer.html" (
    start "" "pixel-racer.html"
) else (
    echo [错误] 未找到 pixel-racer.html，请确认与启动脚本放在同一目录。
    pause
)
exit
