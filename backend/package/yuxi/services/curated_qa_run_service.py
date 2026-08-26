"""人工确认 QA 的直接命中运行路径。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.curated_qa_repository import CuratedQARepository
from yuxi.services.agent_run_service import (
    create_agent_run_input_message,
    persist_agent_run_record,
    prepare_agent_run_creation_scope,
    resolve_agent_run_model_spec,
)
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.services.run_queue_service import append_run_stream_event
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
    """命中人工 QA 时直接完成 run；未命中返回 None 继续普通 Agent 流程。

    精确命中直接输出人工确认的原答案；语义命中（表述相近但字符不同）不直接顶出
    原答案，而是以人工答案为参考让大模型按用户问题原意组织回答（answer_source=
    curated_qa_semantic），避免改述问题得到答非所问的死板复述。
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
    answer_text = (
        await _compose_answer_from_reference(resolved_model_spec, input_message.content, qa_pair)
        if answer_source == "curated_qa_semantic"
        else qa_pair.answer
    )
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

    run_repo = AgentRunRepository(db)
    await run_repo.mark_running(run.id)

    stream_message_id = f"curated-qa-{run.id}"
    answer_metadata = {
        "id": stream_message_id,
        "type": "ai",
        "role": "assistant",
        "content": answer_text,
        "answer_source": answer_source,
        "curated_qa_id": qa_pair.id,
        "human_confirmed": True,
    }
    assistant_message = await ConversationRepository(db).add_message_by_thread_id(
        thread_id=thread_id,
        role="assistant",
        content=answer_text,
        message_type="text",
        extra_metadata=answer_metadata,
        run_id=run.id,
        request_id=request_id,
    )
    if assistant_message is None:
        raise RuntimeError("人工 QA 命中后保存回答失败")

    await run_repo.set_output_message(run.id, assistant_message.id)
    await run_repo.set_terminal_status(run.id, status="completed")
    await qa_repo.mark_hit(qa_pair)
    await db.commit()

    event_meta = {
        "run_id": run.id,
        "request_id": request_id,
        "agent_slug": agent_slug,
        "thread_id": thread_id,
        "uid": str(current_uid),
        "answer_source": answer_source,
        "curated_qa_id": qa_pair.id,
    }
    init_chunk = {
        "request_id": request_id,
        "response": None,
        "thread_id": thread_id,
        "status": "init",
        "meta": event_meta,
        "msg": {
            "role": "user",
            "content": input_message.content,
            "type": "human",
            "message_type": input_message.message_type,
            "extra_metadata": {"request_id": request_id},
        },
    }
    loading_chunk = {
        "request_id": request_id,
        "response": answer_text,
        "thread_id": thread_id,
        "status": "loading",
        "stream_event": {
            "type": "message_delta",
            "message_id": stream_message_id,
            "content": answer_text,
            "thread_id": thread_id,
            "namespace": [],
        },
        "metadata": {},
    }
    finished_chunk = {
        "request_id": request_id,
        "response": None,
        "thread_id": thread_id,
        "status": "finished",
        "meta": event_meta,
    }

    await append_run_stream_event(run.id, "metadata", event_meta, thread_id=thread_id)
    await append_run_stream_event(
        run.id,
        "custom",
        {"name": "yuxi.init", "chunk": init_chunk},
        thread_id=thread_id,
    )
    await append_run_stream_event(
        run.id,
        "messages",
        {"chunk": loading_chunk},
        thread_id=thread_id,
    )
    await append_run_stream_event(
        run.id,
        "end",
        {"status": "completed", "chunk": finished_chunk},
        thread_id=thread_id,
    )

    return _run_response(run)
