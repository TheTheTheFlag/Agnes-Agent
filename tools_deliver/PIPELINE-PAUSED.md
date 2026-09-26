# 流水线已暂停（用户要求） 2026-09-26 09:54:11

- [x] 视频生成：暂停（run_episode_videos.py 有 video-pause.flag 门控；已产 E1 前 10 段 mp4）
- [x] 分镜任务：暂停（驱动与 deliver_task 均已停止，E3 fix#3（09:52 投）被杀）
- [x] 出图：暂停（当前无出图任务在跑）

## 恢复方式
1. 删除暂停标志：rm tools_deliver/video-pause.flag
2. 重启驱动：cd /home/mirror/Agnes-Agent && setsid nohup bash tools_deliver/pipeline_storyboard.sh 1 60 > logs/tasks/pipeline-storyboard6.nohup 2>&1 < /dev/null &
   （重启会对已生成集跳过/重校验：E1 绿跳过；E2 FAILED 会重新首投；E3 重新 validate 续跑）

## 停点现场（便于恢复与质量分析）
- 分镜 validate 绿：1 集（E1）
- E2：3 轮修复未过（残留 8 段/46 切真实内容，可作重试基础），积压原因=陈旧 FINAL 秒回浪费 2 轮修复（驱动已修：dispatch_wait 投递前清空日志）
- E3：17 门仅差「节拍恰好一次」（重复认领 46 拍），fix#3 被杀
- 分镜图：48 张（E1 全齐）；视频：E1 前 10 段 mp4
- 视频质量差待用户下一步指示
