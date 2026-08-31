"""路由定义"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
import markdown
from models import Post

bp = Blueprint('blog', __name__)


@bp.route('/')
def index():
    """首页"""
    posts = Post.get_all()
    return render_template('index.html', posts=posts)


@bp.route('/post/<post_id>')
def post(post_id):
    """文章详情"""
    post = Post.get_by_id(post_id)
    if not post:
        return "文章不存在", 404
    
    # 渲染Markdown
    post['html_content'] = markdown.markdown(post['content'])
    return render_template('post.html', post=post)


@bp.route('/new')
def new():
    """新建文章页面"""
    return render_template('editor.html', post=None)


@bp.route('/edit/<post_id>')
def edit(post_id):
    """编辑文章页面"""
    post = Post.get_by_id(post_id)
    if not post:
        return "文章不存在", 404
    return render_template('editor.html', post=post)


@bp.route('/api/posts', methods=['GET'])
def api_get_posts():
    """获取文章列表API"""
    posts = Post.get_all()
    return jsonify(posts)


@bp.route('/api/posts', methods=['POST'])
def api_create_post():
    """创建文章API"""
    data = request.get_json()
    post = Post.create(
        title=data.get('title', ''),
        content=data.get('content', ''),
        author=data.get('author', '匿名'),
        tags=data.get('tags', [])
    )
    return jsonify(post), 201


@bp.route('/api/posts/<post_id>', methods=['PUT'])
def api_update_post(post_id):
    """更新文章API"""
    data = request.get_json()
    post = Post.update(post_id, **data)
    if post:
        return jsonify(post)
    return jsonify({'error': '文章不存在'}), 404


@bp.route('/api/posts/<post_id>', methods=['DELETE'])
def api_delete_post(post_id):
    """删除文章API"""
    Post.delete(post_id)
    return jsonify({'success': True})


@bp.route('/about')
def about():
    """关于页面"""
    return render_template('about.html')
