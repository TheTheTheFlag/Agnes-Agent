# media_index.json 规范

> 来源：zhaihao118/Micro-Drama-Skills 生产仓实测（`DM-001/DM-002/media_index.json`）
>
> 用途：项目根级的全局媒体文件索引，方便 QA / 人工核对 / 断点续传。
> 不是必须，但强烈建议每项目都建。

## 文件位置

```
<项目根>/media_index.json
```

## 字段规范

```json
{
  "project_id": "HUAYU-001",
  "project_name": "花语战歌",
  "style_id": "STYLE-11",
  "created_date": "2026-09-28",
  "last_updated": "2026-09-28",
  "characters": [
    {
      "filename": "chr-001.png",
      "path": "assets/chr-001.png",
      "size_bytes": 1234567,
      "role": "主角·花铃"
    }
  ],
  "scenes": [
    {
      "filename": "scene-01.png",
      "path": "assets/scene-01.png",
      "size_bytes": 2345678,
      "appearance": ["EP01-01", "EP01-03"]
    }
  ],
  "episodes": [
    {
      "episode": "E01",
      "total_duration_seconds": 176,
      "segments": 12,
      "files": [
        {
          "filename": "E01-01.mp4",
          "path": "投喂稿/E01/E01-01.mp4",
          "type": "video",
          "size_bytes": 5678901,
          "duration_seconds": 14.25,
          "resolution": "1280x704",
          "refs": ["chr-001.png", "scene-01.png"]
        }
      ]
    }
  ],
  "totals": {
    "character_refs": 3,
    "scene_refs": 5,
    "total_segments": 12,
    "total_videos": 12,
    "total_keyframes": 24,
    "total_size_bytes": 123456789
  }
}
```

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_id` | string | ✅ | 项目唯一标识，如 `HUAYU-001` |
| `project_name` | string | ✅ | 项目名称 |
| `style_id` | string | ✅ | 选定的风格档，如 `STYLE-11` |
| `created_date` | string | ✅ | ISO 日期 |
| `last_updated` | string | ✅ | 每次新增媒体后更新 |
| `characters[]` | array | ✅ | 角色 ref 列表，含 path + size_bytes + role |
| `scenes[]` | array | 可选 | 场景 ref 列表，含 appearance（出现在哪些段） |
| `episodes[].files[]` | array | ✅ | 每段视频/关键帧的 file entry |
| `episodes[].files[].type` | enum | ✅ | `"video"` / `"keyframe"` / `"audio"` |
| `episodes[].files[].duration_seconds` | number | ✅ | 实际时长（秒） |
| `episodes[].files[].resolution` | string | ✅ | 实际分辨率，如 `"1280x704"` |
| `episodes[].files[].refs` | array | 可选 | 本段使用的角色/场景 ref 文件名 |
| `totals` | object | ✅ | 汇总统计，QA 快速核对用 |

## 维护时机

1. **每次新增角色 ref** → 追加到 `characters[]`
2. **每次新增场景 ref** → 追加到 `scenes[]`
3. **每生成一段视频** → 追加到对应 episode 的 `files[]`
4. **每生成一张关键帧** → 追加到对应 episode 的 `files[]`（type: "keyframe"）
5. **每次 QA 验收通过** → 更新 `totals` 和 `last_updated`

## 与投喂稿的关系

`media_index.json` 是**投影**，投喂稿是**源**。
- 投喂稿说「这段用了 chr-001」→ media_index 的 refs 字段记录
- 投喂稿说「这段时长 5s」→ media_index 的 duration_seconds 记录
- 两者以 `投喂稿/E01/manifest.json` 为桥梁对齐
