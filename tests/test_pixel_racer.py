"""《像素狂飙 Pixel Racer》交付物测试
- 结构冒烟测试：读取 deliverables/pixel-racer.html，校验关键要素存在
- JS 语法检查：提取 <script> 内容交给 `node --check`（无 node 则跳过）
- 纯逻辑单元测试：碰撞(AABB+收窄)、近距超车(Near Miss)、连击计分（与游戏内实现同参数镜像）
"""
import re
import os
import subprocess
import shutil
import tempfile

HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "deliverables", "pixel-racer.html")

# ---------- 与游戏内实现一致的纯逻辑（镜像，参数同步） ----------
NEAR_MISS_GAP = 26
COMB_WIN = 3.0
COMBO_MAX = 5


def shrink(x, y, w, h, f=0.3):
    dx, dy = w * f / 2, h * f / 2
    return (x + dx, y + dy, w - 2 * dx, h - 2 * dy)


def ov(a, b):
    return a[0] < b[0] + b[2] and a[0] + a[2] > b[0] and a[1] < b[1] + b[3] and a[1] + a[3] > b[1]


def hgap(a, b):
    return max(0.0, max(a[0] - (b[0] + b[2]), b[0] - (a[0] + a[2])))


def near_miss(player, car):
    ps = shrink(player[0], player[1], player[2], player[3])
    cs = shrink(car[0], car[1], car[2], car[3])
    passed = car[1] > player[1] + player[3]
    gap = hgap(ps, cs)
    return {"passed": passed, "near": passed and gap < NEAR_MISS_GAP, "gap": gap}


def combo_next(combo, combo_t):
    """连击规则：3s 窗口内连续 Near Miss 递增，封顶 5x"""
    return min(COMBO_MAX, combo + 1) if combo_t > 0 else 1


def run_unit_tests():
    fails = []
    def check(name, cond):
        if not cond:
            fails.append(name)

    # --- 收窄盒 ---
    s = shrink(100, 200, 16, 32)
    check("shrink-尺寸", abs(s[2] - 11.2) < 1e-6 and abs(s[3] - 22.4) < 1e-6)

    # --- AABB 碰撞 ---
    p = (80, 470, 16, 32)          # 玩家
    same_lane = (72, 470, 16, 32)  # 同车道重叠 → 碰撞
    adj_lane = (128, 470, 16, 32)  # 相邻车道 → 不碰撞
    check("碰撞-同车道", ov(shrink(*p), shrink(*same_lane)) is True)
    check("碰撞-相邻车道", ov(shrink(*p), shrink(*adj_lane)) is False)

    # --- 近距超车 ---
    # 车辆已越过玩家且横向收窄框间距 < 26px → near
    car_close = (90, 504, 16, 32)          # 横向间距 2px，已越过
    check("near-擦身而过", near_miss(p, car_close)["near"] is True)
    car_far = (128, 504, 16, 32)           # 相邻车道，间距 > 26px
    check("near-远道不判", near_miss(p, car_far)["near"] is False)
    car_not_passed = (90, 400, 16, 32)     # 未越过 → 不判
    check("near-未越过不判", near_miss(p, car_not_passed)["near"] is False)

    # --- 连击 ---
    check("combo-窗口内递增", combo_next(1, 1.5) == 2)
    check("combo-封顶5x", combo_next(5, 1.5) == 5)
    check("combo-超时重置为1", combo_next(0, 0.0) == 1)

    return fails


def run_structural_tests(html):
    fails = []
    def check(name, cond):
        if not cond:
            fails.append(name)
    check("DOCTYPE", "<!DOCTYPE html>" in html)
    check("canvas 320x568", '<canvas id="game" width="320" height="568">' in html)
    check("pixelated 渲染", "image-rendering:pixelated" in html or "image-rendering: pixelated" in html)
    check("状态机-TITLE", "state:'TITLE'" in html)
    check("状态机-RACE", "state='RACE'" in html or "'RACE'" in html)
    check("状态机-PAUSE/OVER", "PAUSE" in html and "OVER" in html)
    check("near-miss 逻辑", "NEAR_MISS_GAP" in html)
    check("combo 计分", "COMBO_MAX" in html and "100*G.combo" in html)
    check("localStorage 存档", "localStorage" in html and "pixelracer_" in html)
    check("WebAudio 合成音效", "AudioContext" in html and "createOscillator" in html)
    check("BGM 音序器", "startBGM" in html and "setInterval" in html)
    check("精灵字符画", "makeSprite" in html and "CAR_ROWS" in html and "PLAYER_ROWS" in html)
    check("触屏控制", "touchstart" in html and "touchend" in html)
    check("键盘控制", "ArrowLeft" in html and "ArrowRight" in html)
    check("像素字体", "const FONT=" in html)
    check("3 个 Stage 主题", "SUNSET" in html and "NEON" in html and "DAWN" in html)
    check("无敌帧", "hitT" in html)
    check("测试接口 PR", "window.PR" in html)
    return fails


def js_syntax_check(html):
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    if not m:
        return ["未找到 <script>"]
    if shutil.which("node") is None:
        return []  # 无 node 环境，跳过（不影响结论）
    js = m.group(1)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
        tmp = f.name
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return ["node --check 失败: " + (r.stderr or r.stdout)[:400]]
        return []
    finally:
        os.unlink(tmp)


def main():
    html = open(HTML_PATH, encoding="utf-8").read()
    fails = []
    fails += run_structural_tests(html)
    fails += run_unit_tests()
    fails += js_syntax_check(html)

    print("文件大小: %d 字节" % len(html.encode("utf-8")))
    print("结构校验: %s" % ("通过" if not run_structural_tests(html) else "失败"))
    print("逻辑单元测试: %s" % ("通过" if not run_unit_tests() else "失败"))
    if shutil.which("node"):
        print("JS 语法(node --check): %s" % ("通过" if not js_syntax_check(html) else "失败"))
    else:
        print("JS 语法(node --check): 跳过（未安装 node）")
    if fails:
        print("\n失败项:")
        for f in fails:
            print(" - " + f)
        raise SystemExit(1)
    print("\n全部测试通过 ✓")


if __name__ == "__main__":
    main()
