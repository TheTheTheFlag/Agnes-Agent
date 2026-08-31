"""工具函数"""
import re


def extract_excerpt(content, length=200):
    """从内容中提取摘要"""
    # 移除Markdown语法
    text = re.sub(r'[#*_`\[\]()>]', '', content)
    text = re.sub(r'\n+', ' ', text)
    text = text.strip()
    
    if len(text) > length:
        return text[:length] + '...'
    return text


def generate_slug(title):
    """从标题生成URL友好的slug"""
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\u4e00-\u9fa5]', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')
