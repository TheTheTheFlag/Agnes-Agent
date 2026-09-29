---
name: drama-pipeline
description: "【复用包】一站式短剧/视听创作流水线执行技能（OpenCode 侧）。经 plink/pscp 调用远端 agnes-agent 创作台（127.0.0.1:8081，唯一媒体通道 agnes-media/media.py）完成全流程：①画风选择（任务起始必选，画风库 STYLE-01 摄影写实/STYLE-02 原神风·二次元卡通渲/STYLE-11 次世代国漫CG）→②4 View 角色/场景/道具资产（--ref 锁形象，换装走 assets/regenerate 不新出图）→③剧本(sumeru-* 技能族)+投喂稿→④validate_prompt.py 机检逐段全绿→⑤关键帧（首尾帧独立·九段式帧词·ref≤2·禁资产板·人数前置）→⑥gen_kzqs_videos.py 渲染无配音版（16:9 注入，段视频落盘自动 -an 去模型音轨）→⑦8081 工作台「短剧审核」页验收（资产审核/分镜审核，可改词重生成）→⑧concat 拼接→⑨qa_manifest/qa_videos/qa_deliver 机检（时长/分辨率/流构成/refs 纯净度）→⑩本包 scripts/local_tts.py 离线配音→台账登记（含 credit/耗时/重试账目）。内置 agnes-media 服务运维与 skill 优化循环（复盘→根因→最小修复→规则沉淀→复核清理）。触发词：短剧流水线、出短剧、生成视频、短剧审核、重新生成、配音、agnes 服务、skill 优化、复盘沉淀。"
triggers:
  - 短剧流水线
  - 出短剧
  - 生成视频
  - 短剧审核
  - 重新生成
  - 配音
  - agnes 服务
  - skill 优化
  - 复盘沉淀
---

# 短剧/视听流水线执行包（drama-pipeline）

> 定位：把「调用 agnes-agent 创作剧本→生成视频→任务中修复 agnes 服务→复盘优化 skill」的
> 完整可复用操作册。**凡是用户要求跑短剧全流程、出视频、审核重生成、排查 agnes 服务、沉淀/优化 skill，
> 优先加载本技能并按下方流程执行。**

## 〇、环境速查

| 项 | 值 |
|----|----|
| 远端主机 | `mirror@192.168.31.58`，SSH 端口 9000（`ssh -i ~/.ssh/id_ed25519 -p 9000`） |
| agnes 服务 | systemd 单位 `agnes-agent.service`；日志 `journalctl -u agnes-agent.service` |
| 服务命令 | `python -m app.service --host 0.0.0.0 --port 8081`（`/home/mirror/Agnes-Agent/.venv`） |
| 唯一媒体通道 | `python3 /home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py`（image/video/fetch） |
| 鉴权 | `export AGENT_INTERNAL_TOKEN=$(cat /home/mirror/Agnes-Agent/data/internal_token)`，另带 `X-Agnes-Internal-User: Mirror` |
| 远端 venv python | `/home/mirror/Agnes-Agent/.venv/bin/python`（**无 pip**）；ffmpeg=`/usr/bin/ffmpeg` |
| 项目根 | `/home/mirror/Agnes-Agent/data/users/Mirror/deliverables/短剧/<剧名>/` |
| QA/拼接脚本 | `/home/mirror/stage-kzqs/{qa_manifest,qa_videos,concat_episodes,qa_deliver}.sh`（**已参数化，接 `<项目根>` 参数**） |
| 画风机制 | `app/skills/short-drama-director/references/art-style-selection.md`（任务起始必选） |
| **审核台后端** | `app/server/api/drama_review.py`（前缀 `/api/drama-review/*`） |
| **审核台前端** | `app/server/static/drama-review.js`（工作台「短剧审核」tab，面板 `#studioReviewPanel`） |
| **配音** | 本包自带 `scripts/local_tts.py`（离线 Qwen3-TTS） |
| 配音环境 | `C:/Users/Mirror/Documents/Qoder/2026-09-26/f738e157/ttsenv`（Python 3.12+torch cpu） |

## 一、执行流程（十步，顺序不可乱）

> **唯一例外**：形象变更（换装/发型/受伤/年龄阶段/身份伪装）在关键帧或渲染阶段才被发现时，
> 走 `references/render-contracts.md` §4.11 的**回炉路径**退回第 2 步，资产审核确认后再继续。
> 其余任何情况都不许跳步。

### 1. 画风选择（任务起点）
- 未选定画风**禁止**出任何图/视频。
- 画风库：`STYLE-01 摄影写实·东方诗意` / `STYLE-02 原神风·二次元卡通渲` /
  `STYLE-11 次世代国漫 3D CG`（`次世代国漫 3D CG 画风，顶尖国产 3D 建模，分层布料与物理衣摆，
  细腻发丝根根烘焙，电影级渲染光影，玄幻质感与高级感，杰作级，最佳画质，8K分辨率`）。
  用户指定则直接采用，未指定则询问。**风格档逐字并入一切出图/出视频词，不得改写。**
- 画风管质感（风格档并入出图词），形象由 `--ref` 角色立绘+角色设定决定，两者解耦。
- **风格优先级规则**（P1，2026-09-28 补自 zhaihao118 生产仓经验）：
  - 风格一旦选定，写入项目根 `metadata.json` 的 `visual_style` 字段，**后续全部出图/出视频词自动注入，不得再次询问用户**。
  - 「相对微调」（更明媚/更冷/更柔和）= 在**当前 STYLE 档内**增删质感词，**不是**换档。
  - 换档 = 用户明确说「换风格」才触发，走 §4.11 回炉路径重新出资产。
  - 风格选择只问**一次**，不在每集重复问。
- **相对微调 ≠ 换风格基调（2026-09-28 沉淀）**：用户说「更明媚/更冷」= 在**当前档位内**
  增删质感词，**不是**换 STYLE 档。换档必须用户明确说「换风格」。
- ⚠️ **画风档内禁用摄影词**：`8K高清`+`电影级质感`+`写实/实拍/摄影棚` 三连会把 3D/二次元
  渲染推向「伪实拍」，这才是「棚拍感」的真正来源，不是「写实」二字。
- 风格档应写成「**作品锚 + 渲染工艺**」（如「类似《<某次世代国漫>》3D CG 渲染」），
  比纯形容词有效得多。

### 2. 资产先行（4 View 资产板）+ 资产审核
- **风格先入 metadata.json（P1，2026-09-28 补自 zhaihao118）**：
  画风选定后立即在项目根写 `metadata.json`，字段：
  ```json
  {
    "project_id": "HUAYU-001",
    "project_name": "花语战歌",
    "style_id": "STYLE-11",
    "style_prompt_suffix": "次世代国漫 3D CG 画风，顶尖国产 3D 建模...",
    "created_date": "2026-09-28"
  }
  ```
  后续所有出图/出视频词**自动从 metadata.json 读取 `style_prompt_suffix` 拼入**，
  **不在每段手动拼风格词**，也不重复问用户。
- 角色（多年龄段按需）→场景→道具，`media.py image --ratio 16:9 --size 2K --ref <已有形象图>`。
- 锁定后登记 `assets/manifest.json` + 《剧组产出册.md》+《资产图册.md》（含总数行）。
- **全局媒体索引**（P1，2026-09-28 补自 zhaihao118）：项目根维护 `media_index.json`，
  每次新增角色 ref / 场景 ref / 生成段视频 / 生成关键帧时同步追加 entry，
  含 `path`、`size_bytes`、`type`、`duration_seconds`、`resolution`、`refs` 字段。
  规范详见 `references/media-index-spec.md`。
  QA 验收时直接读该文件做断点续传和完整性校验，**不再逐文件 stat**。
- **同一 CHR 的多年龄段/多服装资产，一律以已有资产图为 `--ref` 走 `assets/regenerate`
  改词重出，禁止作为独立任务新出图**——既省 credit 又保脸一致。
- **资产审核（8081 工作台「短剧审核」tab → 资产审核）**：页面列出每个资产的提示词与出图。
  可修改提示词点「🖼 用此提示词重新生成」，后端 `POST /api/drama-review/assets/regenerate`
  （以原图作 `--ref` 锁形象，改词重出），完成后自动回写 manifest。
- 用户确认资产（含重生成的）后再进入剧本，避免返工。
- ⚠️ **资产图全部禁止入模**（不只是资产板）。地理/道具信息一律走帧词 `[SCENE]` 段文字——
  唯一可机械证明的门禁是「ref 父目录是否为 `assets/`」，详见 `references/render-contracts.md` §4.3。
- **场景/道具资产图 prompt 必须固定收尾**（`reviewed_invariant`）：
  「纯场景/道具资产，画面中无任何人物、无剪影人形、无倒影人形、无可辨识面孔。」
  场景资产图一旦混入人形，它就是「凭空多一个人」的现成素材，重复人问题会从这里复活。
- **角色差异化四维**（剧本角色清单 + 资产 prompt 都要写，2026-09-28 抄自 OnlyShot/FreeVideoSkill 共识，补自 doublesq97-ui 角色矩阵）：  | 维度 | 写法 | 为什么 |
  |---|---|---|
  | **标志色** | 给每个核心角色分配**互补或不重叠**的标志色，落到服装主色/发色/标志性饰品 | 模型靠颜色区分人最稳；两人同色 → 被画成同一类 → 重复人 |
  | **剪影特征** | 明确体型/发型体积/肩宽比例/随身轮廓/**手势幅度**/**静息姿态张力** 差异 | 剪影不同 = 剪影层面就不可混同 |
  | **运动节奏**（P1） | 动作快慢/停顿习惯/发力方式差异 | 映射视频层；帧词静态时用不上，但角色卡要写，否则全片动作同质 |
  | **音色**（P1） | 音高/语速/沙哑度差异 | 直接喂 §9 的 TTS 音色分配 |
  - **条件化判据（P1，优先于「两两都不同」）**：
    **在目标画幅 / 光线 / 服装条件下会被看混的那一对，必须改**。
    「两两都不同」是不可执行的口号；「这一对在本片条件下会混」才是可判的。
  - 帧词 `[CHARACTER]` 段因此开**唯一例外口**：允许写标志色**这一个颜色词**，
    **仍禁止**五官/发色/瞳色/服装细节（见 §5 三条禁令 1，机检白名单只放行颜色词）。
  - **群像自律**（P1）：「重要配角至少有一个不依赖主角的目标和一次独立选择」——
    只有主角有目标 = 群像退化成单人戏，与「女性同盟」设定冲突。
  - **不得依赖难以连续的小配件**（P1，`asset_self_review`）：4 View 出图容易堆配饰，
    小配件跨镜头必漂。少量稳定识别物 > 大量花哨配饰。
- **资产登记必带职责声明**（P0）：`assets/manifest.json` 每个资产写
  `controls`（这张图控制什么：脸/体态/造型/色彩/表情）与 `not_controls`（不得控制什么），
  **一次登记长期生效**，不再每次出帧临时解释同一件事（`render-contracts.md` §4.1 槽位三字段是它的按帧实例化）。
  配套硬判据：**全身图不足以单独承担脸**——脸必须由近景/特写类 ref 承担。
- **资产 ID 命名（P0，渐进式，不破坏存量）**：
  新增资产用 `{类型}-{ID}-{状态或变体}[-vNN]`，如 `chr-001`、`chr-001-look-002`、
  `scene-014-state-night-rain`；旧式 `CHR-02b` 保留为别名写进 manifest 的 `aliases` 字段。
  **文件名不依赖角色名标点，显示名放元数据。**
- **多视角场景资产（`craft_default`，可选）**：预置 6 视角（全景/中景/左视/右视/交互区特写/反向视角）
  供人工选视角，帧词 `[SCENE]` 段可写「参考场景反向视角」这类**方向指名**。
  ⚠️ **只出单视角单文件，禁止拼成板**；仍然 0 ref 入模。

### 3. 剧本 + 投喂稿
- 剧本：`sumeru-*` 技能族（topic→outline→worldbuilder→write→polish→review→finalize）。
- 投喂稿落 `投喂稿/<EP>/投喂稿-<EP>.md`（平铺 12~16 段，每段固定 8 段标签：
  【画幅风格】【场景资产】【核心人物】【站位声明】【时间轴分镜】【接续状态】【音效】【强制禁止项】）。
- **不写 kf_plan.json 到投喂稿里**；资产引用 `@CHR-XX` 只在投喂稿中，机检通过后另行生成
  `投喂稿/kf_plan.json`（仅供关键帧出图）与 `manifest.json`（仅供渲染）。

#### 3.1 投喂稿写作规范（2026-09-28 补，落 P1.2/P2.3/P2.5/P2.8）

**【接续状态】段字段定义**（2026-09-28 补，此前该标签只有名字没有内容规范）：

```
入镜状态：张力/疲劳/信任/伤势/隐瞒等级
触发事件：（本段发生什么）
最小可见证据：（状态变化的最小可见征象——证据写状态，分镜写动作）
出镜状态：（下一镜必须继承什么）
```

- 8 段里唯一「有名无实」的一段，字段化后才可人工核对。
- 与【时间轴分镜】的分工：分镜写**动作**，接续状态写**状态**。同一件事不写两遍。
- 剧内伏笔另立《设置与回收表》（类型/内容/计划回收位置/状态）——
  【接续状态】只管画面连续，不管伏笔，别混在一起。

**段职责**：每段开头标 `[段职责: 建立|升级|转折|铺垫|收束]`。12~16 段若不标职责极易同质化。
集末段必须标「最终收束段」。

**设计期时长预算**（2026-09-29 补，`structural_invariant` 的判定线）：
- **写投喂稿时就把时长账算准**：段时长只有 3/5/10/18 四档，`Σ 段时长 ≤ 集上限（默认 240s − 3s 余量）`。
  账要写在设计期，**不是渲完才发现超**——超上限只能砍段重渲 = 白烧 credit，且段缝连续性断裂。
- 分集在**设计期**定：「一集几段、每段几秒」随剧本一起定稿；内容要多于上限就在设计期拆集。
- 实测口径（代号烛龙）：EP01 178s / EP02 212s / EP03 240s（顶格）/ EP04 60s，四集各自独立成片。
- 设计期账本格式（放投喂稿头部注释即可）：`14 段：3s×2 + 5s×4 + 10s×6 + 18s×2 = 178s ≤ 240s`。
- 校验落点：`qa_manifest.sh`「段时长合计 > 上限」红项即这条的机检面——设计期漏算到这里才暴露。

**台词字数预算**（`craft_default`，中文播报经验值 **5-6 字/秒**，待本模型校准后升 `structural_invariant`）：

| 段时长 | 台词字数上限 | 单段镜头数 |
|---|---|---|
| 3s | ≤15-18 字 | 1-2 镜 |
| 5s | ≤25-30 字 | 1-2 镜 |
| 10s | ≤50-60 字 | 2-3 镜 |
| 18s | ≤90-108 字 | 3 镜 |

- 句间换气 0.15-0.25s。**台词溢出必然踩爆下游 TTS 时间码**（见 §9 与
  `skill-optimization-loop.md` 的「段内多行台词时间码连锁漂移」案例）。
- 台词必须**适配所在镜头时长**：3 秒的镜头塞不进一整句书面语。

**【音效】段必填或显式声明无**：可写完整内容，也可写「无台词/无旁白，仅环境声 + BGM」，
**不得留空或默认省略**——留空会被下游当成「数据缺失」而不是「有意的设计」。

**【强制禁止项】段固定四条**（不要自由发挥，机检依赖这几条）：
`画面水印字幕` / `人物面部漂移` / `服装变化` / **`无关角色乱入`**（本镜不需要的人不许出现）。

**架空/禁忌类设定走 `world_rules.json`，不写进这四条**（`references/world-rules.md`）：
剧本阶段把「本剧绝对不许出现的人/物/词/形象」列成 `banned_elements`，
投喂稿【强制禁止项】段**引用**它（机检会跳过该段，避免自我命中）。
缺失该文件 = 机检只 warn，不阻断；但架空/全女性类项目**必须建**。

**分层纪律（`reviewed_invariant`，易撞车，必须读）**：
- 【时间轴分镜】层**禁代词、禁群体量词**：❌`三人面面相觑` → ✅`@CHR01 赤焰、@CHR02 冰璃、@CHR03 焰心 面面相觑`。
  原因：分镜层点名 = 动作指代，必须点名才不歧义。
- **帧词层规则相反**：禁**点名未出场角色**（点名 = 凭空授权模型画他）。
  两层规则方向不同，**不要互相套用**。

**声音符号契约**（投喂稿/视频词层用）：
`() 音乐` / `<> 音效` / `{} 台词` / `『』 旁白`。
⚠️ **`【】` 是本链路的段标签**（【画幅风格】等），**不是字幕内容标记**——两层不要撞名。

### 4. 机检（全绿才下一步）
- `validate_prompt.py --model agnes`（`app/skills/short-drama-director/scripts/`）逐段通过；
- `split_validate.sh <md> <EP>`（stage-kzqs）绿才继续；红则按提示修 md 重跑。
- `qa_manifest.sh <项目根> <EP>` 绿才继续。它同时校验**世界规则禁用元素**——
  架空 / 全女性类项目必须先建 `world_rules.json`（`references/world-rules.md` §2）。

### 5. 关键帧（首尾帧独立 · 九段式帧词 · ref≤2）★ 最高风险环节

详见 `references/render-contracts.md` §4 与 `references/keyframe-craft.md`。要点：

- 每段正好 2 帧（kf1 首帧 / kf2 尾帧），**全部独立 AI 出图**，按 `gen_e<X>X_kf.py` 模板调用 media.py。
- 关键帧必须 16:9（站点分辨率 2624×1472），否则视频锁不住横屏。
- **段缝不复制、不接力（2026-09-27 铁律）**：废止「上游末帧复制为下游首帧」——坏帧沿段缝一路传染无法自愈。
  衔接靠帧词画面连续（下段首帧与上段尾帧同场景续镜：同站位/同光线/同道具，仅景别或姿态微变），
  不靠像素复制。对应 `kf_plan.json` 每段 `kf1_from` 一律置空。
  ⚠️ 外部 skill 的「首尾帧素材」指**跨镜头另起图**，与帧复制不是同一物，不得据此复活复制规则。
- **九段式帧词（有序配方，`[人数]` 必须第二段）**：
  ```
  [画幅] [人数] [CHARACTER] [BACKGROUND] [SCENE] [CAMERA] [LIGHT] [STYLE] [EXCLUDE]
  ```
  **人数约束从帧词末尾前置到 `[人数]` 段**——末尾是后置文本，权重打不过前置的图像条件（ref）。
- **ref 数量纪律**（`structural_invariant`，机检可阻断）：
  | 场景 | ref 数 | 依据 |
  |---|---|---|
  | 单人镜 | 1（该角色净版立绘） | 1 ref 一致性 ~95% |
  | 双人镜 | 2（两张单人立绘） | 2 ref ~80% |
  | 3 人及以上 | **1**（视觉主角），其余进 `[BACKGROUND]` 强制剪影 | 3+ ref 一致性 **<50%** |
  | 空镜/环境/道具特写 | **0 张 CHR ref** | 挂了模型就有理由把人塞进画面，且常塞两次 |
  - ref 顺序按**视觉重要性**排（第 1 张权重最强，第 3+ 急剧衰减）；**一张图只承担一件事**。
- **资产禁令**（机检硬阻断）：**所有资产图**（4 视图板 / 多宫格 / 单视角场景图 / 道具图 /
  表情差分表，无一例外）**禁止作 `--ref`**。判据是 **ref 父目录是否为 `assets/`**——
  这是唯一可机械证明的口径。净版立绘另放 `kf_refs/` 等 ref 专用目录。
  已在《花语战歌》实测：12/12 段 refs 末尾挂 `SCN-01.png` 资产板 → 人物重复；
  且 refs 分布 `{2:1, 3:1, 4:4, 5:6}`——11/12 段超 `ref≤2`。
- **ref 槽位三字段**（每张 ref 必须在 prompt 正文翻译出来，`media.py` 只传文件）：
  `用途：身份|造型状态|地理|构图|尺度|效果；控制：<范围>；不得控制：<范围>`。
  例：`参考图1《赤焰立绘》：仅用于人物身份与本集造型；不参照其姿势、背景、光线与画面构图。`
- **帧词三条禁令**：
  1. **禁止重描角色外貌**（五官/发色/瞳色/服装细节一律不写进帧词）——身份**只来自 ref**。
     重描 = 让模型按文字重画 → 产出「像 ref 但不是 ref」的新人。
  2. **禁止点名未出场角色**——点名一个本镜不需要的人 = 凭空授权模型画他。
  3. **禁止动作动词**（`疾走/走出/目送/递出/接住/起身/按肩/塞进/回头/转身走开/正要消失…`），
     机检须报 `动作动词残留: 0`。
  > 双时刻拼接同禁（「并肩坐下」+「走向天台」= 一坐一站被写成两人）：根因是**两个时刻
  > 被拼进同一 prompt**，修法是单一时刻·单一姿态·自洽定格，不是加词。
- **帧词字数与并发联动**（`craft_default`）：≤1200 字 → 出图并发 ≤4；1200-1500 字 → 降到 1-2；
  >1600 字 → 阻断重写。
- **禁止用增加形容词回应重复缺陷**（「高质量/避免变形/保持一致」= 零信息量且毁掉可比性）。
  重复缺陷的正确处置是**换层级**：`skill-optimization-loop.md` §处置五选。

### 5.5 逐段流水线（帧齐即渲，出图/渲影可并行）
- **某段 kf1+kf2 出图齐 = 立即渲影该段**，不必等全量帧（总时长 = 出帧与渲影流水叠加）。
- **出图可并行**：media.py 底层是「POST 建任务→轮询」异步模式，后端排队+自动重试。
  并发上限受帧词字数联动约束（见 §5）。
- **渲影可并行**：帧已齐的多段可同时渲影，建议并发 ≤2。
- 推荐编排：`pipe_render.py`（段落线程池，`--workers N`），
  或两阶段并行：`regen_all_frames.py`（并发出图）→ 帧齐后 `gen_kzqs_videos.py`（并发渲影）。
- 唯一硬约束：**同一段不得同时开两个任务**（段内顺序 出帧→渲影）；
  渲影期间不重启 agnes 服务、不改投喂稿/manifest。

### 6. 渲染（无配音版；音轨策略可配）
- 驱动：`/home/mirror/Agnes-Agent/tools_deliver/gen_kzqs_videos.py <manifest.json> [段号]`
  （自动注入 `--width 1280 --height 720`；manifest 由 `rebuild_manifest.sh <EP>` 从投喂稿重建；
  **keyframes 双帧插值**：`[kf1,kf2]` 按首尾帧插值，缺双帧才降级 img2vid）。
  ⚠️ **必须用 tools_deliver 版**，stage-kzqs 下的旧副本没有画幅注入。
- **段视频复制落盘后默认 `ffmpeg -an` 去掉模型自带音轨**（根治个别段落被模型说成英文）。
- **音轨保留特例**：用户指定保留模型音轨的项目（`KEEP_AUDIO=1`）跳过 `-an`，
  QA 反向验收**必须含 audio 流**。
- **超时分级（2026-09-28 新增，替换 99 小时空转）**：
  出图 300s / 视频 1800s / 渲影 1800s。超时后先 `fetch` 确认一次 → 仍无 → 判 FAIL 写标记。
  `fetch` 续查、**禁止重复建任务**（重复提交 = 付两次钱）。
- **静帧兜底（`craft_default`）**：一段渲染失败不该让整集 concat 卡死，降级为静帧 zoompan
  （配方见 `render-contracts.md` §5.2），落盘段必须打 `STATIC_FALLBACK` 标记并在台账列明。
- **统一调色 + 段内淡入淡出（`craft_default`，成片感的最廉价来源）**：各段来自不同任务，
  曝光/色温必然漂移，concat 前逐段统一（配方见 `render-contracts.md` §5.3）。
- **段视频归一化（concat 前置步，2026-09-29 沉淀）**：渲影产物必带两类溢出——
  ① 画幅：模型原生出 1280×704（1.818）而非 1280×720；② 时长：24fps 帧对齐使 18s 档实为 18.375s。
  统一用本包 `scripts/normalize_segments.py <项目根> [EP ...] [--workers N]`：
  按 manifest 声明时长裁剪（`-t`）+ 精确 16:9 pad（`setsar=1`）+ `-an` + crf18；
  覆盖前自动备份 `.review/pre_normalize/` 并登记 `INDEX.jsonl`（原路径+sha256）；幂等可重跑。
  **不归一化 = 整片超 240s 与 concat 时长对账（容差 0.5s）双双必破**（4 段 × 0.375s 已可累积超线）。
  配方见 `render-contracts.md` §5.4。

### 7. 分镜审核 + 重新生成（8081 工作台「短剧审核」tab → 分镜审核）
- 逐集翻页，页面列出**每段分镜提示词 + 已产出视频**（可播放）。
- 每段两个动作：
  - 「🎬 直接重渲此段」：不改提示词，按原词重跑（`POST /api/drama-review/segments/render`，`prompt` 留空）；
  - 「✏ 按上方提示词重渲」：编辑后重渲（`prompt` 填修改稿），后端写独立临时 manifest 重跑。
- 单段任务在页面轮询进度（`GET /api/drama-review/tasks/{id}`），完成后自动刷新。
- **重渲必须声明 `changed_input`（prompt|reference|params）与 `preserve_set`**；
  连续 2 次 `changed_input: "none"` → 拒绝执行并提示「原样重投不产生修复」。
- 全部段落确认后：「✅ 确认合并本集」/「✅ 合并全部集为连播」
  （覆盖前自动备份到 `.review/merge_backup/`）。

### 8. 拼接验收交付
- 合并语义：主走审核台 `POST /api/drama-review/merge`；命令行等价路径 `concat_episodes.sh <项目根> <EP...>`。
- **前置步：先归一化再拼接**——`scripts/normalize_segments.py <项目根> <EP...>`（见 §6）。
  跳过它，`concat_episodes.sh` 的「输出时长 vs 输入段合计对账（容差 0.5s）」与 qa_deliver 的
  「整片 ≤240s」会因帧对齐溢出与 704 高度直接破线，且破线原因隐蔽（单段都在容差内、合计超）。
- **四道机检（全部已参数化，退出码可信）**：
  | 脚本 | 用法 | 退出码 |
  |---|---|---|
  | `qa_manifest.sh` | `<项目根> <EP> [最大总时长秒]` | 0 全绿 / 1 有 FAIL / 2 用法错 |
  | `qa_videos.sh` | `<项目根> <EP>`（`KEEP_AUDIO=1` 反向验收） | 同上 |
  | `concat_episodes.sh` | `<项目根> <EP1> [EP2] …` | 0 / 1 / 2 |
  | `qa_deliver.sh` | `<项目根> <EP1> [EP2] … [最大总时长秒]` | 0 / 1 / 2 |
- `qa_manifest.sh` 检查：残词 / duration 档位(3/5/10/18) / 段时长合计 / 段数 / keyframes 存在性与 16:9 /
  prompt 总字数 / **ref 纯净度（资产禁令 + refs>2 硬阻断 + 每段 ref 数分布）** / 同 ref 重复挂载 /
  **台词字数预算** / 词形软检查 / **世界规则禁用元素**。
  缺 `world_rules.json` 只 warn 不阻断，但架空 / 全女性类项目必须建（`references/world-rules.md`）。
- `qa_videos.sh` 检查：段存在性 / **缺段检测（对照 manifest）** / 分辨率 16:9 容差 0.05 /
  时长 vs manifest 容差 1.0s / 孤儿段 / 流构成（默认只允许 `video`）。
- `concat_episodes.sh` 现有检查：**段数 vs manifest 对账** + **输出时长 vs 输入段合计对账**（容差 0.5s）——
  静默丢段当场 FAIL。⚠️ 脚本内**禁止用 `"$ep"-0*` 通配枚举段目录**（只能匹配到 `-01..-09`，
  段数 ≥10 必漏），用 `find -name "$ep-*"` + 纯数字后缀校验。
- 验收线：整片 ≤240s、单段时长 = duration 档位、分辨率 16:9、**ffprobe 确认无音轨也无字幕流**。

### 9. 配音（本地 Qwen3-TTS，离线）
- 统一由本包 `scripts/local_tts.py`（离线 Qwen3-TTS）从 SRT 生成：
  ```bash
  "C:/Users/Mirror/Documents/Qoder/2026-09-26/f738e157/ttsenv/Scripts/python.exe" `
    "<包>/scripts/local_tts.py" --srt "<SRT>" --out-dir "<输出目录>" `
    --voice-map "<voice_map.json>" --atempo 1.25 `
    --model "C:/Users/Mirror/Documents/Qoder/2026-09-26/f738e157/models/ms/models/Qwen--Qwen3-TTS-12Hz-0.6B-CustomVoice/snapshots/master"
  ```
- 角色→音色映射（SRT 每行 `角色：台词`）：女主角/祝祝/苏夜→`serena`，男主角/蒋蒋/林深→`ryan`，
  同学甲/旁白→`eric`；`--voice-map <json>` 覆盖。voice-map 值两种写法：
  字符串（仅音色）或对象 `{"voice":"…","instruct":"…"}`。
- **实测自然语速（2026-09-29，代号烛龙小样本）**：dylan/serena/vivian ≈ 3.7-3.9 字/秒，
  uncle_fu ≈ 2.9 字/秒；与 §3.1 台词预算的 5-6 字/秒差 1.4-2 倍。
  **必须 `--atempo` 统一补偿**：`atempo ≈ 设计预算 ÷ 实测语速`（代号烛龙取 1.25）。
  atempo 保音高（勿用 `asetrate` 改语速，会变调）；**在混音期施加 → 调参零成本、wav 可复用**。
- **CRLF SRT 是致命坑（2026-09-29 实测）**：Windows 文本模式写出的 SRT 全是 CRLF，
  按 `"\n\n"` 分块解析会整体退化成 **1 条 cue**（实测 51 → 1），整片配音全错。
  `parse_srt` 必须先 `content.replace("\r\n","\n").replace("\r","\n")` 归一化再切。
- **全局防重叠排程（2026-09-29）**：实际起点 = `max(设计起点, 上一行实际结束 + 0.15)`。
  设计稿按 5-6 字/秒写、模型语速更慢，直排必语音互叠（实测 17 cue 超窗、7 处重叠最深 5.4s）。
  顺延只让时间码漂移，**不得丢行**——丢行 = 台词从成片消失。
- 产物：`voice_mix.wav` + 每行 `NNN_1.wav` + **`voice_mix.srt`（按实际排程的时间轴）**。
  voice_mix.srt 作交付字幕旁挂附件，与音轨同步准于设计稿（先例：角色前缀「角色：」保留）。
  **voice_mix.wav 实测格式 = PCM 16bit / 48000Hz / 2ch**（混音链 `aresample=48000` +
  `aformat=…stereo` 的产物；代号烛龙实测 nframes=32219584 → 671.241s）。
  ⚠️ **不得按 24000Hz/mono 反推时长**——`wav_dur()` 已改读 `soundfile.info`，
  旧硬编码 24k/mono 是已修缺陷（见 `skill-optimization-loop.md` 案例表）。
- **混入成片必须给音轨加 `apad`（2026-09-29）**：vox_mix 末尾短于成片时（如 671s vs 690s），
  `-shortest` 会把成片截到音频长度（EP04 会被砍到 ~45s）。滤镜链：
  `[1:a]atrim=start=<off>:end=<off+dur>,asetpts=PTS-STARTPTS,apad,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a]` +
  `-map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart`；
  各集按固定偏移切音轨（代号烛龙 EP01/02/03/04 = 0/178/390/630，总 690s）。
- **本机无 ffmpeg 时**（ttsenv 无 pip）：系统 pip `install --target _tools imageio-ffmpeg`
  取静态构建，再复制为 `_tools/ffbin/ffmpeg.exe`（`shutil.which("ffmpeg")` 只认全名），
  并把 `_tools/ffbin` 前置进子进程 PATH。脚本内置守卫：缺 ffmpeg 时 wav 与 srt 先落盘、exit 3，可换机合成。
- **情绪是独立于音色的第二维（2026-09-28 沉淀；2026-09-29 验证）**：
  每个角色配 2-3 档情绪（常调/激动/低沉），写进 voice-map。
  **`instruct=` 中文情绪指令实测可用**（代号烛龙已按角色写入「沉稳/虚弱冰凉/冰冷机械」等档，
  见该项目 voice_map.json），成本为零；不支持时才退回 ffmpeg 后处理
  （`atempo` 保音高改语速、`asetrate=N*k,aresample=SR` 改音高）。
- **标点参与节奏控制**（零成本，对任何 TTS 成立）：`！` 加强语气、`……` 制造断点、
  `——` 拉长元音（`起——！`）、`，` 自然停顿。写进台词规范。
- 混音注意：`amix` 必须 `normalize=0` + `dropout_transition=0` + 每路 `aresample` 统一采样率 +
  **末端 `alimiter=limit=0.95`**。⚠️ **不要用 `volume=14` 手动补偿归一化**（必削波）。
- **QA 验收（配音版）**：每集 + 连播逐项查 video+audio 流存在、**无 subtitle 流**、
  时长 ±1.0s、1280x720、aac/2ch/48000Hz、`volumedetect` max_volume < -30dB 判近静音、SRT cue 数。

## 二、agnes 服务运维（见 references/agnes-service-ops.md）
健康检查、systemd 重启、任务 `fetch` 续查、`media.py` 修补先备份（`cp media.py media.py.bak-<原因>-<日期>`）、
故障排查清单。**安装远端脚本时必须带非空断言**——scp 静默失败会留下 0 字节文件，
`[ -f ]` 检查通不过去，`cp` 反而把备份好的好文件覆盖成空的。

## 三、skill 优化循环（见 references/skill-optimization-loop.md）
复盘→定位根因（查档案/剧本/生成器，不猜）→最小修复或规则沉淀→改 skill 本体
（权威文件+铁律+目录树+README，用户原稿逐字定稿不擅动）→复核残留→清理工具性残余。

## 四、纪律清单（血泪浓缩）
1. **唯一通道 agnes-media**，禁 h3Prompt/Seedance 直连；禁手写生成脚本绕 media.py。
2. 机检全绿才交付；遇害/虐待/归案等敏感内容淡化（剪影+声音+光影）。
2b. **视频默认不带字幕也不带音轨**：链路无字幕烧录，段视频渲染后默认已 `-an` 去音轨；
    配音一律走第 9 步本地 TTS。交付答疑按此口径。
    **特例**：用户指定保留模型音轨时（`KEEP_AUDIO=1`），QA 反向验收含 audio 流。
3. 大文件脚本 = 本地写好 → pscp 落盘（ASCII 暂存名）→ 远端 bash 执行；**禁内联 sed -i**。
4. 远端中文路径 pscp 会乱码：先 `cp` 成 ASCII 名再传。
5. 本机 PowerShell 吞 `$VAR` 且不支持 `&&`；含中文/括号的命令一律放脚本文件。
6. 改别人文件用**断言**（assert old in text）再替换，失败即停不许盲改。
7. 用户原稿/定稿文件（如画风基准原文）**逐字保留**，只加作用域说明，不删改原文。
8. 工具性残留（.validate、临时脚本、.bak）任务收尾清理——**但备份件在任务确认稳定前不许删**。
9. **改 agnes 后端 .py 后，多文件补丁优先 `systemctl restart agnes-agent`**：
   实测热重载（WatchFiles）在多文件变更时可能卡住不接管。
10. **原样重投不产生修复**：同一输入连续原样重投 ≤2 次，之后必须改内容或退回负责环节。
    重渲必须声明 `changed_input` 与 `preserve_set`。
11. **预算闸门**：每集设 credit 上限 + 重试预算上限，超限即停并上报，**禁「自动重试直到成功」**。
    失败也扣费。
12. **规则分级**（见 `render-contracts.md` §0）：机检红项必须是 `structural_invariant`；
    `craft_default` 只能 warn；`taste_option` 不得单独成为质量缺陷。
    **没标验证条件的规则不许写成绝对禁令**（不把某模型的小样本表现套到所有同轨模型上）。
13. **事前预算 vs 事后处置是两回事**：`video-prompt-craft.md` §7 的 7 轴复杂度预算是
    **动手前**就不该开工；`skill-optimization-loop.md` 的「处置五选」是**失败后**怎么修。
    两步都写，缺一不可——只有事后处置 = 靠烧 credit 买经验。
14. **禁降级 ≠ 禁换层级**：禁的是工具失败后**无声**改用 T2I/T2V（丢掉参考图约束还装作没变）；
    显式拆镜 / 减 ref / 换结构是允许的，但必须**声明改了什么**（`changed_input` + `preserve_set`）。
15. **设定必须是可机检的**：架空 / 全女性 / 禁某类元素这类硬设定落 `world_rules.json`
    （`references/world-rules.md`），不许只活在剧本描述和记忆里。违反时**改设定归属，不删句子**
    （`❌「男爵的军旗」→ ✅「霜华军团的军旗」`）。

## 五、脚本（scripts/）
| 脚本 | 用途 |
|---|---|
| `install_remote.sh` | 把下列 QA/拼接脚本安装到远端 `/home/mirror/stage-kzqs/`（改前备份 + **五道断言**：非空/语法/复检/无写死路径/无 `-0*` 通配）。**改完远端脚本必走它**，别裸 `cp` |
| `agn_health.sh` | 创作台健康检查（进程/端口/日志/重启提示） |
| `render_ep.sh <manifest> <段号...>` | 调用 tools_deliver 驱动渲染指定段 |
| `qa_manifest.sh <项目根> <EP> [最大总时长]` | manifest 机检：残词 / duration 档位 / 段数 / 关键帧 16:9 / **ref 纯净度（资产图 + 重复挂载 + refs>2 硬阻断 + 分布统计）** / **台词字数预算（`{}`·`『』`，6 字/秒 × duration）** / 词形软检查（`--` 截断符 / 抽象情绪词 / 概括复用空话） / **世界规则禁用元素（`world_rules.json`，含否定子句剔除 + 白名单）** |
| `qa_videos.sh <项目根> <EP>` | 段视频机检（含缺段/孤儿段检测） |
| `concat_episodes.sh <项目根> <EP...>` | concat 拼接（段数+时长双对账，覆盖前自动备份 + sha256 登记） |
| `qa_deliver.sh <项目根> <EP...> [最大总时长]` | 全链验收（收集 FAIL 数组，真会 exit 1） |
| `normalize_segments.py <项目根> [EP...] [--workers N]` | 段视频归一化（concat 前置步）：按声明时长裁剪 + 精确 16:9 pad + `-an`；备份 `.review/pre_normalize/` + INDEX.jsonl；幂等（见 §6/§8） |
| `local_tts.py` | 本地 Qwen3-TTS 从 SRT 生成中文配音（见 §9） |

## 六、参考文件
| 文件 | 定位 |
|---|---|
| `references/render-contracts.md` | **渲染层唯一权威**：规则分级 / 画幅锁 / manifest 契约 / 段缝 / 关键帧（ref 纪律·资产禁令·九段式·禁令·回炉路径）/ 视频词分层 / 拼接 / 字幕边界 / 无配音 / 成本台账 |
| `references/keyframe-craft.md` | **帧词工艺**（静态定格）：九段式配方与顺序理由 / 纵深三段 / 焦点层级 / 连戏锚 / EXCLUDE 按帧定制 / 形容词纪律 / 自检清单 / 失败处置五步 |
| `references/video-prompt-craft.md` | **视频词工艺**（动态分镜）：分层禁令 / 段内时间轴分拍 / 运动幅度白名单 / 情绪具象化 / 受控词表 / 声音符号 / 末尾固定禁令 / **事前复杂度预算 7 轴** |
| `references/skill-optimization-loop.md` | 复盘→根因→修复→沉淀→复核；规则分级；**缺陷处置五选**；已沉淀案例表；**外部 skill 比对机制 + 搬运前四问 + 明确不引入表** |
| `references/world-rules.md` | **世界不变量 / 禁用元素 / 偏好元素**三型分离；`world_rules.json` 结构与机检范围；否定声明子句剔除；违反处置（改归属不删句）。架空/全女性等设定的**唯一落点** |
| `references/agnes-service-ops.md` | 服务运维：健康检查 / 客户端契约 / **超时分级** / 审核台 API / 远端脚本安装断言 / **长任务后台化与监控（`pgrep` 自匹配括号技巧·禁编辑运行中脚本·成败读结构化台账）** / 故障排查 / 已修复履历 |
