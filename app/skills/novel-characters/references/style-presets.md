# 画风预设

出图风格可选。**默认 `photoreal`**（真人写实·超写实国风），想要动画质感就换 2D / 3D 类预设。

预设定义在 `scripts/novel-characters.mjs` 的 `STYLE_PRESETS` 里，跑
`node scripts/novel-characters.mjs styles` 可以把整段打出来直接用。

| id | 说明 | 类别 |
| --- | --- | --- |
| `photoreal` | 真人写实·超写实国风，8K 纹理、自然光、布料褶皱。默认 | real |
| `inkwash` | 水墨风（shuimo），留白与晕染、纸面肌理 | 2d |
| `guofeng` | 国风厚涂，古典服饰与堆色，古风 / 玄幻顶配 | 2d |
| `cyberpunk` | 赛博朋克霓虹雨夜、高对比、冷青品红 | real |
| `photography` | 写实摄影，RAW 质感、电影调色、自然肌肤 | real |
| `ghibli` | 吉卜力式手绘赛璐璐，等宽墨线、单层柔和阴影、平涂色块 | 2d |
| `pixar3d` | 3D 影视级卡通（皮克斯 / 盲盒潮玩质感），AI 漫剧主流 | 3d |
| `disney3d` | 迪士尼式 3D 动画，光泽大眼、温暖家庭片质感 | 3d |
| `webtoon` | 韩漫彩漫，半透明光泽上色，现代都市首选 | 2d |
| `shinkai` | 新海诚式电影感日漫，天光体积光，青春情感题材 | 2d |
| `chibi` | Q 版萌系，大头小身、干净粗线、可爱配色 | 2d |

## ⚠️ 换风格是整套换，不是只换一句「画风」

每个预设自带六块：`class`（real / 3d / 2d）+ `render` / `surface` / `lighting` / `negative` / `tags`。
**必须整块取用，不要混搭**：

| 块 | 作用 |
| --- | --- |
| `class` | 类别：real / 3d / 2d，决定反向提示词方向 |
| `render` | 渲染方式那句 |
| `surface` | 皮肤、眼睛、头发、布料怎么处理 |
| `lighting` | 光照 |
| `negative` | 反向提示词 |
| `tags` | 风格标签 |

**类别与反向提示词方向一致**（`validate` 会按 class 检查）：

- `real` 类（`photoreal` / `photography` / `cyberpunk`）**绝不能**禁 `photorealistic` / `3d render`——
  一边要真实感一边禁真实感是自相矛盾的，它禁的是「假」（塑料皮肤、无毛孔娃娃脸、完全对称的脸）
- `pixar3d` / `disney3d`（3D 类）**绝不能**禁 `3d render`——它本身就是 3D；但「写实摄影感」要禁
- `ghibli / inkwash / guofeng / webtoon / shinkai / chibi`（2D / 动画）**必须**禁
  `photorealistic` / `3d render` ——写实细节在这里全是反效果

`surface` 同理。写实要毛孔、皮下散射、根根分明的碎发、布料织纹；吉卜力明确要
**无毛孔、无皮肤纹理、成簇的发丝、平涂无织纹的布料**。把写实那段带进吉卜力，出来就是个四不像。

## 光照的差别

- `real` 类 **分区打光**：左栏半身像给方向主光 + 环境遮蔽（要体积），右侧三视图和细节条平光正交（要抠图和量比例）
- 2D / 3D 类 **全图平光**：均匀日光 + 单层柔和阴影，或整体柔和的 studio 光。平光本来就是这个风格的一部分，不需要分区

## 版面不随风格变

16:9 三区、34%/66% 比例、人物比例协调、细节让位不是人物让位——这些是**版面规则**，
所有风格都一样。变的只有渲染质感。

## 加新预设

往 `STYLE_PRESETS` 里加一个键，`class` + 五块写全，`label` 给齐三语。自测会检查完整性。
