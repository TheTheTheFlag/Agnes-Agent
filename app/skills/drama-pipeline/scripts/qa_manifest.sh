#!/bin/bash
# qa_manifest.sh <项目根> <EP> [最大总时长秒]
# manifest 交付 QA：残词、duration 档位、段数、keyframes 存在性与 16:9、prompt 字数、refs 纯净度、资产账 schema
# 退出码：0=全绿；1=有 FAIL；2=用法/文件缺失
set -u
R="$1"; EP="$2"; MAXSEC="${3:-240}"
[ -d "$R" ] || { echo "缺项目目录: $R"; exit 2; }
MF="$R/投喂稿/$EP/manifest.json"
[ -f "$MF" ] || { echo "缺失 $MF"; exit 2; }
FAIL=()

echo "== $EP manifest QA =="

echo "-- 残词 --"
for w in '接续状态' '@CHR' '@SCN' '【' '】' '--duration' '末态继承' '拼接节点'; do
  n=$(grep -o "$w" "$MF" 2>/dev/null | wc -l)
  printf '  残词[%s]=%s\n' "$w" "$n"
  [ "$n" -gt 0 ] && FAIL+=("残词 $w 出现 $n 次")
done

echo "-- python 校验 --"
python3 - "$R" "$EP" "$MF" "$MAXSEC" <<'PYEOF'
import json, os, re, sys
R, EP, MF, MAXSEC = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
fails = []
m = json.load(open(MF, encoding='utf-8'))
segs = m.get('segments', [])
print("== segments 数: %d ==" % len(segs))

# duration 档位
ALLOWED = {3, 5, 10, 18}
from collections import Counter
dist = Counter()
for s in segs:
    d = s.get('duration')
    dist[d] += 1
    if d not in ALLOWED:
        fails.append("%s duration=%s 不在档位 %s" % (s.get('seg'), d, sorted(ALLOWED)))
print("== duration 分布: %s ==" % dict(dist))
total = sum(int(s.get('duration') or 0) for s in segs)
print("== 段时长合计: %ds (上限 %ds) ==" % (total, MAXSEC))
if total > MAXSEC:
    fails.append("段时长合计 %ds > 上限 %ds" % (total, MAXSEC))

# keyframes
try:
    from PIL import Image
    have_pil = True
except Exception:
    have_pil = False
for s in segs:
    ks = s.get('keyframes') or []
    if len(ks) < 2:
        fails.append("%s keyframes=%d 少于 2（会降级 img2vid）" % (s.get('seg'), len(ks)))
    for k in ks:
        if not os.path.exists(k):
            fails.append("%s 关键帧缺失 %s" % (s.get('seg'), k))
            continue
        if have_pil:
            try:
                w, h = Image.open(k).size
                if abs(w / h - 16 / 9) > 0.05:
                    fails.append("%s/%s 非 16:9 (%dx%d)" % (s.get('seg'), os.path.basename(k), w, h))
            except Exception as e:
                fails.append("%s 无法读图 %s" % (os.path.basename(k), e))

# refs 纯净度（治「人物重复」：资产板/多视图图不得作 --ref）
KFP = os.path.join(R, '投喂稿', 'kf_plan.json')
if os.path.exists(KFP):
    print("== kf_plan refs 纯净度 ==")
    kd = json.load(open(KFP, encoding='utf-8'))
    flat = [x for ep in kd if isinstance(ep, dict) for x in ep.get('segments', [])] \
        if isinstance(kd, list) else kd.get('segments', [])
    board_hits = 0
    for s in flat:
        for r in (s.get('refs') or []):
            b = os.path.basename(str(r))
            parent = os.path.basename(os.path.dirname(str(r)))
            is_board = (parent == 'assets') or re.search(r'sheet|4view|4-view|board|多视图|资产板', b, re.I)
            if is_board:
                board_hits += 1
                fails.append("%s ref 指向资产板/多视图图: %s（模型会把多区块误解为画面需多人/多视角）"
                             % (s.get('seg'), b))
    print("  资产板 ref 命中: %d" % board_hits)
    # 同图重复挂载
    for s in flat:
        rs = [str(r) for r in (s.get('refs') or [])]
        dup = {x for x in rs if rs.count(x) > 1}
        if dup:
            fails.append("%s 同一 ref 重复挂载: %s" % (s.get('seg'), sorted(dup)))
    # ref 数上限（render-contracts §4.2：关键帧出图 ref ≤2）
    for s in flat:
        n_ref = len(s.get('refs') or [])
        if n_ref > 2:
            fails.append("%s refs=%d 超过上限 2（3+ ref 一致性 <50%%，且必然诱发人物重复）"
                         % (s.get('seg'), n_ref))
    # 资产图 ref：非 assets/ 父目录但仍是板图（按文件名兜底），统计全部资产 ref
    print("== kf_plan refs 分布 ==")
    from collections import Counter as _C
    print("  每段 ref 数分布: %s" % dict(_C(len(s.get('refs') or []) for s in flat)))
else:
    print("== 未找到 kf_plan.json，跳过 refs 纯净度检查 ==")

# 台词字数预算（render-contracts §3.1 / SKILL.md §3.1，5-6 字/秒）
# 台词标记：{} 台词 / 『』 旁白（声音符号契约）。未采纳契约的旧稿只提示不强判。
print("== 台词字数预算 ==")
budget_fail, budget_skip = 0, 0
for s in segs:
    d = s.get('duration') or 0
    p = s.get('prompt') or ''
    lines = re.findall(r'\{([^}]*)\}', p) + re.findall(r'『([^』]*)』', p)
    if not lines:
        budget_skip += 1
        continue
    n_cjk = len(re.findall(r'[\u4e00-\u9fff]', ''.join(lines)))
    cap = int(d) * 6          # 6 字/秒 为上限档（5-6 字/秒）
    if n_cjk > cap:
        budget_fail += 1
        fails.append("%s 台词 %d 字 > 上限 %d 字（%ds × 6 字/秒），会踩爆下游 TTS 时间码"
                     % (s.get('seg'), n_cjk, cap, d))
if budget_skip:
    print("  WARN %d 段未检出 {} / 『』 台词标记，跳过预算（建议采纳 SKILL.md §3.1 声音符号契约）"
          % budget_skip)
print("  台词超预算段数: %d" % budget_fail)

# 词形软检查（warn 级，不阻断）
print("== 词形软检查 ==")
for s in segs:
    p = s.get('prompt') or ''
    if '--' in p:
        print("  WARN %s 含连续两个连字符 '--'（外部 skill 称该符之后内容会被模型截断，"
              "该断言无出处，待本模型验证）" % s.get('seg'))
    emo = re.findall(r'(很|非常|十分|极其|特别)(悲伤|愤怒|紧张|害怕|高兴|难过|激动|绝望)', p)
    if emo:
        print("  WARN %s 含抽象情绪词 %s（改写为身体细节，见 video-prompt-craft.md §3）"
              % (s.get('seg'), [''.join(x) for x in emo]))
    if re.search(r'同上|其余一样处理|其他不变|保持不变|参考原图', p):
        print("  WARN %s 含概括复用/空话（每张图每段必须独立写全，见 render-contracts §4.10）"
              % s.get('seg'))

# 世界规则禁用元素（references/world-rules.md）。缺失 world_rules.json = warn 不阻断。
# ⚠️ 缺失时只跳过「禁用元素扫描」这一件事：下方 3) 资产 / 4) 投喂稿 的存在性·覆盖率检查
#    **必须无条件执行**。这些段曾整段嵌在本块的 else 里 → 没有 world_rules.json 的项目，
#    资产空壳检查根本不跑却仍输出全绿（与《代号烛龙》空壳漏检同源）。
WR = os.path.join(R, 'world_rules.json')
print("== 世界规则禁用元素 ==")
if os.path.exists(WR):
    wr = json.load(open(WR, encoding='utf-8'))
else:
    wr = {}
    print("  WARN 未找到 world_rules.json，禁用元素扫描跳过"
          "（架空/全女性等设定必须落此文件，见 world-rules.md §2）")

be = wr.get('banned_elements', {}) or {}
safe = set(w for w in (be.get('safe_overrides', {}) or {}).get('words', []))
groups = {k: v for k, v in be.items()
          if isinstance(v, dict) and k != 'safe_overrides'}
# 否定声明子句必须剔除：资产 prompt 普遍写「画面严禁出现男性」，
# 这类**禁令本身**会自我命中 → 必然假阳性，三轮之后没人再看机检输出。
negs = set((be.get('safe_overrides', {}) or {}).get('negation_markers')
           or ['严禁', '禁止', '不得', '不出现', '不含', '没有', '无任何', '无人物',
               '切勿', '避免', '不要', '未出现'])
if groups:
    print("  禁用组: %s（白名单 %d 词，否定标记 %d 个）"
          % (sorted(groups), len(safe), len(negs)))

def scan(text, where):
    """返回 [(词, 组, 上下文)]：先剔否定子句，再剔白名单命中。
    groups 为空（未提供 world_rules.json）时返回 []——调用方仍会打印扫描覆盖率。"""
    if not text or not groups:
        return []
    t = text
    for w in sorted(safe, key=len, reverse=True):
        if w:
            t = t.replace(w, '\x00' * len(w))
    kept = [c for c in re.split(r'[。；;，,\n！!？?]+', t)
            if not any(n in c for n in negs)]
    t = '\n'.join(kept)
    hits = []
    for gname, g in groups.items():
        for w in (g.get('words') or []):
            if not w:
                continue
            for m in re.finditer(re.escape(w), t):
                hits.append((w, gname, t[max(0, m.start() - 12):m.end() + 12]
                             .replace('\x00', '')))
    return hits

# 1) 视频词（manifest）
for s in segs:
    for w, g, ctx in scan(s.get('prompt') or '', s.get('seg')):
        fails.append("禁用元素[%s] 命中「%s」于 %s 视频词：…%s…（处置见 world-rules.md §5，"
                     "优先改设定归属而非删句）" % (g, w, s.get('seg'), ctx))

# 2) 帧词（kf_plan）—— 最高优先级
KFP2 = os.path.join(R, '投喂稿', 'kf_plan.json')
if os.path.exists(KFP2):
    kd2 = json.load(open(KFP2, encoding='utf-8'))
    flat2 = ([x for ep in kd2 if isinstance(ep, dict) for x in ep.get('segments', [])]
             if isinstance(kd2, list) else kd2.get('segments', []))
    n_kf_chars = 0
    for s in flat2:
        # kf_plan 的帧词字段是 kf1_prompt/kf2_prompt；只扫 'prompt' 会静默空转
        for fld in ('prompt', 'kf1_prompt', 'kf2_prompt'):
            txt = s.get(fld) or ''
            if not txt:
                continue
            n_kf_chars += len(txt)
            for w, g, ctx in scan(txt, s.get('seg')):
                fails.append("禁用元素[%s] 命中「%s」于 %s 帧词(%s)：…%s…"
                             % (g, w, s.get('seg'), fld, ctx))
    print("  已扫帧词 %d 段 / %d 字" % (len(flat2), n_kf_chars))
    if flat2 and n_kf_chars == 0:
        print("  WARN 帧词扫到 0 字——kf_plan 字段名与扫描器不匹配，该检查形同未执行")

# 3) 资产账 schema 断言 + prompt 禁用元素扫描（场景/道具另查零人物收尾）
#    本节三项均判 FAIL 而非 WARN：三者都意味着「本段检查根本没跑」，
#    却会输出全绿——静默放行等于把空壳项目一路送进出货（《代号烛龙》实际事故）。
#    2026-09-29 增 schema 断言（顶层扁平列表 + 必填 7 字段 + 枚举 + ready 路径实存），
#    对齐 short-drama-director/references/asset-first-pipeline.md §2c·补（消费方契约）。
AM = os.path.join(R, 'assets', 'manifest.json')
REQ_FIELDS = ('id', 'type', 'name', 'prompt', 'ratio', 'status', 'path')
ASSET_TYPES = ('character', 'scene', 'prop')
ASSET_STATUS = ('ready', 'draft', 'failed')
if not os.path.exists(AM):
    print("  资产扫描: 未执行（缺 %s）" % AM)
    fails.append("缺 assets/manifest.json——审核台 GET /assets 直接读该文件，"
                 "缺失 = 资产审核页空白；且本段禁用元素扫描形同未执行")
else:
    am = json.load(open(AM, encoding='utf-8'))
    # 顶层必须是扁平列表（§2c·补②）：审核台消费方按顶层数组遍历（for it in data → it.get(…)），
    # 嵌套分类字典迭代出的是字符串键，实测直接 HTTP 500；{assets:[…]} 包裹同理不合契约。
    if isinstance(am, list):
        items = am
    elif isinstance(am, dict):
        inner = am.get('assets')
        if inner is None:
            inner = am.get('items')
        if isinstance(inner, list):
            items = inner
            fails.append("assets/manifest.json 顶层是 {'assets'/'items':[…]} 包裹而非扁平列表"
                         "——消费方按顶层数组遍历，见 asset-first-pipeline.md §2c·补")
        elif isinstance(inner, dict) and any(isinstance(v, list) for v in inner.values()):
            # 真实现网形态：《代号：烛龙》= {assets:{characters,scenes,props}}。
            # 展平只为让禁用元素扫描仍能跑，诊断必须点名分类键而不是被 refs 等顶层列表带偏。
            cat = {c: v for c, v in inner.items() if isinstance(v, list)}
            items = [dict(v, _category=c) for c, lst in cat.items()
                     for v in lst if isinstance(v, dict)]
            fails.append("assets/manifest.json 顶层是嵌套分类字典 assets:%s 而非扁平列表"
                         "（分类信息放每项 type 字段；字典结构实测审核台 GET /assets HTTP 500）"
                         "——见 asset-first-pipeline.md §2c·补" % sorted(cat))
        else:
            cat = {c: v for c, v in am.items() if isinstance(v, list)}
            if cat:
                items = [dict(v, _category=c) for c, lst in cat.items()
                         for v in lst if isinstance(v, dict)]
                fails.append("assets/manifest.json 顶层是嵌套分类字典 %s 而非扁平列表"
                             "（分类信息放每项 type 字段；字典结构实测审核台 HTTP 500）"
                             "——见 asset-first-pipeline.md §2c·补" % sorted(cat))
            else:
                items = []
                fails.append("assets/manifest.json 顶层对象（键: %s）里找不到资产列表，无法解析"
                             "——需为扁平列表，见 asset-first-pipeline.md §2c·补" % sorted(am))
    else:
        items = []
        fails.append("assets/manifest.json 顶层类型 %s 既非数组也非对象，无法解析"
                     % type(am).__name__)
    zero_people = wr.get('scene_zero_people', True)
    n_asset_chars = 0
    seen_ids = set()
    st_dist = {'ready': 0, 'draft': 0, 'failed': 0, '?': 0}
    for a in items:
        if not isinstance(a, dict):
            fails.append("资产条目非对象: %.60r（schema 见 asset-first-pipeline.md §2c·补）" % (a,))
            continue
        aid = a.get('id') or a.get('code') or a.get('name') or '?'
        # ① 必填字段（schema 必填 id/type/name/prompt/ratio/status/path）
        miss = [k for k in REQ_FIELDS if k not in a]
        if miss:
            fails.append("资产 %s 缺必填字段 %s（必填 %s）"
                         % (aid, miss, '/'.join(REQ_FIELDS)))
        # ② id 唯一（账本主键）
        if a.get('id'):
            if a['id'] in seen_ids:
                fails.append("资产 id 重复: %s" % a['id'])
            seen_ids.add(a['id'])
        # ③ 枚举：type / status（仅 ready 可被下游引用）
        if 'type' in a and a.get('type') not in ASSET_TYPES:
            fails.append("资产 %s type=%r 不在 %s" % (aid, a.get('type'), list(ASSET_TYPES)))
        st = a.get('status')
        if st in ASSET_STATUS:
            st_dist[st] += 1
        else:
            st_dist['?'] += 1
            if 'status' in a:
                fails.append("资产 %s status=%r 不在 %s（仅 ready 可被下游引用）"
                             % (aid, st, list(ASSET_STATUS)))
        # ④ prompt 必填非空：必须是实际提交的完整提示词全文（不得摘要/省略）
        pr = a.get('prompt') or a.get('prompt_zh') or ''
        if 'prompt' in a and not str(a.get('prompt') or '').strip():
            fails.append("资产 %s prompt 为空——须写实际提交的完整提示词全文（§2c·补②）" % aid)
        # ⑤ ratio 形式 + ready 资产 path 实存/非截断/与声明比例一致
        rt = a.get('ratio')
        if 'ratio' in a and not re.match(r'^\d+:\d+$', str(rt or '')):
            fails.append("资产 %s ratio=%r 非 'W:H' 形式" % (aid, rt))
        if st == 'ready':
            p = str(a.get('path') or '')
            if not p:
                fails.append("资产 %s status=ready 但 path 为空（§2c·补：path 用项目根相对路径）" % aid)
            else:
                ap = p if os.path.isabs(p) else os.path.join(R, p)
                if not os.path.exists(ap):
                    fails.append("资产 %s path 不存在: %s（ready 资产须实存，支持项目根相对路径）"
                                 % (aid, p))
                else:
                    sz = os.path.getsize(ap)
                    if sz <= 1000:
                        fails.append("资产 %s 文件过小/疑似截断: %s (%dB)（出图产物断言②）"
                                     % (aid, p, sz))
                    elif have_pil and re.match(r'^\d+:\d+$', str(rt or '')):
                        try:
                            w, h = Image.open(ap).size
                            rw, rh = (int(x) for x in str(rt).split(':'))
                            if abs(w / h - rw / rh) > 0.05:
                                fails.append("资产 %s 实际 %dx%d 与声明 ratio %s 不符"
                                             % (aid, w, h, rt))
                        except Exception as e:
                            fails.append("资产 %s 图片无法读取: %s" % (aid, e))
        # ⑥ 扫描：禁用元素 + 场景/道具零人物收尾
        n_asset_chars += len(pr)
        for w, g, ctx in scan(pr, aid):
            fails.append("禁用元素[%s] 命中「%s」于资产 %s prompt：…%s…"
                         % (g, w, aid, ctx))
        kind = (a.get('type') or a.get('kind') or a.get('category') or a.get('_category') or '')
        if zero_people and str(kind).lower() in ('scene', 'scenes', 'scn', 'prop', 'props',
                                                 'prp', '场景', '道具'):
            if pr and not re.search(r'无任何人物|无人物|无人\b|不含人物', pr):
                print("  WARN 资产 %s（%s）prompt 缺「画面内零人物」收尾——"
                      "人眼审核项，见 world-rules.md §3" % (aid, kind))
        # ⑦ 可选字段轻校验（格式不合只 warn）
        if a.get('md5') and not re.match(r'^[0-9a-fA-F]{32}$', str(a.get('md5'))):
            print("  WARN 资产 %s md5 非 32 位十六进制（可选字段）" % aid)
        if 'views' in a and not isinstance(a.get('views'), list):
            print("  WARN 资产 %s views 非列表（可选字段）" % aid)
    print("  已扫资产 %d 项 / %d 字；status 分布 ready=%d draft=%d failed=%d 未标=%d"
          % (len(items), n_asset_chars, st_dist['ready'], st_dist['draft'],
             st_dist['failed'], st_dist['?']))
    if not items:
        fails.append("assets/manifest.json 存在但 0 个资产条目（空壳）——"
                     "审核台无内容可审，本段检查未执行")

# 4) 投喂稿 md：跳过【强制禁止项】段（它会声明禁用清单，必然自我命中）
MD = os.path.join(R, '投喂稿', EP, '投喂稿-%s.md' % EP)
if os.path.exists(MD):
    raw = open(MD, encoding='utf-8').read()
    kept, skip = [], False
    for line in raw.splitlines():
        if line.strip().startswith('【强制禁止项】'):
            skip = True
            continue
        if skip and line.strip().startswith('【'):
            skip = False
        if not skip:
            kept.append(line)
    body = '\n'.join(kept)
    md_hits = scan(body, '投喂稿')
    for w, g, ctx in md_hits:
        fails.append("禁用元素[%s] 命中「%s」于投喂稿（已跳过【强制禁止项】段）：…%s…"
                     % (g, w, ctx))
    print("  已扫投喂稿（剔除强制禁止项段后 %d 字，命中 %d）" % (len(body), len(md_hits)))
else:
    print("  WARN 未找到 %s，跳过投喂稿扫描" % os.path.basename(MD))

tot = sum(len(s.get('prompt', '')) for s in segs)
print("== prompt 总字数: %d ==" % tot)

if fails:
    print("\n!! FAIL %d 项 !!" % len(fails))
    for f in fails:
        print("  - %s" % f)
    sys.exit(1)
print("\n== manifest QA 通过 ✓ ==")
PYEOF
rc=$?
[ $rc -ne 0 ] && FAIL+=("manifest python 校验未通过（见上）")

echo
if [ ${#FAIL[@]} -gt 0 ]; then
  echo "===== qa_manifest $EP 失败 ${#FAIL[@]} 项 ====="
  for f in "${FAIL[@]}"; do echo "  - $f"; done
  exit 1
fi
echo "===== qa_manifest $EP 全绿 ✓ ====="
exit 0
