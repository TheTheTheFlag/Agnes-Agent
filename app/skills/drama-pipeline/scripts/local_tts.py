"""
Local TTS dubbing pipeline using Qwen3-TTS-12Hz-0.6B (CPU inference).
Reads an SRT subtitle file, generates TTS audio per line using local model,
places multi-line dialogue sequentially within each cue, merges into voice_mix.wav.

2026-09-29（《代号：烛龙》实测沉淀，5 条小样本实验）：
- 本模型自然语速：dylan/serena/vivian ≈ 3.7-3.9 字/秒，uncle_fu ≈ 2.9 字/秒；
  分镜台词按 5-6 字/秒预算 → 按设计起点直排会语音互叠（实测 17 cue 超窗、7 处重叠最深 5.4s）。
- --atempo 统一语速补偿（混音时逐行加速、保音高），代号烛龙用 1.25（→ ~4.8 字/秒）。
- 全局防重叠排程：实际起点 = max(设计起点, 上一行实际结束 + 0.15)，保证零重叠；
  密集段个别行顺延（>1s 会打印明细）。
- 产出 voice_mix.srt（按实际排程时间，旁挂交付）。
"""
import argparse
import json
import os
import shutil
import subprocess
import time

import numpy as np
import soundfile as sf
import torch


# Default voice mapping for Chinese drama characters
DEFAULT_VOICES = {
    "祝祝": "serena",
    "蒋蒋": "ryan",
    "同学甲": "eric",
    "旁白": "eric",
    "林深": "ryan",
    "苏夜": "serena",
    "AI「回声」": "eric",
    "默认": "serena",
}

# Model path (default, can be overridden)
DEFAULT_MODEL_PATH = os.environ.get(
    "MODEL_PATH",
    "models/ms/models/Qwen--Qwen3-TTS-12Hz-0.6B-CustomVoice/snapshots/master"
)


def ts(s):
    """Parse SRT timestamp to seconds."""
    h, m, sec = s.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec.replace(",", "."))


def ms(t):
    """Convert seconds to milliseconds."""
    return int(round(t * 1000))


def wav_dur(path):
    """Duration of a wav, read from the real header (no hardcoded sample rate).

    2026-09-28: 原实现按 24000Hz/mono/16bit 反推，若模型换采样率（如 16k）
    时长会算错 1.5 倍，导致后续所有时间码漂移。soundfile 已在顶部 import。
    """
    try:
        return float(sf.info(path).duration)
    except Exception:
        pass
    # 兜底：仅在文件头不可读时用保守估计
    sz = os.path.getsize(path)
    if sz < 44:
        return 0.0
    return (sz - 44) / (24000 * 2)


def parse_srt(path):
    """Parse SRT file into list of cues with texts."""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    # 2026-09-29：CRLF 的 SRT 按 "\n\n" 分块会整体退化成 1 条 cue
    # （Windows 文本模式写出的 SRT 全是 CRLF），统一归一化后再切
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in content.strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0])
        except ValueError:
            continue
        if " --> " not in lines[1]:
            continue
        s, e = lines[1].split(" --> ")
        texts = lines[2:]
        cues.append({"idx": idx, "start": ts(s), "end": ts(e), "texts": texts})
    return cues


def main():
    ap = argparse.ArgumentParser(description="Local TTS dubbing with Qwen3-TTS")
    ap.add_argument("--srt", required=True, help="Path to SRT file")
    ap.add_argument("--out-dir", default=None, help="Output directory (default: same as SRT)")
    ap.add_argument("--model", default=DEFAULT_MODEL_PATH, help="Path to Qwen3-TTS model")
    ap.add_argument("--voice-map", default=None, help="JSON file with voice mapping")
    ap.add_argument("--limit", type=int, default=0, help="Max lines to process (0=all)")
    ap.add_argument("--atempo", type=float, default=1.0,
                    help="统一语速补偿（>1 加快、保音高；代号烛龙实测用 1.25）")
    args = ap.parse_args()

    # Load model
    print(f"Loading model from {args.model}...", flush=True)
    t0 = time.time()
    from qwen_tts import Qwen3TTSModel
    model = Qwen3TTSModel.from_pretrained(args.model, device_map="cpu", dtype=torch.float32)
    print(f"Model loaded in {time.time()-t0:.1f}s", flush=True)

    # Setup output dir
    srt_dir = args.out_dir or os.path.dirname(args.srt)
    os.makedirs(srt_dir, exist_ok=True)

    # Voice map
    voice_map = dict(DEFAULT_VOICES)
    if args.voice_map:
        with open(args.voice_map, encoding="utf-8") as f:
            voice_map.update(json.load(f))
    print(f"Voice map: {json.dumps(voice_map, ensure_ascii=False)}", flush=True)

    # Parse SRT
    cues = parse_srt(args.srt)
    print(f"Parsed {len(cues)} cues from {args.srt}", flush=True)

    # Generate TTS per line
    line_files = []
    total_time = 0
    # 2026-09-28 新增：单行音频最小时长门禁。模型偶发返回 0.05s 空音，
    # 原实现只 catch exception，空音会静默混入 voice_mix。
    MIN_LINE_DUR = 0.15
    for c in cues:
        cursor = c["start"]
        for ti, raw in enumerate(c["texts"]):
            # 段内累计越过 cue end 不再丢行（2026-09-29 改）：丢行会让台词从成片消失，
            # 顺延只让时间码漂移，且会被下面的全局排程统一消解
            if cursor > c["end"] + 1e-6:
                print(f"  WARN cue {c['idx']} 第{ti+1}行设计起点 {cursor:.2f}s "
                      f"已越过 cue 结束 {c['end']:.2f}s，交由全局排程顺延", flush=True)

            if "：" in raw:
                sp, text = raw.split("：", 1)
            else:
                sp, text = "", raw
            # voice-map 值兼容两种写法：字符串（仅音色）或对象 {"voice":..,"instruct":..}
            v = voice_map.get(sp.strip(), voice_map.get("默认", "serena"))
            if isinstance(v, dict):
                voice = v.get("voice", "serena")
                instruct = v.get("instruct")
            else:
                voice, instruct = v, None
            fname = f"{c['idx']:03d}_{ti+1}.wav"
            fpath = os.path.join(srt_dir, fname)

            # Skip if already exists
            if os.path.exists(fpath) and os.path.getsize(fpath) > 1000:
                dur = wav_dur(fpath)
                line_files.append((cursor, fpath, dur, voice, sp, text))
                print(f"  SKIP {fname} ({dur:.2f}s)", flush=True)
                cursor += dur + 0.15
                continue

            print(f"  [{len(line_files)+1}] {sp} ({voice}{'/' + instruct if instruct else ''}): {text[:20]}", flush=True)
            t1 = time.time()
            dur = None          # 关键：每轮重置，失败时不得沿用上一条的残值
            try:
                wavs, sr = model.generate_custom_voice(
                    text=text, language="chinese", speaker=voice,
                    instruct=instruct, non_streaming_mode=True)
                a = np.asarray(wavs[0], dtype=np.float32)
                dur = len(a) / sr
                if dur < MIN_LINE_DUR:
                    raise ValueError(
                        f"生成时长 {dur:.3f}s < {MIN_LINE_DUR}s，判为空音/失败")
                sf.write(fpath, a, sr)
                line_files.append((cursor, fpath, dur, voice, sp, text))
                print(f"    Done: {dur:.2f}s @ {sr}Hz ({time.time()-t1:.1f}s wall)", flush=True)
                total_time += dur
            except Exception as e:
                # 关键：失败时直接 continue。原实现在此之后仍执行
                # `cursor += dur + 0.15`，而 dur 是上一条台词的残值
                # → 后续所有时间码连锁漂移。
                print(f"    ERR: {e}", flush=True)
                continue

            if cursor + dur > c["end"] + 1e-6:
                print(f"  WARN cue {c['idx']} 第{ti+1}行超出 cue 时长 "
                      f"(起始 {cursor:.2f}s + {dur:.2f}s > 结束 {c['end']:.2f}s)", flush=True)

            cursor += dur + 0.15
            if args.limit and len(line_files) >= args.limit:
                break
        if args.limit and len(line_files) >= args.limit:
            break

    if not line_files:
        print("No lines generated.", flush=True)
        return 1

    print(f"Generated {len(line_files)} lines, total audio: {total_time:.1f}s", flush=True)

    # 全局防重叠排程（2026-09-29）：实测按设计起点直排会出现语音互叠（最深 5.4s），
    # 实际起点 = max(设计起点, 上一行实际结束 + 0.15)，保证零重叠；atempo 同步缩时长
    A = args.atempo
    placed = []
    prev_end = -1e9
    for start, path, dur, voice, sp, text in line_files:
        d = dur / A
        st = max(start, prev_end + 0.15)
        placed.append((st, path, d, voice, sp, text))
        prev_end = st + d
    drift = [(st - start, os.path.basename(path))
             for (start, _, _, _, _, _), (st, _, _, _, _, _) in zip(line_files, placed)
             if st - start > 1.0]
    print(f"排程完成：{len(placed)} 行 atempo={A}，语音末尾 {prev_end:.1f}s，"
          f"顺延>1s 共 {len(drift)} 行", flush=True)
    for dd, name in drift:
        print(f"  顺延 {name}: +{dd:.2f}s", flush=True)

    def ss(t):
        return "%02d:%02d:%06.3f" % (int(t // 3600), int((t % 3600) // 60), t % 60)

    srt_out = os.path.join(srt_dir, "voice_mix.srt")
    with open(srt_out, "w", encoding="utf-8") as f:
        for i, (st, path, d, voice, sp, text) in enumerate(placed, 1):
            body = f"{sp}：{text}" if sp else text
            a_, b_ = ss(st).replace(".", ","), ss(st + d).replace(".", ",")
            f.write(f"{i}\n{a_} --> {b_}\n{body}\n\n")
    print(f"SRT: {srt_out}（按实际排程时间，旁挂交付）", flush=True)

    # Merge with ffmpeg
    OUT = os.path.join(srt_dir, "voice_mix.wav")
    cmd = ["ffmpeg", "-y"]
    filters = []
    af = f"atempo={A}," if abs(A - 1.0) > 1e-9 else ""
    for i, (start, path, dur, voice, sp, text) in enumerate(placed):
        cmd.extend(["-i", path])
        dms = ms(start)
        filters.append(f"[{i}:a]{af}adelay={dms}|{dms}[a{i}]")

    n = len(placed)
    sum_in = "".join(f"[a{j}]" for j in range(n))
    # 2026-09-28 修正三处：
    #  1) dropout_transition 3 -> 0：原值会在某路结束后把其余路增益
    #     在 3s 内重新归一化抬升；当前无 BGM 听不出，一旦混 BGM 就是每句
    #     台词结束瞬间 BGM 突响。
    #  2) 统一 aresample + 立体声：各路 wav 采样率/声道不一致时 amix 行为不可预期。
    #  3) 末端 alimiter：normalize=0 下多路叠加仍可能过载削波。
    #     注意不要用 volume=14 手动补偿归一化（必削波）。
    filters.append(
        f"{sum_in}amix=inputs={n}:dropout_transition=0:normalize=0[mix]")
    filters.append(
        "[mix]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        "alimiter=limit=0.95:level=disabled[aout]")

    cmd.extend(["-filter_complex", ";".join(filters)])
    cmd.extend(["-map", "[aout]", OUT])

    print(f"Merging {n} lines -> {OUT}", flush=True)
    if not shutil.which("ffmpeg"):
        print("ERR: 本机无 ffmpeg，跳过合成（wav 与 voice_mix.srt 已落盘，可换机合成）",
              flush=True)
        return 3
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode == 0:
        dur_out = wav_dur(OUT)   # 本地常无 ffprobe；soundfile 读真实时长
        sz = os.path.getsize(OUT)
        print(f"Done: {OUT} duration={dur_out:.2f}s size={sz/1024/1024:.1f}MB", flush=True)
        return 0
    else:
        print(f"ERR: {result.stderr[-500:]}", flush=True)
        return 2


if __name__ == "__main__":
    exit(main())
