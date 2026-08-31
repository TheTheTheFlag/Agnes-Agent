import os

class Config:
    """应用配置"""
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    DATA_DIR = os.path.join(BASE_DIR, 'data')
    POSTS_FILE = os.path.join(DATA_DIR, 'posts.json')
    
    # 确保数据目录存在
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # 博客配置
    BLOG_TITLE = "我的博客"
    BLOG_SUBTITLE = "分享技术与生活"
    BLOG_AUTHOR = "博主"
    
    # 分页配置
    POSTS_PER_PAGE = 10
