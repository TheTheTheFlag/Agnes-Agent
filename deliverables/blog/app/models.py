"""数据模型"""
import json
import uuid
from datetime import datetime
from config import Config


class Post:
    """文章模型"""
    
    @staticmethod
    def _load_posts():
        """加载所有文章"""
        try:
            with open(Config.POSTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
    
    @staticmethod
    def _save_posts(posts):
        """保存文章列表"""
        with open(Config.POSTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_all(cls):
        """获取所有已发布的文章，按时间倒序"""
        posts = cls._load_posts()
        return sorted(posts, key=lambda x: x.get('created_at', ''), reverse=True)
    
    @classmethod
    def get_by_id(cls, post_id):
        """根据ID获取文章"""
        posts = cls._load_posts()
        for post in posts:
            if post.get('id') == post_id:
                return post
        return None
    
    @classmethod
    def create(cls, title, content, author, tags=None, excerpt=None):
        """创建新文章"""
        posts = cls._load_posts()
        now = datetime.now().isoformat()
        
        post = {
            'id': str(uuid.uuid4())[:8],
            'title': title,
            'content': content,
            'excerpt': excerpt or content[:200] + '...' if len(content) > 200 else content,
            'author': author,
            'created_at': now,
            'updated_at': now,
            'tags': tags or [],
            'published': True
        }
        
        posts.append(post)
        cls._save_posts(posts)
        return post
    
    @classmethod
    def update(cls, post_id, **kwargs):
        """更新文章"""
        posts = cls._load_posts()
        for i, post in enumerate(posts):
            if post.get('id') == post_id:
                posts[i].update(kwargs)
                posts[i]['updated_at'] = datetime.now().isoformat()
                cls._save_posts(posts)
                return posts[i]
        return None
    
    @classmethod
    def delete(cls, post_id):
        """删除文章"""
        posts = cls._load_posts()
        posts = [p for p in posts if p.get('id') != post_id]
        cls._save_posts(posts)
        return True
