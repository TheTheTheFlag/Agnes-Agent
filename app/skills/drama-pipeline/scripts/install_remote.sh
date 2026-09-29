#!/bin/bash
# 安装 QA/拼接脚本到 /home/mirror/stage-kzqs/，改前备份；带非空+语法断言
set -u
SRC=/tmp/qa_fix
DST=/home/mirror/stage-kzqs
TS=$(date +%Y%m%d)
mkdir -p "$DST" || exit 2

for f in qa_manifest.sh qa_videos.sh concat_episodes.sh qa_deliver.sh; do
  [ -f "$SRC/$f" ] || { echo "!! 缺源文件 $SRC/$f"; exit 2; }
  sz=$(stat -c%s "$SRC/$f" 2>/dev/null || echo 0)
  [ "$sz" -gt 200 ] || { echo "!! 源文件过小($sz B) 疑似传输失败: $f"; exit 2; }
  bash -n "$SRC/$f" || { echo "!! 源文件语法错: $f"; exit 2; }
  if [ -f "$DST/$f" ]; then
    cp -p "$DST/$f" "$DST/$f.bak-$TS" 2>/dev/null
    echo "备份 $f -> $f.bak-$TS ($(stat -c%s "$DST/$f") B)"
  fi
  cp "$SRC/$f" "$DST/$f" && chmod +x "$DST/$f" || { echo "!! 安装失败 $f"; exit 1; }
  echo "安装 $f ($(stat -c%s "$DST/$f") B)"
done

echo "=== 断言复检 ==="
for f in qa_manifest.sh qa_videos.sh concat_episodes.sh qa_deliver.sh; do
  bash -n "$DST/$f" || { echo "  语法 FAIL: $f"; exit 1; }
  n=$(stat -c%s "$DST/$f")
  [ "$n" -gt 200 ] || { echo "  过小 FAIL: $f"; exit 1; }
  echo "  OK $f ($n B)"
done
if grep -q '困在七岁' "$DST"/qa_manifest.sh "$DST"/qa_videos.sh "$DST"/concat_episodes.sh "$DST"/qa_deliver.sh 2>/dev/null; then
  echo "  !! 仍有写死项目路径"; exit 1
fi
echo "  已清除写死项目路径 OK"
echo
echo "=== 禁止通配符枚举段目录自检（剥掉注释后查代码）==="
bad=0
for f in qa_videos.sh concat_episodes.sh; do
  # 先用 sed 去掉行内注释，避免命中「这里警告不能用 -0*」的说明文字
  if sed 's/#.*//' "$DST/$f" | grep -n -- '-0\*'; then
    echo "  !! $f 代码里仍存在 -0* 通配（会漏 -10 及以后）"; bad=1
  fi
done
[ "$bad" = "0" ] || exit 1
echo "  代码中无 -0* 通配 OK"
