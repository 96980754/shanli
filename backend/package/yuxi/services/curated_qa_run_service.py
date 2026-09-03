"""人工确认 QA 的直接命中运行路径。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.curated_qa_repository import CuratedQARepository
from yuxi.services.agent_run_service import (
    create_agent_run_input_message,
    enqueue_agent_run,
    persist_agent_run_record,
    prepare_agent_run_creation_scope,
    resolve_agent_run_model_spec,
)
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.storage.postgres.models_curated_qa import CuratedQAPair
from yuxi.utils import logger


def _run_response(run) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "thread_id": run.conversation_thread_id,
        "status": run.status,
        "request_id": run.request_id,
        "stream_url": f"/api/agent/runs/{run.id}/events",
    }


async def _semantic_match_curated_qa(
    qa_repo: CuratedQARepository, agent_slug: str, question: str
) -> CuratedQAPair | None:
    """精确匹配未命中时，按语义相近召回人工问答对；匹配失败只降级不阻断。"""
    try:
        from yuxi.services.curated_qa_semantic_matcher import CuratedQASemanticMatcher

        return await CuratedQASemanticMatcher(qa_repo).find_match(agent_slug=agent_slug, question=question)
    except Exception as exc:  # noqa: BLE001
        logger.warning("人工问答对语义匹配失败，走正常流程: %s", exc)
        return None


async def _compose_answer_from_reference(model_spec: str, question: str, qa_pair: CuratedQAPair) -> str:
    """以人工确认的问答对为参考，让大模型按用户问题原意组织回答。

    语义命中的问题表述与原问题不同，直接顶出原答案会显得答非所问；引导模型
    基于参考组织回答，同时明确要求参考不相关时如实拒答，避免编造。
    """
    from yuxi.models.chat import select_model

    messages = [
        {
            "role": "system",
            "content": (
                "你是企业知识库助手。请以提供的参考材料为基础回答用户问题；"
                "如果参考材料与问题不相关或不足以回答，请如实说明未检索到相关依据，不要编造。"
                "始终使用与用户问题一致的语言回答（用户问题为中文时用中文），不要跟随时参考材料的外语。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户问题：{question}\n\n"
                f"参考材料（管理员人工确认的问答）：\n"
                f"问：{qa_pair.question}\n答：{qa_pair.answer}"
            ),
        },
    ]
    try:
        response = await select_model(model_spec).call(messages, stream=False)
        composed = str((response and response.content) or "").strip()
        if composed:
            return composed
    except Exception as exc:  # noqa: BLE001
        logger.warning("人工问答对语义命中的引导生成失败，退回原答案: %s", exc)
    return qa_pair.answer


def _eligible_for_curated_qa(input_message: AgentRunInputMessage, meta: dict) -> bool:
    if input_message.message_type != "text" or input_message.image_content:
        return False
    if meta.get("attachment_file_ids"):
        return False
    if meta.get("source") == "agent_evaluation":
        return False
    return bool(str(input_message.content or "").strip())


async def try_create_curated_qa_run(
    *,
    input_message: AgentRunInputMessage,
    agent_slug: str,
    thread_id: str,
    meta: dict,
    current_uid: str,
    db: AsyncSession,
) -> dict[str, Any] | None:
    """命中人工 QA 时创建一条流式 run；未命中返回 None 走普通 Agent 流程。

    这里只做轻量命中检测与持久化：精确命中直接输出人工确认的原答案；语义命中
    （表述相近但字符不同）以人工答案为参考组织回答（answer_source=curated_qa_semantic），
    避免改述问题得到答非所问的死板复述。

    组装（基础答案/补充检索/事件流）整体由 worker 流式执行（见 stream_curated_qa_answer），
    模型调用与知识库检索因此落在 worker 容器，且补充检索期间前端能展示「正在查询知识库…」。
    run 与输入消息落库后投递 worker 队列即返回；终态与输出消息由 worker 在消费完成后标记。
    """
    meta = dict(meta or {})
    if not _eligible_for_curated_qa(input_message, meta):
        return None

    qa_repo = CuratedQARepository(db)
    qa_pair = await qa_repo.get_exact(agent_slug=agent_slug, question=input_message.content)
    answer_source = "curated_qa"
    if qa_pair is None:
        qa_pair = await _semantic_match_curated_qa(qa_repo, agent_slug, input_message.content)
        if qa_pair is None:
            return None
        answer_source = "curated_qa_semantic"

    request_id = str(meta.get("request_id") or uuid.uuid4())
    scope = await prepare_agent_run_creation_scope(
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
        current_uid=current_uid,
        db=db,
        request_id=request_id,
        run_type="chat",
        agent_kind="main",
    )
    if scope.existing_run:
        return _run_response(scope.existing_run)

    resolved_model_spec = resolve_agent_run_model_spec(None, scope.agent_item, scope.agent_backend)
    input_metadata: dict[str, Any] = {"request_id": request_id, "answer_source": answer_source}
    if raw_message := input_message.raw_message():
        input_metadata["raw_message"] = raw_message
    if source := meta.get("source"):
        input_metadata["source"] = source

    persisted_input_message = await create_agent_run_input_message(
        db=db,
        conversation_id=scope.conversation.id,
        request_id=request_id,
        input_message=input_message.with_metadata(input_metadata),
    )
    run, created = await persist_agent_run_record(
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
        current_uid=current_uid,
        db=db,
        request_id=request_id,
        conversation_id=scope.conversation.id,
        run_type="chat",
        input_payload={"model_spec": resolved_model_spec, "answer_source": answer_source, "curated_qa_id": qa_pair.id},
        persisted_input_message=persisted_input_message,
    )
    if not created:
        await db.commit()
        return _run_response(run)

    await db.commit()
    await enqueue_agent_run(run.id)
    return _run_response(run)


async def stream_curated_qa_answer(
    *,
    agent_slug: str,
    thread_id: str,
    meta: dict,
    input_message: AgentRunInputMessage,
    current_user,
    db: AsyncSession,
) -> AsyncIterator[bytes]:
    """worker 流式执行人工 QA 命中 run：先给基础答案，再补检索并归纳补充段落。

    事件顺序与普通 agent 流一致：init → message_delta(基础答案) → tool-started(检索胶囊)
    → message_delta(补充段落) → finished。胶囊在补充检索开始前广播，前端因此在检索与
    归纳期间持续显示「正在查询知识库…」，这是 QA 快答路径的体验关键。

    回答在流末尾一次性落库为单条 assistant 消息（与历史 reload 兼容，正文含补充段落），
    mark_hit 也只在落库成功后发生；检索或归纳任一步失败都降级为仅基础答案。
    """
    meta = dict(meta or {})
    run_id = str(meta.get("run_id") or "")
    request_id = str(meta.get("request_id") or "")
    uid = str(current_user.uid)
    model_spec = str(meta.get("model_spec") or "")
    answer_source = meta.get("answer_source") or "curated_qa"
    stream_message_id = f"curated-qa-{run_id}"

    def make_chunk(content=None, **kwargs):
        return (
            json.dumps(
                {"request_id": request_id, "response": content, "thread_id": thread_id, **kwargs},
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )

    qa_repo = CuratedQARepository(db)
    try:
        curated_qa_id = meta.get("curated_qa_id")
        qa_pair = curated_qa_id and await qa_repo.get(curated_qa_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("人工问答对加载失败（id=%s）: %s", meta.get("curated_qa_id"), exc)
        qa_pair = None
    if qa_pair is None:
        logger.warning("人工问答对不存在或已删除，跳过 QA 命中流式执行")
        yield make_chunk(
            status="error",
            error_type="curated_qa_missing",
            error_message="人工问答对不存在或已删除",
            meta=meta,
        )
        return

    scope = await prepare_agent_run_creation_scope(
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
        current_uid=uid,
        db=db,
        request_id=request_id,
        run_type="chat",
        agent_kind="main",
    )

    base_answer = (
        await _compose_answer_from_reference(model_spec, input_message.content, qa_pair)
        if answer_source == "curated_qa_semantic"
        else qa_pair.answer or ""
    )

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
    if base_answer:
        yield make_chunk(
            content=base_answer,
            status="loading",
            stream_event={
                "type": "message_delta",
                "message_id": stream_message_id,
                "content": base_answer,
                "thread_id": thread_id,
                "namespace": [],
            },
            metadata={},
        )

    supplement = ""
    extra_sources: list[dict[str, Any]] = []
    if base_answer:
        try:
            # 先广播工具开始，让前端在检索与归纳期间显示「正在查询知识库…」胶囊。
            yield make_chunk(
                status="stream_event",
                event={
                    "method": "tools",
                    "data": {
                        "event": "tool-started",
                        "tool_name": "query_kbs",
                        "tool_call_id": f"curated-qa-retrieval-{run_id}",
                    },
                },
                namespace=[],
                meta=meta,
            )
            extra_sources = await _retrieve_extra_sources(
                db=db,
                current_uid=uid,
                agent_item=scope.agent_item,
                agent_backend=scope.agent_backend,
                question=input_message.content,
            )
            if extra_sources:
                supplement = await _compose_extra_retrieval_supplement(
                    model_spec, input_message.content, base_answer, extra_sources
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("QA 命中后的知识库补充检索失败，仅返回人工答案: %s", exc)
            extra_sources = []
            supplement = ""
        if supplement:
            yield make_chunk(
                content=supplement,
                status="loading",
                stream_event={
                    "type": "message_delta",
                    "message_id": stream_message_id,
                    "content": supplement,
                    "thread_id": thread_id,
                    "namespace": [],
                },
                metadata={},
            )

    try:
        combined = _merge_curated_supplement(base_answer, supplement)
        answer_metadata = {
            "id": stream_message_id,
            "type": "ai",
            "role": "assistant",
            "content": combined,
            "answer_source": answer_source,
            "curated_qa_id": qa_pair.id,
            "human_confirmed": True,
        }
        assistant_message = await ConversationRepository(db).add_message_by_thread_id(
            thread_id=thread_id,
            role="assistant",
            content=combined,
            message_type="text",
            extra_metadata=answer_metadata,
            run_id=run_id,
            request_id=request_id,
        )
        if assistant_message is None:
            raise RuntimeError("人工 QA 命中后保存回答失败")
        if extra_sources:
            await _attach_extra_retrieval_tool_call(
                db=db,
                message_id=assistant_message.id,
                question=input_message.content,
                sources=extra_sources,
            )
        run_repo = AgentRunRepository(db)
        await run_repo.set_output_message(run_id, assistant_message.id)
        await qa_repo.mark_hit(qa_pair)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("人工 QA 命中流式落库失败: %s", exc)
        yield make_chunk(
            status="error",
            error_type="curated_qa_persist_failed",
            error_message="QA 命中回答保存失败",
            meta=meta,
        )
        return

    yield make_chunk(status="finished", meta=meta)


def _merge_curated_supplement(base_answer: str, supplement: str) -> str:
    """把检索补充段落拼到基础答案之后；无补充时原样返回基础答案。"""
    if not supplement:
        return base_answer
    return f"{base_answer}\n\n补充资料（知识库检索）：\n{supplement}"


# 补充检索参数：每库最多保留的片段数，与 query_kbs 单库 5 条的量级一致；
# 跨库合计上限控制补充归纳的输入规模，避免长文本拖慢 QA 命中路径。
_PER_KB_EXTRA_KEEP = 4
_SUPPLEMENT_MAX_SNIPPETS = 12

async def _retrieve_extra_sources(
    *,
    db: AsyncSession,
    current_uid: str,
    agent_item,
    agent_backend,
    question: str,
) -> list[dict[str, Any]]:
    """解析该智能体可见的知识库并并行检索，返回含来源文件名的检索块。"""
    from yuxi.agents.context import normalize_agent_context_config
    from yuxi.knowledge.base import KnowledgeBase
    from yuxi.knowledge.runtime import knowledge_base
    from yuxi.storage.postgres.models_business import User

    user_result = await db.execute(select(User).where(User.uid == str(current_uid)))
    current_user = user_result.scalar_one_or_none()
    if current_user is None:
        return []
    agent_config = await normalize_agent_context_config(
        (agent_item.config_json or {}).get("context", {}),
        db=db,
        user=current_user,
        context_schema=agent_backend.context_schema,
    )
    kb_ids = [str(value) for value in (agent_config.get("knowledges") or []) if str(value).strip()]
    if not kb_ids:
        return []

    async def _search_one(kb_id: str) -> list[dict[str, Any]]:
        try:
            raw = await knowledge_base.aquery(question, kb_id=kb_id, agent_call=True, final_top_k=_PER_KB_EXTRA_KEEP)
        except Exception as exc:  # noqa: BLE001
            logger.warning("QA 命中后的知识库补充检索跳过 %s: %s", kb_id, exc)
            return []
        results = KnowledgeBase.build_search_output(kb_id, raw).get("results", [])
        return [item for item in results if str(item.get("content") or "").strip()]

    grouped = await asyncio.gather(*(_search_one(kb_id) for kb_id in kb_ids))
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for item in (chunk for group in grouped for chunk in group):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        chunk_id = str(metadata.get("chunk_id") or item.get("id") or "")
        # 同一份文档分片可能同时出现在多个库的召回中，按分片身份去重只保留最先命中的
        dedup_key = (
            str(item.get("file_id") or ""),
            chunk_id,
            str(item.get("content") or ""),
        )
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        merged.append(item)
        if len(merged) >= _SUPPLEMENT_MAX_SNIPPETS:
            break
    return merged


async def _compose_extra_retrieval_supplement(
    model_spec: str,
    question: str,
    base_answer: str,
    sources: list[dict[str, Any]],
) -> str:
    """让模型只归纳检索片段中、原答案未覆盖的补充信息；无补充时返回空串。"""
    from yuxi.models.chat import select_model

    snippet_lines: list[str] = []
    for index, item in enumerate(sources, start=1):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        source = str(metadata.get("source") or item.get("file_id") or "")
        snippet_lines.append(f"[{index}] 《{source}》\n{item.get('content') or ''}")
    snippets = "\n".join(snippet_lines)
    messages = [
        {
            "role": "system",
            "content": (
                "你是企业知识库助手。下面有一份人工确认的回答和知识库检索到的候选片段。"
                "请找出片段中「原回答没提到、且与用户问题直接相关」的补充信息，"
                "用与用户问题一致的语言写成一小段补充内容，引用片段里的文档时用《文件名》标注。"
                "不要复述原回答的内容；候选片段若与原回答冲突，以原回答为准；"
                "若没有可补充的新信息，只回复四个字：无需补充。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户问题：{question}\n\n已有人工确认的回答：\n{base_answer}\n\n知识库检索到的候选片段：\n{snippets}"
            ),
        },
    ]
    try:
        response = await select_model(model_spec).call(messages, stream=False)
        text = str((response and response.content) or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("QA 命中后的补充内容归纳失败，仅返回人工答案: %s", exc)
        return ""
    if not text or "无需补充" in text:
        return ""
    return text


async def _attach_extra_retrieval_tool_call(
    *,
    db: AsyncSession,
    message_id: int,
    question: str,
    sources: list[dict[str, Any]],
) -> None:
    """把补充检索结果以 query_kbs 工具调用挂到回答消息上，供前端来源面板展示。"""
    from yuxi.knowledge.schemas import SearchOutputSchema

    payload = SearchOutputSchema(
        schema_version=1,
        status="ok",
        kb_id="",
        results=sources,
    ).model_dump()
    kb_ids = list(dict.fromkeys(str(item.get("kb_id") or "") for item in sources if item.get("kb_id")))
    await ConversationRepository(db).add_tool_call(
        message_id=message_id,
        tool_name="query_kbs",
        tool_input={"query_text": question, "kb_ids": kb_ids},
        tool_output=json.dumps(payload, ensure_ascii=False),
        status="success",
    )
