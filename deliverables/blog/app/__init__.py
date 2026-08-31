"""Flask应用工厂"""
from flask import Flask
from config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 注册蓝图
    from app.routes import bp
    app.register_blueprint(bp)
    
    return app
