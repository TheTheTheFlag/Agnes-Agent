"""临时复现脚本：观察 langgraph messages 流对非流式 invoke 的 tool_calls 如何投递。"""
import sys
sys.path.insert(0, r"C:\Users\17625\Agnes-Agent")

from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END, MessagesState
from langchain_core.messages import AIMessageChunk

@tool
def fake_tool(query: str = "x") -> str:
    """fake tool"""
    return f"result for {query}"

class ToolCallingModel:
    """模拟非流式 invoke：第一轮返回 tool_call，第二轮返回纯文本。"""
    def __init__(self):
        self.calls = 0
    def bind_tools(self, tools, **kw):
        return self
    def invoke(self, messages, **kw):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[{"name": "fake_tool", "args": {"query": "hello"}, "id": "call_1", "type": "tool_call"}],
            )
        return AIMessage(content="done")

model = ToolCallingModel()

def node(state):
    msgs = state["messages"]
    last = msgs[-1]
    if getattr(last, "tool_calls", None):
        tool_calls = last.tool_calls
        for tc in tool_calls:
            msgs.append(ToolMessage(content="ok", tool_call_id=tc["id"]))
        return {"messages": msgs}
    if getattr(last, "type", "") == "human":
        resp = model.invoke(msgs)
        return {"messages": [resp]}
    return {"messages": []}

g = StateGraph(MessagesState)
g.add_node("chatbot", node)
g.add_edge(START, "chatbot")
g.add_edge("chatbot", END)
app = g.compile()

print("=" * 60)
for mode, payload in app.stream({"messages": [HumanMessage(content="hi")]}, stream_mode=["updates", "messages"]):
    if mode == "messages":
        chunk, meta = payload
        print("--- messages payload ---")
        print("  type:", type(chunk).__name__, "| isChunk:", isinstance(chunk, AIMessageChunk))
        print("  content:", repr(str(getattr(chunk, "content", ""))[:60]))
        print("  tool_calls:", getattr(chunk, "tool_calls", None))
        tcc = getattr(chunk, "tool_call_chunks", None)
        print("  tool_call_chunks:", [(getattr(c, "name", None), str(getattr(c, "args", ""))[:40], getattr(c, "id", None)) for c in (tcc or [])])
    else:
        print("--- updates payload:", payload, "---")
