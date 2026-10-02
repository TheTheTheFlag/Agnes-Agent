#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_kzqs_videos.py — 段视频渲染驱动 · reference 模式（多图参考）。

依据 short-drama-agnes-sop/SKILL.md 阶段四：短剧段视频用
`agnes-video-2.5-flash` 的 **reference 模式**，参考图 **1~5 张**，prompt 里用
`<Picture N>` 指代。refs 经 `--extra '{"refs":[...]}'` 传入。

关于「多人物同场」：槽位预算是 5，而一段里常有 2~3 个角色。数据侧已经解决了这件
事——`投喂稿/kf_plan*.json` 每段的 `refs` 就是**逐段差异化编排好**的组合，每角色
只占一格（`kf_refs/ref-*.png` 是精修过的单人基准图，不是四视图），并带
`ref_roles` 说明每张图「管什么、不管什么」。所以本驱动不猜、不重排、不合成板，
只忠实执行 plan 的 refs；refs 为空时才用纯文本模式（reference 模式允许 1~5 张，
0 张也能跑，只是失去一致性锚点）。
（历史上 `assets/characters/` 里每角色存 4 张视图，那是出图阶段的中间产物，
参考图走 `kf_refs/` + 场景母版，两者不要混用。）

kf2 分镜构图图：**可选**，有就作为构图槽接在 refs 之后（总数 ≤5）。实测晨光
30 段里只有 1 段有 kf2，所以它不能当渲影门禁。

轮询与重试：`media.py` 提交后自行轮询，后端失败会自动重建重试（最多 30 次），
`--timeout 356400` 与之一致。因此本地只要求「提交成功」，不再自造并发上限
（--workers 可选，默认全量并发）。

用法:
  python3 gen_kzqs_videos.py <manifest.json>                 # 跑本集全部未完成段
  python3 gen_kzqs_videos.py <manifest.json> E01-03          # 只跑某段
  python3 gen_kzqs_videos.py <manifest.json> --from E02-03   # 从某段跑到本集末
  python3 gen_kzqs_videos.py <manifest.json> --force         # 已完成也重渲
  python3 gen_kzqs_videos.py <manifest.json> --dry-run       # 只打印将提交的请求
  python3 gen_kzqs_videos.py <manifest.json> --workers 4     # 覆盖并发（默认不限）
"""
import argparse
import concurrent.futures as futures
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time

MEDIA = "/home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py"
TOKEN_PATH = "/home/mirror/Agnes-Agent/data/internal_token"
VIDEO_MODEL = "agnes-video-2.5-flash"     # reference 模式只此模型支持
MAX_REFS = 5                             # reference 模式硬上限（short-drama-agnes-sop）
VIDEO_TIMEOUT = 356400                   # 与后端 30 次自动重试对齐，不要调小
DUR_OK = (3, 5, 10, 18)


# ---------------------------------------------------------------- media 复用
_media_mod = None


def media_mod():
    """import media.py 以复用其 _api_upload_ref（保持与创作台同一上传通道）。"""
    global _media_mod
    if _media_mod is None:
        spec = importlib.util.spec_from_file_location("agnes_media", MEDIA)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _media_mod = mod
    return _media_mod


def upload_refs(paths):
    """本地参考图 → uploads/<name>。任一张缺失即抛错（不静默丢图）。"""
    up = []
    for p in paths:
        if not os.path.isfile(p):
            raise FileNotFoundError("参考图不存在: %s" % p)
        up.append(media_mod()._api_upload_ref(p))
    return up


# ---------------------------------------------------------------- 计划与段序
def _seg_order(pdir):
    """读 kf_plan*.json 得到全片段序（跨集）。无则按 EXX-NN 推算。"""
    order = []
    for kp in _plan_files(pdir):
        for s in _plan_segs(kp):
            seg = s.get("seg")
            if seg and seg not in order:
                order.append(seg)
    if order:
        return order
    fdir = os.path.join(pdir, "投喂稿")
    eps = sorted({m.group(1) for m in
                  (re.match(r"(E\d+)-\d+$", d) for d in _listdir(fdir))
                  if m and os.path.isdir(os.path.join(fdir, d))})
    for ep in eps:
        for x in sorted((d for d in _listdir(os.path.join(fdir, ep))
                         if re.match(rf"^{ep}-\d+$", d)),
                        key=lambda x: int(x.split("-")[1])):
            order.append(x)
    return order


def _listdir(p):
    try:
        return os.listdir(p)
    except OSError:
        return []


def _plan_files(pdir):
    """kf_plan.json + kf_plan_epNN.json（跳过 .bak* 备份与目录）。"""
    d = os.path.join(pdir, "投喂稿")
    out = []
    for f in sorted(_listdir(d)):
        if f.startswith("kf_plan") and f.endswith(".json") and ".bak" not in f:
            out.append(os.path.join(d, f))
    return out


def _plan_segs(path):
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    eps = raw if isinstance(raw, list) else [raw]
    segs = []
    for ep in eps:
        if isinstance(ep, dict):
            segs += [s for s in (ep.get("segments") or []) if isinstance(s, dict)]
    return segs


def seg_plan(pdir, seg):
    """该段在 kf_plan 里的 (refs, ref_roles)。找不到返回 ([], [])。"""
    for kp in _plan_files(pdir):
        for s in _plan_segs(kp):
            if s.get("seg") != seg:
                continue
            refs, roles = [], []
            for r in (s.get("refs") or []):
                fp = r if os.path.isabs(r) else os.path.normpath(
                    os.path.join(os.path.dirname(kp), "..", r))
                if os.path.isfile(fp):
                    refs.append(fp)
                else:
                    print("[warn] %s: refs 缺失 %s" % (seg, r), file=sys.stderr)
            by_path = {str((x or {}).get("path")): x for x in (s.get("ref_roles") or [])
                       if isinstance(x, dict)}
            for i, fp in enumerate(refs, 1):
                meta = by_path.get(fp) or {}
                roles.append({
                    "use": meta.get("use") or ref_role(fp),
                    "controls": str(meta.get("controls") or ""),
                    "not_controls": str(meta.get("not_controls") or ""),
                })
            return refs, roles
    return [], []


def seg_kf2(pdir, seg):
    """本段分镜构图图（尾帧落点）。不存在返回 ""。**可选**，不是渲影必需项。"""
    m = re.match(r"^(E\d+)-(\d+)$", seg or "")
    if not m:
        return ""
    p = os.path.join(pdir, "投喂稿", m.group(1).upper(), seg, "kf2.png")
    return p if os.path.isfile(p) and os.path.getsize(p) > 0 else ""


def build_refs(pdir, seg):
    """组装该段 reference 集。返回 (items, info)。

    items = [{path, use, controls, not_controls, n}]，顺序即 <Picture N> 顺序。
    info = {"refs": n, "kf2": bool, "kf2_dropped": bool, "roles": [...], "labels": [...]}

    组成 = kf_plan.refs（人物/场景基准，人工编排、逐段差异化，优先级最高）
         + 本段 kf2 构图图（**有就加，没有也不影响**——reference 模式只要求 1~5 张）
    槽位满 5 时先丢 kf2，不动人工编排的 refs。
    """
    refs, roles = seg_plan(pdir, seg)
    items = [{"path": p, "use": r.get("use") or ref_role(p),
              "controls": r.get("controls", ""),
              "not_controls": r.get("not_controls", "")}
             for p, r in zip(refs, roles)]
    over = len(items) > MAX_REFS
    if over:
        print("[warn] %s: kf_plan refs %d 张超过上限 %d，多余丢弃"
              % (seg, len(items), MAX_REFS), file=sys.stderr)
        items = items[:MAX_REFS]

    kf2 = seg_kf2(pdir, seg)
    kf2_dropped = False
    if kf2 and kf2 not in [i["path"] for i in items]:
        if len(items) < MAX_REFS:
            items.append({"path": kf2, "use": "本段分镜构图",
                          "controls": "景别、机位、人物站位、构图关系",
                          "not_controls": "人物长相、服装细节"})
        else:
            kf2_dropped = True

    for i, it in enumerate(items, 1):
        it["n"] = i
    info = {"refs": len(items), "kf2": bool(kf2), "kf2_dropped": kf2_dropped,
            "labels": [ref_label(i["path"]) for i in items],
            "uses": [i["use"] for i in items]}
    return items, info


def refs_ready(pdir, seg):
    """reference 集是否就绪。返回 (ready, 说明)。

    就绪 = kf_plan 里该段有 ≥1 张可用 refs（人物形象基准 / 场景母版）。
    **kf2 不是必需** —— 晨光 30 段里仅 1 段有 kf2，把它当门禁会挡掉 29 段。
    """
    items, info = build_refs(pdir, seg)
    if not items:
        return False, "kf_plan 无可用 refs（该段未编排人物/场景基准图）"
    note = "，含 kf2 构图" if info["kf2"] and not info["kf2_dropped"] else ""
    if info["kf2_dropped"]:
        note = "，refs 已满 5、kf2 未进 ref"
    return True, "refs %d 张%s" % (info["refs"], note)


# ---------------------------------------------------------------- <Picture N>
def ref_label(path):
    """从参考图路径推导人可读标签，供 prompt 的 <Picture N> 指代句使用。"""
    b = os.path.splitext(os.path.basename(path))[0]
    b = re.sub(r"^ref[-_]", "", b)
    return b.replace("_", " ").strip() or "参考图"


def ref_role(path):
    """粗分类，用于把 <Picture N> 说成「人物/场景/构图」基准。"""
    b = os.path.basename(path).lower()
    d = path.replace("\\", "/")
    if b == "kf2.png":
        return "本段分镜构图"
    if "/kf_refs/" in d or b.startswith("ref-"):
        return "人物形象基准"
    if re.search(r"scn|scene|场景", b):
        return "场景美术基准"
    if re.search(r"prop|道具", b):
        return "道具基准"
    return "画面参考"


def picture_sentence(items):
    """SOP 语法：<Picture N> 指代参考图，并用 ref_roles 的 controls/not_controls
    说明每张图「管什么、不管什么」——避免模型把身份图当构图图用。"""
    if not items:
        return ""
    bits = []
    for it in items:
        t = "<Picture %d>=「%s」%s" % (it["n"], ref_label(it["path"]), it["use"])
        if it.get("controls"):
            t += "，管：%s" % it["controls"]
        if it.get("not_controls"):
            t += "；不管：%s" % it["not_controls"]
        bits.append(t)
    return "参考图指代：" + "；".join(bits) + "。请严格按上述分工使用参考图。"


def aspect_of(prompt, override):
    if override:
        return override
    if "9:16" in prompt or "竖屏" in prompt:
        return "9:16"
    return "16:9"


# ---------------------------------------------------------------- 单段渲染
def render_segment(pdir, seg, prompt, dur, args, tok):
    """渲染一段。返回 (状态, 说明)；状态 ∈ ok/fail/skip/dry。"""
    items, info = build_refs(pdir, seg)

    extra = {"aspect_ratio": aspect_of(prompt, args.aspect)}
    cmd = ["python3", MEDIA, "video",
           "--model", VIDEO_MODEL,
           "--mode", "reference",
           "--duration", str(dur),
           "--timeout", str(VIDEO_TIMEOUT),
           "--prompt", prompt]
    tag = "refs=%d" % info["refs"]

    if items:
        try:
            extra["refs"] = upload_refs([i["path"] for i in items])
        except FileNotFoundError as e:
            return "fail", str(e)
        except SystemExit as e:
            return "fail", "上传失败: %s" % e
        tag += " " + " ".join("<Picture %d>%s" % (i["n"], ref_label(i["path"]))
                              for i in items)
        if info["kf2_dropped"]:
            tag += "（refs 已满 5，kf2 构图未进 ref）"
    else:
        tag += "（无参考图，纯文本）"

    cmd += ["--extra", json.dumps(extra, ensure_ascii=False)]

    env = dict(os.environ)
    if tok:
        env["AGENT_INTERNAL_TOKEN"] = tok
    env["AGENT_MEDIA_SOURCE"] = "short-drama-director"

    print("[%s] 提交 %s dur=%ds %s" % (seg, VIDEO_MODEL, dur, tag), flush=True)
    if args.dry_run:
        shown = list(cmd)
        shown[shown.index("--prompt") + 1] = "<PROMPT %d字>" % len(prompt)
        print("    " + " ".join(shown))
        return "dry", ""

    last = ""
    for attempt in range(1, args.retries + 2):
        try:
            pr = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=VIDEO_TIMEOUT + 900, env=env)
        except subprocess.TimeoutExpired:
            last = "本地等待超时(>%ds)，后端可能仍在跑；用 fetch 续查，勿重建" % VIDEO_TIMEOUT
            print("[%s] %s" % (seg, last), file=sys.stderr)
            break
        out = (pr.stdout or "") + (pr.stderr or "")
        fm = re.search(r"FILE=(\S+)", out)
        if pr.returncode == 0 and fm:
            return "ok", _deliver(pdir, seg, fm.group(1).strip(), args.no_audio)
        last = "rc=%s %s" % (pr.returncode, out[-500:].replace("\n", " "))
        if attempt <= args.retries:
            print("[%s] 第 %d 次失败，%ds 后重试（后端自身也会自动重建重试）"
                  % (seg, attempt, args.retry_gap), file=sys.stderr)
            time.sleep(args.retry_gap)
    return "fail", last


def _deliver(pdir, seg, src, no_audio):
    """把产物落到 <项目目录>/<段号>/<段号>.mp4。"""
    dst_dir = os.path.join(pdir, seg)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, seg + ".mp4")
    try:
        shutil.copy(src, dst)
    except Exception as e:
        return "复制失败 %s" % e
    if no_audio:
        tmp = dst + ".muted.mp4"
        try:
            mr = subprocess.run(["ffmpeg", "-y", "-i", dst, "-an", "-c:v", "copy", tmp],
                                capture_output=True, text=True, timeout=600)
            if mr.returncode == 0 and os.path.isfile(tmp):
                os.replace(tmp, dst)
        except Exception:
            for junk in (tmp,):
                if os.path.isfile(junk):
                    os.remove(junk)
    return dst


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("manifest")
    ap.add_argument("seg", nargs="?", default="")
    ap.add_argument("--from", dest="from_seg", default="")
    ap.add_argument("--force", action="store_true", help="已完成段也重渲")
    ap.add_argument("--workers", type=int, default=0, help="并发数；0=全量并发（默认，不设上限）")
    ap.add_argument("--retries", type=int, default=2, help="本地失败重试次数（默认 2）")
    ap.add_argument("--retry-gap", type=int, default=20, help="重试间隔秒（默认 20）")
    ap.add_argument("--aspect", default="", help="aspect_ratio；默认按 prompt 判 16:9/9:16")
    ap.add_argument("--no-audio", action="store_true", help="去掉模型自带音轨")
    ap.add_argument("--dry-run", action="store_true", help="只打印将提交的请求，不烧额度")
    args = ap.parse_args()

    mp = args.manifest
    m = json.load(open(mp, encoding="utf-8"))
    pdir = m.get("project_dir") or os.path.dirname(os.path.abspath(mp))
    logfile = os.path.join(pdir, "video-run.log")

    tok = ""
    try:
        tok = open(TOKEN_PATH, encoding="utf-8").read().strip()
    except Exception:
        pass

    def log(s):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), s)
        print(line, flush=True)
        try:
            with open(logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    order = _seg_order(pdir)
    log("===== gen_kzqs_videos reference 模式 | manifest=%s | only=%s from=%s force=%s ====="
        % (os.path.basename(mp), args.seg or "-", args.from_seg or "-", args.force))

    jobs = []
    for s in m.get("segments", []):
        seg = str(s.get("seg", "")).strip()
        if not seg:
            continue
        if args.seg and seg != args.seg:
            continue
        if args.from_seg and seg in order and args.from_seg in order \
                and order.index(seg) < order.index(args.from_seg):
            continue
        prompt = str(s.get("prompt", "")).strip()
        if not prompt:
            log("%s: 缺 prompt，跳过" % seg)
            continue
        dur = int(s.get("duration") or 5)
        if dur not in DUR_OK:
            log("%s: duration=%s 异常，按 5 处理" % (seg, dur))
            dur = 5
        ready, why = refs_ready(pdir, seg)
        if not ready and not args.force:
            log("%s: reference 集未就绪（%s），跳过（--force 可强提）" % (seg, why))
            continue
        dst = os.path.join(pdir, seg, seg + ".mp4")
        if os.path.isfile(dst) and os.path.getsize(dst) > 0 and not args.force:
            log("%s: 已完成，跳过（--force 可重渲）" % seg)
            continue
        items, _ = build_refs(pdir, seg)
        if items:
            prompt = picture_sentence(items) + prompt
        jobs.append((seg, prompt, dur))

    if not jobs:
        log("===== 无待渲段 =====")
        return 0

    workers = args.workers if args.workers > 0 else len(jobs)
    log("待渲 %d 段，并发 %d（不设硬上限）: %s"
        % (len(jobs), workers, " ".join(j[0] for j in jobs)))

    tally = {"ok": 0, "fail": 0, "dry": 0}

    def work(job):
        seg, prompt, dur = job
        try:
            st, msg = render_segment(pdir, seg, prompt, dur, args, tok)
        except Exception as e:                      # 单段异常不许带崩整批
            return seg, "fail", "未捕获异常 %r" % e
        return seg, st, msg

    with futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for seg, st, msg in ex.map(work, jobs):
            tally[st] = tally.get(st, 0) + 1
            if st == "ok":
                log("%s: OK -> %s" % (seg, msg))
            elif st == "dry":
                pass
            else:
                log("%s: FAIL %s" % (seg, msg))

    log("===== 完成: 成功 %d, 失败 %d, dry %d ====="
        % (tally["ok"], tally["fail"], tally["dry"]))
    return 0 if tally["fail"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())