# 技术选型文档

## 项目概述
网页版博客系统 - 简单、轻量、易于部署

## 技术栈选择

### 前端
- **HTML5** - 语义化标记
- **CSS3** - 现代布局（Flexbox/Grid）、响应式设计
- **JavaScript (ES6+)** - 原生JS，无框架依赖
- **Markdown渲染** - marked.js

### 后端
- **Python 3.10+**
- **Flask** - 轻量级Web框架
- **Jinja2** - 模板引擎

### 数据存储
- **JSON文件** - posts.json 存储文章数据
- **本地文件系统** - 保存上传的静态资源

## 目录结构

```
blog/
├── app/
│   ├── __init__.py      # Flask应用工厂
│   ├── routes.py         # 路由定义
│   ├── models.py         # 数据模型
│   └── utils.py          # 工具函数
├── static/
│   ├── css/
│   │   └── style.css     # 样式文件
│   └── js/
│       └── main.js       # 前端脚本
├── templates/
│   ├── base.html         # 基础模板
│   ├── index.html        # 首页
│   ├── post.html         # 文章详情
│   ├── editor.html       # 编辑器
│   └── about.html        # 关于页面
├── data/
│   └── posts.json        # 文章数据
├── config.py             # 配置文件
├── run.py                # 启动脚本
└── requirements.txt      # 依赖列表
```

## API 设计

### 文章接口
- `GET /` - 首页，列出所有文章
- `GET /post/<id>` - 查看文章详情
- `GET /new` - 新建文章页面
- `POST /api/posts` - 创建文章
- `PUT /api/posts/<id>` - 更新文章
- `DELETE /api/posts/<id>` - 删除文章

### 辅助接口
- `GET /about` - 关于页面
- `GET /api/posts` - 获取文章列表JSON

## 数据模型

### Post (文章)
```json
{
  "id": "uuid",
  "title": "标题",
  "content": "Markdown内容",
  "excerpt": "摘要",
  "author": "作者",
  "created_at": "ISO时间戳",
  "updated_at": "ISO时间戳",
  "tags": ["标签1", "标签2"],
  "published": true
}
```

## 依赖包

```
Flask>=2.3.0
markdown>=3.4.0
uuid>=1.30
python-dateutil>=2.8.0
```

## 部署方式
- 开发环境：Flask内置服务器
- 生产环境：Gunicorn + Nginx
