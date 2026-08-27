import os
from dotenv import load_dotenv
load_dotenv()                     # 确保环境变量已加载

from langchain_tavily import TavilySearch

tavily_tool = TavilySearch(max_results=2)