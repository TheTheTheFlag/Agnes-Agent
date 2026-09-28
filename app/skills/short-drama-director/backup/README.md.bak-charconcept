# 漫剧老李 AIGC 创作全流程 Skill · V6.9.8 轻量版 · Agnes 本地化单通道（Short-Drama Director Suite）

面向 **AIGC 视听创作** 的工业级全流程创作技能包，供 AI 助手 / LLM 调用——从创意立项、剧本、资产、分镜到视频投喂提示词与独立质检。
典型场景为 AI 短剧 / 漫剧 / 短视频剧集；管线与题材解耦，同样适用于 AI 广告片 / 宣传片、分镜预演（PREVIS）、武打动作设计、微表情专项等一切需要跨镜头一致性的 AIGC 视频生产。
覆盖：**Asset-First 六阶段管线**（立项锁定 → 五阶门控剧本 → 数字资产包 → 分镜 → 视频提示词 → 独立质检）。

> 🔑 **核心理念：资产图先于分镜。** 角色/场景/道具在 P2 数字资产包阶段完成**出图并锁定**（非文字描述），成为《资产图册》唯一权威事实来源，分镜/投喂一律引用已锁定 @资产图。

> **品牌**：漫剧老李 AIGC 全流程 Skill
> **版本**：V6.9.8 轻量版（Lite）**· Agnes 本地化单通道（2026-09-26）**：
> **V6.9.8（2026-09-26 本地化）**：媒体执行**统一收敛为 agnes 单通道**——出图 = `agnes-image-2.5-flash`、出视频 = `agnes-video-v2.0`（flash 档 `agnes-video-2.5-flash` 备选），
> 唯一执行入口 = `agnes-media`（`app/skills_builtin/agnes-media/media.py`，内部创作台 127.0.0.1:8081，需注入 `AGENT_INTERNAL_TOKEN`）；
> 角色形象以各角色资产板/参考图为准；**不再询问模型版本三选一**，
> Seedance / MiniMax H3 / 即梦 等文档库内容仅作方法论来源，不构成执行契约；出稿机检 C1~C20 保持，`validate_prompt.py --model agnes` 语义化。
> **V6.9.7（2026-09-19）**：新增**首镜单主体锚定与具象化**（§5.0.2 续增单主体铁律 + §5.0.4 续增抽象形容删除纪律 + 法则 23 + 机检 **C20**——
> 锚定句只许一个具名主体、禁「X 与 Y 同框」类并列主体句式；指代词必须带归属；抽象形态/程度副词一律删；反光道具零光效描写（C20d）；道具动作与语气一致（C16d））。
> **V6.9.6（2026-09-19）**：新增**禁止项栏目纪律**（§5.0.4 续增 + 法则 22 + 机检 **C19e**——【强制禁止项】只装负面清单，正向执行指令禁入，否定式禁令同样禁入）。
> **V6.9 基座（2026-09-18）**：〇节新增 **D·段缝衔接方式三选一锁** / **旁白·独白视听编织规程**（OS 声画分离红线·VO 配额）/
> **时长守恒·禁拉伸凑满**（法则 17 修订，-25% 弹性，C15 门）/ 出稿机检升 **C1~C15**，40+ 份规则库。
> **V6.9.3（2026-09-19）**：对白语气位规范（HOW THEY SOUND，**全规格必写**；音色锁可选不做资产体系）+ 机检 **C16**；
> **V6.9.4**：首镜锚定·景别一致·姿态延续·视线指派 + 机检 **C18**；**V6.9.5**：禁令失效三改法 + 机检 **C19**；
> （首帧/关键帧能力：**agnese 无首尾帧**，形象锁定与首末过渡全部由 keyframes 2~3 图 / img2vid 单图承担，见 `references/agnes-media-adapter.md` §2）

> ⚠️ **部署铁律（高于一切原生契约）**：
> - 出图/出视频唯一通道 = **agnes-media `media.py`**；调用前必须 `export AGENT_INTERNAL_TOKEN=$(cat /home/mirror/Agnes-Agent/data/internal_token)`；
> - A 锁 = **agnes 单通道**（不三选一、不询问模型版本）；B 锁画幅显式指定（默认 16:9，禁止靠默认）；C 交付形态默认 ③ 两者都交付；D 段缝三选一；
> - keyframes 为 append 参数：`--keyframes A --keyframes B`（2~3 张，服务端要求 ≥2 张），单 flag 后只跟一张图；
> - 时长档位 `--duration 3|5|10|18`（→ 81/121/241/441 帧 @24fps），单段上限 18s，由内容倒推、禁拉伸凑满。

---

## 🎬 亮点

- **可视化资产管线**：P2 数字资产包全部出图锁定，分镜/投喂引用 @资产图，画风强制并入用户三视图人物形象提示词；
- **四前置锁**：A 模型（agnes 单通道）/ B 画幅 / C 交付形态 / D 段缝衔接方式，未指定不动笔；
- **五阶门控剧本引擎**：12 节拍情绪曲线 + 台词 OBJECT/CONTEXT/BEAT 拆分 + C1~C20 机检门禁；
- **资产锁 16 项**：形象/服装/场景/道具/画幅/角色账等，跨段不穿帮；
- **FACS 微表情引擎**：情绪 → AU 单元 → 面部肌肉动作映射，冷硬台词不甜笑；
- **独立质检**：剧本端 v3.4.3 词表过滤 + 投喂端 validate_prompt（C1~C20，`--model agnes`）+ 包级 check_package。

---

## 🚀 快速开始（给 AI 助手）

1. 读取 `SKILL.md`，按“六步工作法”执行——**先完成 A 锁（agnes 单通道，不询问模型版本三选一）与 B 锁（画幅 16:9/9:16/21:9）才动笔，未指定不动笔**；
2. 先定**动作强度档**：`R1 写实克制` / `R2 商业高燃（默认）` / `R3 玄幻大招`；
3. 有对白 → 先过 `dialogue-speed-check` 语速自检；
4. 查 `agnes-media-adapter` 当前媒体能力（唯一通道）；
5. 出稿前过 `quality-gate-review`；
6. 交付前机检：`scripts/validate_prompt.py <稿> --model agnes`，FAIL 清零。

## 🧮 常用指令（SKILL 内注册）

`/写剧本` · `/台词诊断` · `/数字资产包` · `/做分镜` · `/语速自检` · `/生成视频提示词` · `/导出工作流参数` · `/看板` · `/微表情` · `/审查` · `跳过确认，直接出整集`

> 完整指令路由（含 `/剧组产出册` `/资产图册` `/情绪曲线` `/角色资产板` `/顶视图` 等）见 `SKILL.md` 四、快速指令路由。

---

## 📦 安装

```bash
# OpenClaw
openclaw skills install ./short-drama-director --as short-drama-director
```

其他 Agent 框架：把整个目录放入技能目录，并在系统提示中引用 `SKILL.md`。

## 🧹 质量自检

仓库内置 `scripts/check_package.py`：

```bash
python3 scripts/check_package.py
```

检查：SKILL frontmatter 完整性、references 清单（43 份）、语义回归（可灵残留 / 4 View 口径 / Prompt 格式命名 / LEGACY 标记 / README-SKILL 版本一致 / agnes 单通道 / 机检 C1~C20）。

---

## 🧱 结构

```
short-drama-director/
├── SKILL.md                     # 总控路由 + 工作法 + 模块清单
├── references/                  # 43 个专业规则库
├── scripts/
│   ├── check_package.py         # 包级静态规则与完整性校验脚本（打包/出厂时跑）
│   ├── validate_prompt.py       # 稿件级投喂词机检门禁（C1~C20，`--model agnes`；`--model h3` 历史兼容）
│   ├── build_board_lite.py      # 轻量离线分镜看板编译器（md → 单文件 HTML）
│   └── generate_emotion_curve.py# 12 节拍情绪曲线绘制脚本
├── README.md
├── LICENSE
└── CHANGELOG.md
```

> 媒体通道权威文件：`references/agnes-media-adapter.md`（★）；画风选择：`references/art-style-selection.md`（★，任务起始第一步必选）。

## 📄 License

见 [LICENSE](./LICENSE)。

## 🙏 致谢与使用约定

- 本包整理自 AI 短剧/漫剧工业化生产实践，供创作学习使用；
- 涉及真实武术流派、历史地点仅作创作参考；
- 输出内容需自行遵守所在平台的内容规范与版权要求。