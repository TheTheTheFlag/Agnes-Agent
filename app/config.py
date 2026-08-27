"""
app.config — 全局配置中心

统一管理所有路径、数据库位置、模型配置来源。
数据文件（memory.db / checkpoints.db / model_config）统一存放在 data/ 目录。
"""
import os

# 项目根目录（app/ 的上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据目录（数据库 + 模型配置）
DATA_DIR = os.path.join(BASE_DIR, "data")

# 静态资源目录（前端 HTML/CSS/JS）
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "static")

# 提示词模板
PROMPT_TEMPLATE_PATH = os.path.join(BASE_DIR, "app", "graph", "prompt_template.txt")

# 数据库
DB_PATH = os.path.join(DATA_DIR, "memory.db")            # 记忆 SQLite
CHECKPOINT_DB_PATH = os.path.join(DATA_DIR, "checkpoints.db")  # LangGraph checkpoint
MODEL_CONFIG_PATH = os.path.join(DATA_DIR, ".model_config")    # 模型接入/默认配置


def ensure_dirs():
    """确保必要的目录存在。"""
    for d in (DATA_DIR, STATIC_DIR):
        os.makedirs(d, exist_ok=True)
