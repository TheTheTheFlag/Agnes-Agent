"""app.server.api.kb — 知识库管理 API。

面向调试面板"知识库"tab：向量/图后端信息、文档管理（列表/分块/删除/重建/喂入）、检索测试。
所有读写都走 app.memory.ligraphrag_adapter（worker 事件循环桥 + 会话实例）。
受登录中间件保护。"""
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.memory import ligraphrag_adapter as L
from app.server.api.upload import UPLOAD_DIR

router = APIRouter(prefix="/api/kb", tags=["kb"])


class IngestReq(BaseModel):
    thread_id: str
    texts: list[str] = []


class DeleteReq(BaseModel):
    thread_id: str
    doc_id: str


class ReprocessReq(BaseModel):
    thread_id: str
    doc_id: str


class IngestFileReq(BaseModel):
    thread_id: str
    path: str  # uploads/ 下的相对路径


@router.get("/threads")
async def kb_threads() -> dict:
    return {"threads": L.kb_threads()}


@router.get("/status")
async def status(thread_id: str = Query(...)) -> dict:
    try:
        return L.kb_status(thread_id)
    except Exception as e:
        return JSONResponse({"error": f"读取知识库状态失败: {e}"}, status_code=500)


@router.get("/documents")
async def documents(thread_id: str = Query(...), page: int = Query(1), page_size: int = Query(50)) -> dict:
    try:
        return L.kb_documents(thread_id, page=page, page_size=page_size)
    except Exception as e:
        return JSONResponse({"error": f"读取文档列表失败: {e}"}, status_code=500)


@router.get("/chunks")
async def chunks(thread_id: str = Query(...), doc_id: str = Query("")) -> dict:
    try:
        return L.kb_chunks(thread_id, doc_id)
    except Exception as e:
        return JSONResponse({"error": f"读取分块失败: {e}"}, status_code=500)


@router.get("/search")
async def search(thread_id: str = Query(...), q: str = Query(""), top_k: int = Query(8)) -> dict:
    try:
        return L.kb_search(thread_id, q, top_k=top_k)
    except Exception as e:
        return JSONResponse({"error": f"检索失败: {e}"}, status_code=500)


@router.post("/ingest")
async def ingest(req: IngestReq) -> dict:
    return L.kb_ingest(req.thread_id, req.texts)


@router.post("/delete")
async def delete(req: DeleteReq) -> dict:
    return L.kb_delete_doc(req.thread_id, req.doc_id)


@router.post("/reprocess")
async def reprocess(req: ReprocessReq) -> dict:
    return L.kb_reprocess(req.thread_id, req.doc_id)


@router.post("/ingest_file")
async def ingest_file(req: IngestFileReq) -> dict:
    """把 uploads/ 下的文件内容喂入知识库。

    纯文本类（txt/md/json/csv/html）直接读取；
    其余常见文档（pdf/docx/pptx/xlsx/xls/epub/ipynb）经 markitdown
    转成 Markdown 后再建库。
    """
    path = (req.path or "").replace("\\", "/")
    if path.startswith("uploads/"):
        path = path[len("uploads/"):]
    if path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="非法路径")
    fp = os.path.join(UPLOAD_DIR, path)
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="文件不存在")
    ext = os.path.splitext(path)[1].lower()
    try:
        content = _read_document_as_markdown(fp, ext)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析文件失败: {e}")
    if not content.strip():
        raise HTTPException(status_code=400, detail="文件内容为空（扫描件或无文本层的文件暂无法处理）")
    return L.kb_ingest(req.thread_id, [content])


_SAFE_TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".html", ".htm"}
_MARKITDOWN_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".epub", ".ipynb"}


def _read_document_as_markdown(fp: str, ext: str) -> str:
    """读取文件为 Markdown 文本：文本类直读，文档类经 markitdown 转换。"""
    if ext in _SAFE_TEXT_EXTS:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    if ext not in _MARKITDOWN_EXTS:
        raise HTTPException(status_code=400, detail=f"暂不支持 {ext or '无扩展名'} 类型")
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(fp)
            pages = []
            for page in reader.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:
                    pages.append("")
            text = "\n".join(pages)
        except Exception:
            text = ""
        if text.strip():
            return text
    try:
        from markitdown import MarkItDown
        res = MarkItDown().convert(fp)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{ext} 转换失败（markitdown）: {e}")
    if res is None or not (getattr(res, "text_content", "") or "").strip():
        raise HTTPException(status_code=400, detail="未能从该文件提取文本（可能是扫描件/加密文件）")
    return res.text_content


class CreateReq(BaseModel):
    thread_id: str = ""
    name: str = ""
    description: str = ""


class ConfigSetReq(BaseModel):
    thread_id: str
    config: dict


class ImportURLReq(BaseModel):
    thread_id: str
    url: str


class DeleteKBReq(BaseModel):
    thread_id: str


class FeedbackReq(BaseModel):
    thread_id: str
    query: str
    answer: str = ""
    verdict: str
    top_k: int = 0
    mode: str = ""
    note: str = ""


class ChunkDeleteReq(BaseModel):
    thread_id: str
    chunk_key: str


class ChunkEditReq(BaseModel):
    thread_id: str
    chunk_key: str
    content: str
    tags: str = ""


class AnswerReq(BaseModel):
    thread_id: str
    q: str
    top_k: int = 8
    mode: str = "hybrid"


@router.get("/list")
async def list_kbs() -> dict:
    try:
        return {"knowledge_bases": L.kb_list()}
    except Exception as e:
        return JSONResponse({"error": f"读取知识库列表失败: {e}"}, status_code=500)


@router.get("/dashboard")
async def dashboard() -> dict:
    try:
        return L.kb_dashboard()
    except Exception as e:
        return JSONResponse({"error": f"读取大盘失败: {e}"}, status_code=500)


@router.post("/create")
async def create(req: CreateReq) -> dict:
    return L.kb_create(req.thread_id or None, req.name, req.description)


@router.get("/config")
async def get_config(thread_id: str = Query(...)) -> dict:
    return {"config": L.kb_get_config(thread_id)}


@router.post("/config")
async def set_config(req: ConfigSetReq) -> dict:
    return {"config": L.kb_set_config(req.thread_id, req.config or {})}


@router.post("/import_url")
async def import_url(req: ImportURLReq) -> dict:
    return L.kb_import_url(req.thread_id, req.url)


@router.post("/delete_kb")
async def delete_kb(req: DeleteKBReq) -> dict:
    return L.kb_delete_thread(req.thread_id)


@router.post("/feedback")
async def feedback(req: FeedbackReq) -> dict:
    return L.kb_feedback(req.thread_id, req.query, req.answer, req.verdict,
                         top_k=req.top_k, mode=req.mode, note=req.note)


@router.get("/feedback")
async def feedback_list(thread_id: Optional[str] = None, limit: int = Query(100)) -> dict:
    return {"items": L.kb_feedback_list(thread_id, limit)}


@router.post("/chunk_delete")
async def chunk_delete(req: ChunkDeleteReq) -> dict:
    return L.kb_delete_chunk(req.thread_id, req.chunk_key)


@router.post("/chunk_edit")
async def chunk_edit(req: ChunkEditReq) -> dict:
    return L.kb_edit_chunk(req.thread_id, req.chunk_key, req.content, req.tags)


@router.post("/answer")
async def answer(req: AnswerReq) -> dict:
    import time as _t
    t0 = _t.time()
    try:
        out = L.lightrag_query(req.thread_id, req.q, top_k=req.top_k, mode=req.mode)
        return {"answer": str(out), "mode": req.mode, "top_k": req.top_k,
                "elapsed_ms": int((_t.time() - t0) * 1000), "thread_id": req.thread_id}
    except Exception as e:
        return JSONResponse({"error": f"问答失败: {e}"}, status_code=500)