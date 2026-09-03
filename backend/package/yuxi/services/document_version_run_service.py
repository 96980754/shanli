"""历史版本内容阅读/对比的 worker 运行路径。

问答引用文档存在历史版本且提问命中版本意图时，前端在注记条内让用户就地选择
「查看某一历史版本」或「对比两个版本（可含当前版）」，随后以 chat run + 结构化
version_ask 载荷发起一轮新回答。本模块实现载荷模型与 worker 生成器：

- 按 file_id 读取归档版本正文（复用 DocumentDiffService.get_version_text，归档行
  保留 MinIO markdown_file，仅删 Milvus 向量，内容不重定向当前版）；
- 一次模型调用生成回答（read 结构化总结 / compare 逐段对比），落库为单条
  assistant 消息，并以合成 query_kbs 工具调用承载来源，供前端来源面板如实展示
  被引用的归档文件；
- 归档内容全程不进普通 agent 工具集（封闭单用途 run，镜像 curated QA 范式）。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.document_diff_service import (
    DocumentDiffNotFoundError,
    DocumentDiffService,
)
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.utils import logger

# 单次问答可喂给模型的版本正文总量（字符）；compare 时按文件平分。超出部分保头尾丢中段，
# 由模型感知并追加省略说明，避免超长文档直接拖垮上下文。
_DEFAULT_MAX_INPUT_CHARS = 60000


def _max_input_chars() -> int:
    try:
        return max(1000, int(os.getenv("VERSION_ASK_MAX_INPUT_CHARS", str(_DEFAULT_MAX_INPUT_CHARS))))
    except ValueError:
        return _DEFAULT_MAX_INPUT_CHARS


class DocumentVersionFile(BaseModel):
    file_id: str
    document_version: int | float | str | None = None
    filename: str = ""
    is_current: bool = False


class DocumentVersionAskRequest(BaseModel):
    kb_id: str
    action: Literal["read", "compare"]
    file_ids: list[str] = Field(min_length=1, max_length=2)
    title: str | None = None
    versions: list[DocumentVersionFile] = Field(default_factory=list)

    def validate_action(self) -> str | None:
        """校验结构化请求自洽；不合法时返回用户可读的错误信息，合法返回 None。"""
        if not self.file_ids:
            return "历史版本查询缺少 file_id"
        if self.action == "read" and len(self.file_ids) != 1:
            return "查看单个版本时 file_ids 必须恰好 1 个"
        if self.action == "compare":
            if len(self.file_ids) != 2:
                return "对比两个版本时 file_ids 必须恰好 2 个"
            if self.file_ids[0] == self.file_ids[1]:
                return "对比的两个版本不能相同"
        return None


def _head_tail_truncate(text: str, max_chars: int, front_ratio: float = 0.6) -> tuple[str, bool]:
    """正文过长时保头尾、丢中段：保留可读开头 + 结尾，其余以占位说明替代。"""
    if len(text) <= max_chars:
        return text, False
    head_len = int(max_chars * front_ratio)
    tail_len = max_chars - head_len
    omitted = f"\n\n[正文过长，中段约 {len(text) - max_chars} 字符已省略，其余内容略]"
    return text[:head_len] + omitted + text[-tail_len:], True


def _version_label(doc: dict[str, Any]) -> str:
    label = f"《{doc['filename']}》"
    version = doc.get("document_version")
    if version is not None and str(version).strip() != "":
        label += f"（V{version}）"
    label += "（当前版本）" if doc.get("is_current") else "（历史版本，已被替换归档）"
    return label


def _build_document_blocks(docs: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, doc in enumerate(docs, start=1):
        blocks.append(f"<document id={index}>\n文件名标签：{_version_label(doc)}\n正文：\n{doc['text']}\n</document>")
    return "\n\n".join(blocks)


async def compose_document_version_answer(
    *,
    model_spec: str,
    query: str,
    version_ask: dict[str, Any],
    docs: list[dict[str, Any]],
) -> str:
    """让模型基于指定版本正文组织回答：read=忠实概括某历史版；compare=逐段对比两个版本。"""
    from yuxi.models.chat import select_model

    names = "、".join(_version_label(doc) for doc in docs)
    if version_ask.get("action") == "compare":
        system = (
            "你是企业知识库助手。下面是同一文档家族的两个版本全文。"
            f"请对 {names} 做逐章对比：对每个有内容的章节，分别给出两版的内容要点与差异，"
            "并标注差异结论（仅四种：新增 / 删除 / 修改 / 无变化）。"
            "回答结构：先逐段对比（每段含「章节 / 版本A要点 / 版本B要点 / 差异结论」），"
            "再给一张汇总表（章节、差异结论、改动简述），最后写总体结论。"
            "只依据给定正文，不推断版本正文之外的内容；引用文档时用《文件名》标注。"
            "始终使用与用户请求一致的语言回答。"
        )
    else:
        system = (
            "你是企业知识库助手。下面是用户指定查看的历史版本全文"
            "（该版本已被新版本替换归档，不代表当前生效内容）。"
            f"请忠实阅读 {names} 的正文，按用户请求组织回答：若请求是了解该版本内容，"
            "请给出有条理、保留章节结构的内容概括，不要与其它版本的内容混合。"
            f"回答开头须注明依据版本（例如：本回答基于历史版本《{docs[0]['filename']}》，不代表当前版本）。"
            "引用文档时用《文件名》标注。始终使用与用户请求一致的语言回答。"
        )

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"用户请求：{query}\n\n需读取的版本正文：\n{_build_document_blocks(docs)}",
        },
    ]
    response = await select_model(model_spec).call(messages, stream=False)
    return str((response and response.content) or "").strip()


async def _load_version_docs(version_ask: dict[str, Any]) -> list[dict[str, Any]]:
    """按 file_id 逐个读取版本正文；内容访问严格按传入 file_id，不重定向当前版。"""
    version_by_id = {version.get("file_id"): version for version in (version_ask.get("versions") or [])}
    service = DocumentDiffService()
    docs: list[dict[str, Any]] = []
    for file_id in version_ask["file_ids"]:
        text = await service.get_version_text(kb_id=version_ask["kb_id"], file_id=file_id)
        if not str(text or "").strip():
            raise DocumentDiffNotFoundError(f"版本 {file_id} 没有可读取的正文内容")
        meta = version_by_id.get(file_id, {})
        docs.append(
            {
                "file_id": file_id,
                "filename": str(meta.get("filename") or file_id),
                "document_version": meta.get("document_version"),
                "is_current": bool(meta.get("is_current")),
                "text": text,
            }
        )
    return docs


def _truncate_docs_for_budget(
    docs: list[dict[str, Any]], action: str
) -> list[dict[str, Any]]:
    """按上下文预算截断正文：read 单文档整预算，compare 每侧平分，并记录是否被截断。"""
    budget = _max_input_chars()
    per_doc = budget if len(docs) == 1 else budget // len(docs)
    truncated_docs: list[dict[str, Any]] = []
    for doc in docs:
        text, truncated = _head_tail_truncate(doc["text"], per_doc)
        truncated_docs.append({**doc, "text": text, "truncated": truncated})
    return truncated_docs


def _append_truncation_note(answer: str, docs: list[dict[str, Any]]) -> str:
    names = "、".join(f"《{doc['filename']}》" for doc in docs if doc.get("truncated"))
    if not names:
        return answer
    count = len([d for d in docs if d.get("truncated")])
    note = (
        f"（说明：{names} 正文过长，本次 {count} 个版本的中段未纳入整理，"
        "完整内容请在文件侧版本历史中查看。）"
    )
    return f"{answer}\n\n{note}"


async def _attach_version_sources(
    *,
    db: AsyncSession,
    message_id: int,
    query: str,
    kb_id: str,
    docs: list[dict[str, Any]],
) -> None:
    """把被读取的版本文件以 query_kbs 结果挂到回答消息上，供前端来源面板展示。"""
    from yuxi.knowledge.schemas import SearchOutputSchema, SearchResultSchema

    results = [
        SearchResultSchema(
            id=f"doc-version-{doc['file_id']}",
            kb_id=kb_id,
            file_id=doc["file_id"],
            content=str(doc["text"][:120]).strip() or "（正文过长，见文件侧完整内容）",
            metadata={
                "source": doc["filename"],
                "file_id": doc["file_id"],
                "document_version": doc["document_version"],
            },
        )
        for doc in docs
    ]
    payload = SearchOutputSchema(schema_version=1, status="ok", kb_id=kb_id, results=results).model_dump()
    await ConversationRepository(db).add_tool_call(
        message_id=message_id,
        tool_name="query_kbs",
        tool_input={"query_text": query, "kb_ids": [kb_id]},
        tool_output=json.dumps(payload, ensure_ascii=False),
        status="success",
    )


async def stream_document_version_answer(
    *,
    agent_slug: str,
    thread_id: str,
    meta: dict,
    input_message: AgentRunInputMessage,
    current_user,
    db: AsyncSession,
) -> AsyncIterator[bytes]:
    """worker 流式执行历史版本阅读/对比 run：读归档正文 → 一次模型调用 → 落库单条回答。

    事件顺序与普通 agent 流一致：init → tool-started(读取历史版本胶囊) →
    message_delta(回答正文) → finished。胶囊在模型组织回答期间持续显示，
    回答在流末尾一次性落库为单条 assistant 消息（与历史 reload 兼容），
    失败路径不落任何半截消息。
    """
    meta = dict(meta or {})
    run_id = str(meta.get("run_id") or "")
    request_id = str(meta.get("request_id") or "")
    model_spec = str(meta.get("model_spec") or "")
    version_ask = meta.get("version_ask") or {}
    stream_message_id = f"doc-version-{run_id}"

    def make_chunk(content=None, **kwargs):
        return (
            json.dumps(
                {"request_id": request_id, "response": content, "thread_id": thread_id, **kwargs},
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )

    def _error(error_type: str, error_message: str):
        logger.error("历史版本 run %s 失败（%s）: %s", run_id, error_type, error_message)
        return make_chunk(status="error", error_type=error_type, error_message=error_message, meta=meta)

    yield make_chunk(
        status="init",
        meta=meta,
        msg={
            "role": "user",
            "content": input_message.content,
            "type": "human",
            "message_type": input_message.message_type,
            "extra_metadata": {"request_id": request_id},
        },
    )
    # 先广播工具开始，让前端在读取正文与模型组织回答期间显示「正在读取历史版本…」胶囊。
    yield make_chunk(
        status="stream_event",
        event={
            "method": "tools",
            "data": {
                "event": "tool-started",
                "tool_name": "read_document_version",
                "tool_call_id": f"doc-version-read-{run_id}",
            },
        },
        namespace=[],
        meta=meta,
    )

    try:
        if not version_ask:
            raise ValueError("version_ask 载荷缺失")
        docs = await _load_version_docs(version_ask)
        docs = _truncate_docs_for_budget(docs, version_ask.get("action") or "read")
        answer = await compose_document_version_answer(
            model_spec=model_spec,
            query=input_message.content,
            version_ask=version_ask,
            docs=docs,
        )
        if any(doc.get("truncated") for doc in docs):
            answer = _append_truncation_note(answer, docs)
        if not answer:
            raise ValueError("历史版本回答生成结果为空")
    except DocumentDiffNotFoundError as exc:
        yield _error("document_version_source_unavailable", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        yield _error("document_version_compose_failed", f"历史版本内容生成失败：{exc}")
        return

    yield make_chunk(
        content=answer,
        status="loading",
        stream_event={
            "type": "message_delta",
            "message_id": stream_message_id,
            "content": answer,
            "thread_id": thread_id,
            "namespace": [],
        },
        metadata={},
    )

    try:
        answer_metadata = {
            "id": stream_message_id,
            "type": "ai",
            "role": "assistant",
            "content": answer,
            "document_version_answer": True,
            "document_version_meta": {
                "kb_id": version_ask.get("kb_id"),
                "title": version_ask.get("title"),
                "action": version_ask.get("action"),
                "file_ids": version_ask.get("file_ids"),
            },
        }
        assistant_message = await ConversationRepository(db).add_message_by_thread_id(
            thread_id=thread_id,
            role="assistant",
            content=answer,
            message_type="text",
            extra_metadata=answer_metadata,
            run_id=run_id,
            request_id=request_id,
        )
        if assistant_message is None:
            raise RuntimeError("历史版本回答保存失败")
        await _attach_version_sources(
            db=db,
            message_id=assistant_message.id,
            query=input_message.content,
            kb_id=version_ask.get("kb_id") or "",
            docs=docs,
        )
        run_repo = AgentRunRepository(db)
        await run_repo.set_output_message(run_id, assistant_message.id)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        yield _error("document_version_persist_failed", f"历史版本回答保存失败：{exc}")
        return

    yield make_chunk(status="finished", meta=meta)
