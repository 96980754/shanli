"""Agent runtime streaming service.

This module is the LangGraph execution path used by the worker after an
``AgentRun`` has already been created. It restores input messages, builds the
agent runtime context, streams model/tool events, persists assistant output and
extracts UI-facing agent state.

Do not put run creation, request id idempotency, queueing or external
invocation response formatting here. Those responsibilities belong to
``agent_run_service`` and ``agent_invocation_service`` respectively. Keeping
this file focused on execution makes normal chat, resume runs and subagent runs
share the same runtime behavior once they reach the worker.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from langchain.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.types import Command
from yuxi import config as conf
from yuxi.agents.buildin import agent_manager
from yuxi.agents.buildin.chatbot.prompt import SCOPE_REFUSAL_REPLY
from yuxi.agents.context import build_agent_input_context, normalize_agent_context_config
from yuxi.agents.state import AgentStatePayload
from yuxi.config.app import sanitize_business_domain
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.subagent_thread_repository import SubagentThreadRepository
from yuxi.services.conversation_service import serialize_attachment
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.services.knowledge_answer_disposition import (
    DISPOSITION_SCHEMA_VERSION,
    HANDOFF_REFUSAL_TYPES,
    apply_knowledge_disposition,
    apply_refusal_judgment,
    build_knowledge_evidence,
    collect_turn_tool_names,
    is_final_assistant_message,
    is_handoff_disposition,
    judge_refusal,
    no_evidence_disposition,
)
from yuxi.services.knowledge_gap_service import record_knowledge_gap
from yuxi.services.knowledge_scope_gate import build_scope_corpus, evaluate_scope
from yuxi.services.translation_service import is_chinese_text, translate_from_chinese, translate_to_chinese
from yuxi.services.langfuse_service import (
    LangfuseRunContext,
    build_run_context,
    flush_langfuse,
    get_trace_info,
)
from yuxi.services.subagent_run_service import serialize_subagent_run_state
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent, User
from yuxi.utils.guard import content_guard
from yuxi.utils.logging_config import logger
from yuxi.utils.paths import VIRTUAL_PATH_OUTPUTS, VIRTUAL_PATH_UPLOADS, VIRTUAL_SKILLS_PATH
from yuxi.utils.question_utils import (
    normalize_questions as _normalize_interrupt_questions,
)
from yuxi.utils.thread_utils import extract_thread_id as _metadata_thread_id

# 顶层主对话（含 resume，非 subagent）的沙箱只读范围：本线程 uploads/outputs + skills，
# 排除跨会话共享的 workspace——其内容不作为回答依据。subagent/生成类子流保持全量。
_MAIN_CHAT_READ_ROOTS = (VIRTUAL_PATH_UPLOADS, VIRTUAL_PATH_OUTPUTS, VIRTUAL_SKILLS_PATH)


def _apply_main_chat_read_scope(context, *, run_type: str | None) -> None:
    """给主对话上下文注入受限读根：沙箱后端据此只读本会话 uploads/outputs + skills。"""
    if run_type != "subagent":
        setattr(context, "fs_read_roots", _MAIN_CHAT_READ_ROOTS)


def _apply_industry_solution_skill_gate(context, *, run_type: str | None, industry_enabled: bool) -> None:
    """行业方案（Word 交付）技能改显式门禁：仅结构化 industry_solution 请求放开。

    普通主对话（chat/resume）不再广告/自激活 industry-solution，避免模型因一句
    泛化指令（如「再去寻找一些内容」）擅自进入行业方案流程；subagent 等内层流
    不受影响（由上层门禁决定是否进入）。

    结构化请求是唯一放行通道：即便 agent 未在技能清单里声明 industry-solution，
    也把它补进 skills，get_graph→prepare_agent_runtime_context 重算后即可
    挂载/广告该技能；否则「放开」退化为依赖 agent 预配置，行业方案模式在未配置的
    agent 上会静默失效、模型只能降级手工生成 Word。

    context 既可以是已构造的 BaseContext（读写 .skills），也可以是 input_context
    dict（读写 ['skills']）。运行期工具绑定从 input_context 重建 context
    （agents/base.py:_stream_input_with_state → get_graph → prepare），因此调用方必须
    把门禁作用在传给流式执行的 input_context 上；只改一个事后不参与工具绑定的
    dataclass 实例会在重建时丢失，导致技能与检索工具挂载不上。
    """
    if run_type not in ("chat", "resume"):
        return
    if isinstance(context, dict):
        skills = list(context.get("skills") or [])
    else:
        skills = list(getattr(context, "skills", None) or [])
    has_industry = "industry-solution" in skills
    if industry_enabled:
        if has_industry:
            return
        skills = [*skills, "industry-solution"]
    elif not has_industry:
        return
    else:
        skills = [slug for slug in skills if slug != "industry-solution"]
    if isinstance(context, dict):
        context["skills"] = skills
    else:
        context.skills = skills


def _build_state_files(attachments: list[dict]) -> dict:
    """将附件列表转换为 StateBackend 格式的 files 字典

    StateBackend 期望的格式:
    {
        "/attachments/file.md": {
            "content": ["line1", "line2", ...],
            "created_at": "...",
            "modified_at": "...",
        }
    }
    """
    files = {}
    for attachment in attachments:
        if attachment.get("status") != "parsed":
            continue

        file_path = attachment.get("file_path")
        markdown = attachment.get("markdown")

        if not file_path or not markdown:
            continue

        now = datetime.now(UTC).isoformat()
        # 将 markdown 内容按行拆分
        content_lines = markdown.split("\n")
        files[file_path] = {
            "content": content_lines,
            "created_at": attachment.get("uploaded_at", now),
            "modified_at": attachment.get("uploaded_at", now),
        }

    return files


def _build_agent_context(agent, input_context: dict):
    context = agent.context_schema()
    context.update(input_context)
    return context


async def _get_langgraph_messages(agent_instance, config_dict, *, context):
    graph = await agent_instance.get_graph(context=context)
    state = await graph.aget_state(config_dict)

    if not state or not state.values:
        logger.warning("No state found in LangGraph")
        return None

    return state.values.get("messages", [])


def _build_langfuse_run_context(
    *,
    current_user,
    thread_id: str,
    agent_id: str,
    request_id: str,
    operation: str,
    backend_id: str | None = None,
    message_type: str | None = None,
    meta: dict | None = None,
) -> LangfuseRunContext:
    extra_metadata = None
    extra_tags = None
    invocation_meta = (meta or {}).get("agent_invocation_meta") if isinstance(meta, dict) else None
    evaluation = invocation_meta.get("evaluation") if isinstance(invocation_meta, dict) else None
    # 如果请求来自智能体评测，添加评测相关的 metadata 和 tags，方便在 Langfuse 中进行过滤和分析
    if (meta or {}).get("source") == "agent_evaluation" or (isinstance(evaluation, dict) and evaluation):
        extra_metadata = {
            "source": "agent_evaluation",
            "feature": "agent_evaluation",
        }
        extra_tags = ["agent_evaluation"]
        if isinstance(evaluation, dict):
            dataset_name = evaluation.get("dataset_name")
            experiment_name = evaluation.get("experiment_name")
            for key in ("dataset_name", "dataset_item_id", "experiment_name"):
                value = evaluation.get(key)
                if value:
                    extra_metadata[f"evaluation_{key}"] = str(value)
            if dataset_name:
                extra_tags.append(f"dataset:{dataset_name}")
            if experiment_name:
                extra_tags.append(f"experiment:{experiment_name}")

    return build_run_context(
        user_id=str(getattr(current_user, "uid", current_user.id)),
        thread_id=thread_id,
        agent_id=agent_id,
        request_id=request_id,
        operation=operation,
        backend_id=backend_id,
        message_type=message_type,
        username=getattr(current_user, "username", None),
        login_user_id=getattr(current_user, "uid", None),
        department_id=getattr(current_user, "department_id", None),
        extra_metadata=extra_metadata,
        extra_tags=extra_tags,
    )


def extract_agent_state(values: dict) -> AgentStatePayload:
    """从 LangGraph state 中提取 agent 状态"""
    if not isinstance(values, dict):
        return {"todos": [], "files": {}, "artifacts": [], "subagent_runs": [], "token_usage": None}

    # 直接获取，信任 state 的数据结构
    todos = values.get("todos")
    artifacts = values.get("artifacts")
    subagent_runs = values.get("subagent_runs")
    token_usage = values.get("token_usage")
    result: AgentStatePayload = {
        "todos": list(todos)[:20] if todos else [],
        "files": values.get("files") or {},
        "artifacts": list(artifacts) if artifacts else [],
        "subagent_runs": list(subagent_runs) if subagent_runs else [],
        "token_usage": dict(token_usage) if isinstance(token_usage, dict) else None,
    }

    return result


def _agent_state_signature(agent_state: AgentStatePayload | dict | None) -> str:
    if not agent_state:
        return ""
    try:
        return json.dumps(agent_state, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(agent_state)


def _metadata_namespace(metadata: dict | None) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    namespace = metadata.get("namespace")
    if isinstance(namespace, list):
        return [str(item) for item in namespace]
    return []


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(child) for child in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    return str(value)


def _apply_model_override(input_context: dict, meta: dict | None) -> None:
    """对话级模型覆盖：meta.model_spec 优先于智能体配置的 model。值已在创建 run 时校验。"""
    model_spec = (meta or {}).get("model_spec")
    model_spec = model_spec.strip() if isinstance(model_spec, str) else model_spec
    if model_spec:
        input_context["model"] = model_spec


def _apply_subagent_runtime_context(input_context: dict, meta: dict | None) -> None:
    """把子智能体 run 的父线程和文件线程信息注入运行 context。"""
    meta = meta or {}
    # 仅对子智能体类型的 run 生效
    if meta.get("run_type") != "subagent":
        return
    # 这三个线程 ID 由 subagent_run_service 在创建 run 时写入 runtime，
    # 是子智能体区别于普通对话的唯一依据；缺失即上游契约被破坏，直接失败而非静默回退。
    for key in ("parent_thread_id", "file_thread_id", "skills_thread_id"):
        value = str(meta.get(key) or "").strip()
        if not value:
            raise ValueError(f"子智能体运行缺少必需的 {key}")
        input_context[key] = value
    # 标记为子智能体运行，供下游逻辑判断
    input_context["is_subagent_runtime"] = True


def _stream_message_key(metadata: dict | None, namespace: list[str], thread_id: str | None) -> tuple[str, str]:
    if not isinstance(metadata, dict):
        return thread_id or "", "/".join(namespace)
    return thread_id or "", str(metadata.get("run_id") or metadata.get("langgraph_node") or "/".join(namespace))


def _stream_message_id(
    message_ids: dict[tuple[str, str], str],
    key: tuple[str, str],
    preferred: str | None = None,
) -> str:
    if preferred:
        message_ids[key] = preferred
        return preferred
    return message_ids.setdefault(key, str(uuid.uuid4()))


def _message_chunk_yuxi_events(
    msg_dict: dict[str, Any],
    *,
    message_id: str,
    thread_id: str | None,
    namespace: list[str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    route = {"thread_id": thread_id, "namespace": namespace}
    content = msg_dict.get("content")
    additional_kwargs = msg_dict.get("additional_kwargs") if isinstance(msg_dict.get("additional_kwargs"), dict) else {}
    reasoning_content = msg_dict.get("reasoning_content")
    additional_reasoning_content = additional_kwargs.get("reasoning_content")

    message_event: dict[str, Any] = {"type": "message_delta", "message_id": message_id, **route}
    if isinstance(content, str) and content:
        message_event["content"] = content
    if isinstance(reasoning_content, str) and reasoning_content:
        message_event["reasoning_content"] = reasoning_content
    if isinstance(additional_reasoning_content, str) and additional_reasoning_content:
        message_event["additional_reasoning_content"] = additional_reasoning_content
    if len(message_event) > 4:
        events.append(message_event)

    tool_call_chunks = msg_dict.get("tool_call_chunks")
    if isinstance(tool_call_chunks, list):
        for tool_call_chunk in tool_call_chunks:
            if not isinstance(tool_call_chunk, dict):
                continue
            args_delta = tool_call_chunk.get("args")
            if args_delta is None:
                args_delta = ""
            elif not isinstance(args_delta, str):
                args_delta = json.dumps(args_delta, ensure_ascii=False)
            if not tool_call_chunk.get("id") and not tool_call_chunk.get("name") and not args_delta:
                continue
            events.append(
                {
                    "type": "tool_call_delta",
                    "message_id": message_id,
                    "tool_call_id": tool_call_chunk.get("id"),
                    "name": tool_call_chunk.get("name") or None,
                    "args_delta": args_delta,
                    "index": tool_call_chunk.get("index") if tool_call_chunk.get("index") is not None else 0,
                    **route,
                }
            )
    return events


def _protocol_event_yuxi_event(
    event: dict[str, Any],
    *,
    message_id: str | None,
    thread_id: str | None,
    namespace: list[str],
) -> dict[str, Any] | None:
    event_name = event.get("event")
    if event_name in {"message-start", "content-block-start", "message-finish"} or not message_id:
        return None

    route = {"thread_id": thread_id, "namespace": namespace}
    if event_name == "content-block-delta":
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        text = delta.get("text")
        if delta.get("type") == "text-delta" and isinstance(text, str) and text:
            return {"type": "message_delta", "message_id": message_id, "content": text, **route}
        return None

    if event_name == "content-block-finish":
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        if content.get("type") != "tool_call" or not content.get("id") and not content.get("name"):
            return None
        return {
            "type": "tool_call",
            "message_id": message_id,
            "tool_call_id": content.get("id"),
            "name": content.get("name"),
            "args": content.get("args") if content.get("args") is not None else {},
            "index": event.get("index") if event.get("index") is not None else 0,
            **route,
        }

    return None


def _context_compression_payload(payload: Any) -> dict | None:
    if isinstance(payload, dict) and payload.get("type") == "yuxi.context_compression":
        return payload
    return None


def _tool_output_as_dict(output: Any) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    content = output.get("content", output)
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _requires_knowledge_preflight(agent_backend_id: str, query: str) -> bool:
    # 问候/寒暄、自我介绍类（你是谁/能做什么）等非业务提问不走知识库预检与入口门，
    # 直接交主模型应答（身份类由提示词约束回复 IDENTITY_REPLY）。
    return agent_backend_id == "ChatbotAgent" and query.strip().lower().strip("，。！？!?、 ") not in {
        "你好",
        "您好",
        "嗨",
        "hi",
        "hello",
        "在吗",
        "在么",
        "你是谁",
        "你是谁呀",
        "你是什么",
        "你是做什么的",
        "你们是谁",
        "你们是做什么的",
        "你能做什么",
        "你能干什么",
        "能做什么",
        "介绍一下你自己",
        "介绍下你自己",
        "介绍一下你",
        "who are you",
        "what are you",
        "what can you do",
    }


async def _get_last_assistant_message(conv_repo: ConversationRepository, thread_id: str):
    """返回 thread 内最新一条已落库 assistant 消息；无则 None（仅在首答轮之前调用，开销低）。"""
    try:
        messages = await conv_repo.get_messages_by_thread_id(thread_id, limit=50)
    except Exception as exc:  # noqa: BLE001 — 查询失败按“无前文”处理，不阻断问答
        logger.warning("读取会话前文失败: {}", exc)
        return None
    for message in reversed(messages):
        if getattr(message, "role", "") == "assistant":
            return message
    return None


def _assistant_meta_is_answer(extra_metadata: dict | None) -> bool:
    """一条已落库 assistant 消息是否属“正常回答”。

    预检拒绝/跑题拒绝/报错消息都带 handoff 或 error 标记、或 disposition 为拒答类型，
    视为未作答；引入 disposition 前的旧消息按正常回答处理。
    """
    meta = dict(extra_metadata or {})
    if meta.get("handoff_available") or meta.get("is_error") or meta.get("error_type"):
        return False
    disposition = meta.get("knowledge_disposition") or {}
    if disposition.get("type"):
        return disposition.get("type") == "answered"
    return True


async def _thread_has_answered_turn(conv_repo: ConversationRepository, thread_id: str) -> bool:
    """thread 是否已进入正常问答（上一条 assistant 是正常回答）。

    入口门与零命中预检只对「尚无正常作答的首答轮」触发；续答轮放行主模型结合上下文作答。
    """
    message = await _get_last_assistant_message(conv_repo, thread_id)
    return bool(message) and _assistant_meta_is_answer(getattr(message, "extra_metadata", None))


async def _last_assistant_has_kb_evidence(conv_repo: ConversationRepository, thread_id: str) -> bool:
    """紧邻上一条 assistant 消息是否带 ok 检索证据（决策② 续答轮豁免判定）。"""
    message = await _get_last_assistant_message(conv_repo, thread_id)
    if message is None:
        return False
    evidence = (getattr(message, "extra_metadata", None) or {}).get("knowledge_evidence") or {}
    return bool(evidence) and any(query.get("status") == "ok" for query in (evidence.get("queries") or []))


@dataclass
class _KnowledgeEvidenceTracker:
    started_ids: set[str] = field(default_factory=set)
    completed_ids: set[str] = field(default_factory=set)
    has_evidence: bool = False
    failed: bool = False

    def observe(self, event: Any) -> None:
        data = event.get("data") if isinstance(event, dict) else None
        if not isinstance(data, dict):
            return
        tool_name = str(data.get("tool_name") or data.get("name") or "")
        if tool_name != "query_kb":
            return
        call_id = str(data.get("tool_call_id") or "")
        if data.get("event") == "tool-started":
            self.started_ids.add(call_id or f"started-{len(self.started_ids)}")
            return
        if data.get("event") != "tool-finished":
            return
        completed_id = call_id or f"finished-{len(self.completed_ids)}"
        if completed_id in self.completed_ids:
            return
        self.completed_ids.add(completed_id)
        output = _tool_output_as_dict(data.get("output"))
        if data.get("error") or output is None or not isinstance(output.get("results"), list):
            self.failed = True
            return
        self.has_evidence = self.has_evidence or bool(output["results"])

    @property
    def should_handoff(self) -> bool:
        return bool(
            self.started_ids
            and self.completed_ids
            and self.started_ids <= self.completed_ids
            and not self.has_evidence
            and not self.failed
        )


def _stream_event_response(event: dict[str, Any]) -> str:
    if event.get("type") != "message_delta":
        return ""
    return str(event.get("content") or "")


def _message_payload_yuxi_events(
    msg: Any,
    *,
    metadata: dict[str, Any],
    namespace: list[str],
    thread_id: str | None,
    protocol_message_ids: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    message_key = _stream_message_key(metadata, namespace, thread_id)
    if isinstance(msg, dict) and isinstance(msg.get("event"), str):
        preferred_message_id = str(msg["id"]) if msg.get("event") == "message-start" and msg.get("id") else None
        message_id = _stream_message_id(protocol_message_ids, message_key, preferred_message_id)
        stream_event = _protocol_event_yuxi_event(
            msg,
            message_id=message_id,
            thread_id=thread_id,
            namespace=namespace,
        )
        return [stream_event] if stream_event else []

    if isinstance(msg, AIMessageChunk) or hasattr(msg, "model_dump"):
        msg_dict = msg.model_dump()
    elif isinstance(msg, dict):
        msg_dict = dict(msg)
    else:
        msg_dict = {"content": str(msg)}

    message_id = str(msg_dict.get("id") or _stream_message_id(protocol_message_ids, message_key))
    return _message_chunk_yuxi_events(
        msg_dict,
        message_id=message_id,
        thread_id=thread_id,
        namespace=namespace,
    )


async def _stream_agent_events(agent, messages, *, input_context=None, **kwargs):
    async for mode, payload in agent.stream_messages_with_state(
        messages,
        input_context=input_context,
        **kwargs,
    ):
        yield mode, payload


# =============================================================================
# 沙箱预冷：沙箱容器冷启动约 4s（docker 启动 + 健康探测），若在首个工具调用时才
# 触发会阻塞模型拿到工具结果。这里在 run 开始时后台预冷，把耗时藏到首轮 LLM 生成
# 之后；热路径（缓存命中）为亚毫秒，预冷失败也不影响本次运行。
# =============================================================================

_PREWARM_TASKS: set[asyncio.Task] = set()


def _schedule_sandbox_prewarm(*, thread_id: str, uid: str, meta: dict) -> None:
    """后台预冷 run 的沙箱作用域，提前建好/复用容器，首个 read_file 不再等 4s。"""
    file_thread_id = str(meta.get("file_thread_id") or thread_id).strip()
    skills_thread_id = str(meta.get("skills_thread_id") or thread_id).strip()
    task = asyncio.create_task(
        _prewarm_sandbox(
            thread_id=thread_id,
            uid=uid,
            file_thread_id=file_thread_id,
            skills_thread_id=skills_thread_id,
        )
    )
    _PREWARM_TASKS.add(task)
    task.add_done_callback(_PREWARM_TASKS.discard)


async def _prewarm_sandbox(
    *,
    thread_id: str,
    uid: str,
    file_thread_id: str,
    skills_thread_id: str,
) -> None:
    try:
        from yuxi.agents.backends.sandbox.provider import get_sandbox_provider

        # 同步阻塞调用丢到线程池：冷建沙箱（docker 启动 + 健康探测）耗时约 4s，
        # 若直接在事件循环里跑会冻结整个 loop，连带推迟首条模型 token。
        await asyncio.to_thread(
            get_sandbox_provider().get,
            thread_id,
            uid=uid,
            create_if_missing=True,
            file_thread_id=file_thread_id,
            skills_thread_id=skills_thread_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Sandbox prewarm failed for thread {thread_id}: {exc}")


async def _get_existing_message_ids(conv_repo: ConversationRepository, thread_id: str) -> set[str]:
    existing_messages = await conv_repo.get_messages_by_thread_id(thread_id)
    return {
        msg.extra_metadata["id"]
        for msg in existing_messages
        if msg.extra_metadata and "id" in msg.extra_metadata and isinstance(msg.extra_metadata["id"], str)
    }


async def _save_ai_message(
    conv_repo: ConversationRepository,
    thread_id: str,
    msg_dict: dict,
    trace_info: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
):
    content = msg_dict.get("content", "")
    tool_calls_data = msg_dict.get("tool_calls") or []
    if isinstance(content, list):
        if not tool_calls_data:
            tool_calls_data = [
                {"id": item.get("id"), "name": item.get("name"), "args": item.get("args") or {}}
                for item in content
                if isinstance(item, dict) and item.get("type") == "tool_call"
            ]
        content = "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    elif not isinstance(content, str):
        content = str(content)
    extra_metadata = dict(msg_dict)
    if trace_info:
        extra_metadata.update(trace_info)

    ai_msg = await conv_repo.add_message_by_thread_id(
        thread_id=thread_id,
        role="assistant",
        content=content,
        message_type="text",
        extra_metadata=extra_metadata,
        run_id=run_id,
        request_id=request_id,
    )

    if ai_msg and tool_calls_data:
        for tc in tool_calls_data:
            await conv_repo.add_tool_call(
                message_id=ai_msg.id,
                tool_name=tc.get("name") or "unknown",
                tool_input=tc.get("args", {}),
                status="pending",
                langgraph_tool_call_id=tc.get("id"),
            )

    return ai_msg


async def _save_tool_message(conv_repo: ConversationRepository, msg_dict: dict) -> None:
    tool_call_id = msg_dict.get("tool_call_id")
    content = msg_dict.get("content", "")

    if not tool_call_id:
        return

    if isinstance(content, list):
        tool_output = json.dumps(content) if content else ""
    else:
        tool_output = str(content)

    await conv_repo.update_tool_call_output(
        langgraph_tool_call_id=tool_call_id,
        tool_output=tool_output,
        status="success",
    )


async def save_partial_message(
    conv_repo: ConversationRepository,
    thread_id: str,
    full_msg=None,
    error_message: str | None = None,
    error_type: str = "interrupted",
    trace_info: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
):
    try:
        extra_metadata = {
            "error_type": error_type,
            "is_error": True,
            "error_message": error_message or f"发生错误: {error_type}",
        }
        if full_msg:
            msg_dict = full_msg.model_dump() if hasattr(full_msg, "model_dump") else {}
            content = full_msg.content if hasattr(full_msg, "content") else str(full_msg)
            extra_metadata = msg_dict | extra_metadata
        else:
            content = ""

        if trace_info:
            extra_metadata.update(trace_info)

        return await conv_repo.add_message_by_thread_id(
            thread_id=thread_id,
            role="assistant",
            content=content,
            message_type="text",
            extra_metadata=extra_metadata,
            run_id=run_id,
            request_id=request_id,
        )

    except Exception as e:
        logger.exception(f"Error saving message: {e}")
        return None


async def save_messages_from_langgraph_state(
    agent_instance,
    thread_id: str,
    conv_repo: ConversationRepository,
    config_dict: dict,
    context,
    trace_info: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
) -> None:
    messages = await _get_langgraph_messages(agent_instance, config_dict, context=context)
    if messages is None:
        return

    existing_ids = await _get_existing_message_ids(conv_repo, thread_id)
    knowledge_question, knowledge_evidence = build_knowledge_evidence(messages)
    # 决策② 依据：本轮实际调用的工具名集合 + 上一条已落库回答是否带 ok 检索证据（续答豁免）。
    turn_tool_names = collect_turn_tool_names(messages)
    prev_answer_has_evidence = await _last_assistant_has_kb_evidence(conv_repo, thread_id)

    last_ai_message = None
    final_ai_message = None
    final_ai_metadata = None
    for msg in messages:
        if hasattr(msg, "model_dump"):
            msg_dict = msg.model_dump()
        elif isinstance(msg, dict):
            msg_dict = dict(msg)
        else:
            continue

        msg_type = msg_dict.get("type", "unknown")
        if msg_type == "unknown":
            role = msg_dict.get("role")
            if role in {"assistant", "ai"}:
                msg_type = "ai"
            elif role in {"user", "human"}:
                msg_type = "human"
            elif role == "tool":
                msg_type = "tool"

        msg_id = getattr(msg, "id", None) or msg_dict.get("id")
        if msg_type == "human" or msg_id in existing_ids:
            continue

        if msg_type == "ai":
            msg_dict = apply_knowledge_disposition(
                msg_dict,
                question=knowledge_question,
                evidence=knowledge_evidence,
            )
            # 拒答消息：无检索证据的分支用 LLM judge 区分知识缺口/跑题/跨域/策略拦截，
            # 再落业务域；仅知识类/范围类标记可转人工，策略类拒答不转普通客服。
            # 域解析只在拒答分支执行，正常回答零额外 DB/LLM 开销。
            disposition = msg_dict.get("knowledge_disposition") or {}
            if disposition.get("judgment_required"):
                judgment = await judge_refusal(knowledge_question or "")
                disposition = apply_refusal_judgment(disposition, judgment)
                msg_dict["knowledge_disposition"] = disposition
            # 决策② 无依据不输出兜底：模型对业务内问题零检索硬答（守规失败）→ 按知识缺口拒答并转人工。
            # 豁免：身份/寒暄正文、本轮用了合法来源工具（文件/图片/联网/文档等）、紧邻带 ok 证据回答的续答轮。
            if disposition.get("type") == "answered":
                no_evidence = no_evidence_disposition(
                    msg_dict,
                    evidence=knowledge_evidence,
                    tool_names=turn_tool_names,
                    continuation_with_evidence=prev_answer_has_evidence,
                )
                if no_evidence is not None:
                    disposition = no_evidence
                    msg_dict["knowledge_disposition"] = disposition
                    msg_dict["knowledge_no_evidence"] = True
            if disposition.get("type") in HANDOFF_REFUSAL_TYPES or disposition.get("type") == "policy_refusal":
                # 业务域仅作统计标签且不再有 agent 级配置来源：无检索证据拒答经 judge 细分时
                # 域已由 apply_refusal_judgment 写入；此处统一归一，游离/空值回退 unknown（清单外 code 不入库）。
                disposition["domain"] = sanitize_business_domain(disposition.get("domain"))
                msg_dict["knowledge_disposition"] = disposition
                if is_handoff_disposition(disposition):
                    msg_dict["handoff_available"] = True
                    msg_dict["handoff_query"] = knowledge_question or ""
            last_ai_message = await _save_ai_message(
                conv_repo,
                thread_id,
                msg_dict,
                trace_info=trace_info,
                run_id=run_id,
                request_id=request_id,
            )
            if is_final_assistant_message(msg_dict):
                final_ai_message = last_ai_message
                final_ai_metadata = msg_dict
        elif msg_type == "tool":
            await _save_tool_message(conv_repo, msg_dict)

    output_message = final_ai_message or last_ai_message
    if run_id and output_message:
        run_repo = AgentRunRepository(conv_repo.db)
        await run_repo.set_output_message(run_id, output_message.id)
        await conv_repo.db.commit()

    if final_ai_message and isinstance(final_ai_metadata, dict):
        disposition = final_ai_metadata.get("knowledge_disposition") or {}
        evidence = final_ai_metadata.get("knowledge_evidence") or {}
        if disposition.get("type") == "knowledge_refusal":
            conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
            await record_knowledge_gap(
                question=str(final_ai_metadata.get("knowledge_question") or ""),
                agent_slug=str(getattr(conversation, "agent_id", "") or ""),
                kb_scope=evidence.get("kb_scope") or [],
                reason=str(disposition.get("reason") or ""),
                uid=str(getattr(conversation, "uid", "") or "") or None,
                conversation_thread_id=thread_id,
                assistant_message_id=final_ai_message.id,
            )
    # 返回本轮最终 assistant 落库行，供调用方做出口多语言本地化（原调用方忽略返回值，行为不变）。
    return output_message


def _extract_interrupt_info(state) -> Any | None:
    """从 LangGraph state 中提取中断信息"""
    if hasattr(state, "tasks") and state.tasks:
        for task in state.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                return task.interrupts[0]

    interrupt_data = state.values.get("__interrupt__")
    if isinstance(interrupt_data, list) and interrupt_data:
        return interrupt_data[0]

    return None


def _coerce_interrupt_payload(info: Any) -> dict:
    """将 LangGraph interrupt 对象转换为 dict 结构。"""
    if isinstance(info, dict):
        return info

    payload = getattr(info, "value", None)
    if isinstance(payload, dict):
        return payload

    questions = getattr(info, "questions", None)
    source = getattr(info, "source", None)
    result: dict[str, Any] = {}
    if isinstance(questions, list):
        result["questions"] = questions
    if isinstance(source, str) and source.strip():
        result["source"] = source
    return result


def _build_ask_user_question_payload(info: Any, thread_id: str) -> dict[str, Any]:
    """将 interrupt 信息标准化为 ask_user_question_required 载荷。"""
    payload = _coerce_interrupt_payload(info)

    questions = _normalize_interrupt_questions(payload.get("questions"))

    if not questions:
        questions = [
            {
                "question_id": str(uuid.uuid4()),
                "question": "请选择一个选项",
                "options": [],
                "multi_select": False,
                "allow_other": True,
            }
        ]

    source = str(payload.get("source") or payload.get("tool_name") or "interrupt")

    return {
        "questions": questions,
        "source": source,
        "thread_id": thread_id,
    }


def _ensure_full_msg(full_msg: AIMessage | None, accumulated_content: list[str]) -> AIMessage | None:
    """如果 full_msg 为空且有累积内容，构建 AIMessage"""
    if not full_msg and accumulated_content:
        return AIMessage(content="".join(accumulated_content))
    return full_msg


def _extract_ai_message(messages: list[Any] | None) -> AIMessage | None:
    """从消息列表中提取最后一条 AIMessage。"""
    if not isinstance(messages, list):
        return None

    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg

        msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else {}
        if msg_dict.get("type") == "ai":
            content = msg_dict.get("content", "")
            return msg if hasattr(msg, "content") else AIMessage(content=content)

    return None


async def _resolve_agent_runtime(
    *,
    db,
    user: User,
    requested_agent_slug: str | None,
    thread_id: str | None,
    agent_kind: Literal["main", "subagent"] = "main",
) -> tuple[Agent, Any, dict]:
    """解析智能体运行时，返回 (Agent, backend, agent_config)"""
    agent_repo = AgentRepository(db)
    conv_repo = ConversationRepository(db)
    resolved_agent_slug = requested_agent_slug

    if thread_id:
        conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
        if conversation:
            if conversation.uid != str(user.uid) or conversation.status == "deleted":
                raise ValueError("对话线程不存在")
            # Conversation.agent_id 是历史字段名，实际保存的是 Agent.slug。
            if requested_agent_slug and requested_agent_slug != conversation.agent_id:
                raise ValueError("已有线程已绑定智能体，不能切换")
            resolved_agent_slug = conversation.agent_id

    if not resolved_agent_slug:
        raise ValueError("缺少必需的 agent_slug 字段")

    agent_item = await agent_repo.get_visible_by_slug(slug=resolved_agent_slug, user=user, kind=agent_kind)
    if not agent_item:
        raise ValueError("智能体不存在或无权限访问")

    backend = agent_manager.get_agent(agent_item.backend_id)
    if not backend:
        raise ValueError(f"智能体后端 {agent_item.backend_id} 不存在")

    agent_config = await normalize_agent_context_config(
        (agent_item.config_json or {}).get("context", {}),
        db=db,
        user=user,
        context_schema=backend.context_schema,
    )
    return agent_item, backend, agent_config


async def check_and_handle_interrupts(
    agent,
    langgraph_config: dict,
    make_chunk,
    meta: dict,
    thread_id: str,
    context,
) -> AsyncIterator[bytes]:
    try:
        graph = await agent.get_graph(context=context)
        state = await graph.aget_state(langgraph_config)

        if not state or not state.values:
            return

        interrupt_info = _extract_interrupt_info(state)
        if interrupt_info:
            question_payload = _build_ask_user_question_payload(interrupt_info, thread_id)
            meta["interrupt"] = question_payload
            yield make_chunk(status="ask_user_question_required", meta=meta, **question_payload)

    except Exception as e:
        logger.exception(f"Error checking interrupts: {e}")


async def _ensure_thread_bound_agent(
    *,
    conv_repo: ConversationRepository,
    thread_id: str,
    uid: str,
    agent_item: Agent,
) -> None:
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation:
        await conv_repo.create_conversation(
            uid=uid,
            agent_id=agent_item.slug,
            thread_id=thread_id,
            metadata={"backend_id": agent_item.backend_id},
        )
        return

    if conversation.agent_id != agent_item.slug:
        raise ValueError("已有线程已绑定智能体，不能切换")


def _normalize_attachment_file_ids(meta: dict | None) -> list[str]:
    file_ids = (meta or {}).get("attachment_file_ids") or []
    if not isinstance(file_ids, list):
        return []

    normalized = []
    seen = set()
    for file_id in file_ids:
        value = str(file_id).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


async def _bind_request_attachments(
    *,
    conv_repo: ConversationRepository,
    thread_id: str,
    request_id: str,
    attachment_file_ids: list[str],
) -> list[dict]:
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation:
        return []

    if attachment_file_ids:
        attachments = await conv_repo.bind_attachments_to_request(conversation.id, request_id, attachment_file_ids)
    else:
        attachments = await conv_repo.get_attachments_by_request_id(conversation.id, request_id)

    return [serialize_attachment(attachment) for attachment in attachments]


async def stream_agent_chat(
    *,
    agent_slug: str,
    thread_id: str | None,
    meta: dict,
    input_message: AgentRunInputMessage,
    current_user,
    db,
    save_user_message: bool = True,
) -> AsyncIterator[bytes]:
    start_time = asyncio.get_event_loop().time()

    def make_chunk(content=None, **kwargs):
        chunk_thread_id = kwargs.pop("thread_id", None) or meta.get("thread_id") or thread_id
        return (
            json.dumps(
                {"request_id": meta.get("request_id"), "response": content, "thread_id": chunk_thread_id, **kwargs},
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )

    meta = dict(meta or {})
    if "request_id" not in meta or not meta.get("request_id"):
        logger.warning("请求缺少 request_id，已自动生成一个新的 request_id")
        meta["request_id"] = str(uuid.uuid4())

    uid = str(current_user.uid)
    if not thread_id:
        thread_id = str(uuid.uuid4())
        logger.warning(f"No thread_id provided, generated new thread_id: {thread_id}")

    query = input_message.content
    image_content = input_message.image_content
    human_message = input_message.require_langchain_message()
    message_type = input_message.message_type

    # 多语言边界（入口）：仅对非中文纯文本问题做「语言检测 + 中译」，让跑题门/检索/judge/拒答分类
    # 仍在中文内部规范语上工作。中文（含中英混合）、多模态、以及带结构化包装（industry_solution /
    # output_format 已并入 langchain 文本导致 content != query）的问题不转换。
    # 受配置 conf.enable_multilingual 门控（设置页可热开关；默认关闭保持既有行为与测试不变）。
    # 内部 query/state 用中文，落库与 init 回显保留用户原文。
    display_query = query
    source_lang: str | None = None
    if (
        conf.enable_multilingual
        and not image_content
        and isinstance(human_message.content, str)
        and human_message.content == query
        and not is_chinese_text(query)
    ):
        inbound = await translate_to_chinese(query)
        if inbound is not None and inbound.chinese and inbound.chinese != query:
            query = inbound.chinese
            human_message = HumanMessage(content=query)
            source_lang = inbound.source_lang

    if conf.enable_content_guard and await content_guard.check(query):
        yield make_chunk(
            status="error", error_type="content_guard_blocked", error_message="输入内容包含敏感词", meta=meta
        )
        return

    try:
        agent_item, agent, agent_config = await _resolve_agent_runtime(
            db=db,
            user=current_user,
            requested_agent_slug=agent_slug,
            thread_id=thread_id,
            agent_kind="subagent" if meta.get("run_type") == "subagent" else "main",
        )
    except ValueError as e:
        yield make_chunk(status="error", error_type="invalid_agent", error_message=str(e), meta=meta)
        return

    meta.update(
        {
            "query": query,
            "agent_slug": agent_item.slug,
            "backend_id": agent_item.backend_id,
            "thread_id": thread_id,
            "uid": current_user.uid,
            "has_image": bool(image_content),
        }
    )

    messages = [human_message]
    input_context = await build_agent_input_context(
        agent_config,
        thread_id=thread_id,
        uid=uid,
        run_id=meta.get("run_id"),
        request_id=meta.get("request_id"),
        # 沙箱作用域：与下方 langgraph_config 的 scope 解析保持一致，缺省回退 thread_id。
        file_thread_id=meta.get("file_thread_id") or thread_id,
        skills_thread_id=meta.get("skills_thread_id") or thread_id,
    )
    _apply_model_override(input_context, meta)
    _apply_subagent_runtime_context(input_context, meta)
    # 行业方案门禁必须作用在 input_context 上：运行期工具绑定由 input_context 重建
    # context（agents/base.py:_stream_input_with_state→get_graph→prepare），只改 dataclass
    # context 会在重建时丢失，导致 industry-solution 技能与其检索工具挂载不上。
    _apply_industry_solution_skill_gate(
        input_context,
        run_type=meta.get("run_type"),
        industry_enabled=bool(meta.get("industry_solution")),
    )
    context = _build_agent_context(agent, input_context)
    _apply_main_chat_read_scope(context, run_type=meta.get("run_type"))
    langfuse_run = _build_langfuse_run_context(
        current_user=current_user,
        thread_id=thread_id,
        agent_id=agent_item.slug,
        backend_id=agent_item.backend_id,
        request_id=meta["request_id"],
        operation="agent_chat_stream",
        message_type=message_type,
        meta=meta,
    )
    full_msg = None
    accumulated_content: list[str] = []
    trace_info: dict[str, Any] = {}
    last_agent_state_signature = ""
    knowledge_evidence = _KnowledgeEvidenceTracker()

    try:
        conv_repo = ConversationRepository(db)
        await _ensure_thread_bound_agent(
            conv_repo=conv_repo,
            thread_id=thread_id,
            uid=uid,
            agent_item=agent_item,
        )

        request_attachments = await _bind_request_attachments(
            conv_repo=conv_repo,
            thread_id=thread_id,
            request_id=meta["request_id"],
            attachment_file_ids=_normalize_attachment_file_ids(meta),
        )

        init_msg = {
            "role": "user",
            "content": display_query,
            "type": "human",
            "message_type": message_type,
            "extra_metadata": {
                "request_id": meta.get("request_id"),
                "attachments": request_attachments,
            },
        }
        if image_content:
            init_msg["image_content"] = image_content
        yield make_chunk(status="init", meta=meta, msg=init_msg)

        if save_user_message:
            try:
                await conv_repo.add_message_by_thread_id(
                    thread_id=thread_id,
                    role="user",
                    content=display_query,
                    message_type=message_type,
                    image_content=image_content,
                    extra_metadata={
                        "raw_message": human_message.model_dump(),
                        "request_id": meta.get("request_id"),
                        "attachments": request_attachments,
                    },
                )
            except Exception as e:
                logger.error(f"Error saving user message: {e}")

        # 智能体流式执行期间不访问业务数据库，先结束预处理事务并归还连接池。
        await db.commit()

        # 先构建 langgraph_config
        # 决策①：入口门与 0 命中预检只对「尚无正常作答的首答轮」触发；续答轮放行主模型结合上下文作答。
        first_substantive = not await _thread_has_answered_turn(conv_repo, thread_id)
        if first_substantive and not image_content and _requires_knowledge_preflight(agent_item.backend_id, query):
            try:
                # 决策① 跑题入口门：明显业务外/闲聊问题在进主模型前拦截 → scope_refusal/off_topic（不转人工）。
                # 语料构建或判定失败一律按业务内放行主模型（宁可放过不可误拦），由 ② 无证据兜底再兜一层。
                corpus = await build_scope_corpus(
                    uid=uid,
                    system_prompt=context.system_prompt,
                    enabled_kb_ids=context.knowledges,
                )
                scope_verdict = await evaluate_scope(query, corpus)
                if scope_verdict == "off_topic":
                    refusal, message_id = SCOPE_REFUSAL_REPLY, f"scope-{meta['request_id']}"
                    # 出口边界：跑题拒绝正文随提问语言本地化后再落库/回显；disposition 仍按中文判定。
                    if source_lang:
                        localized = await translate_from_chinese(refusal, source_lang)
                        if localized:
                            refusal = localized
                    await conv_repo.add_message_by_thread_id(
                        thread_id=thread_id,
                        role="assistant",
                        content=refusal,
                        message_type="text",
                        extra_metadata={
                            "id": message_id,
                            "knowledge_disposition": {
                                "schema_version": DISPOSITION_SCHEMA_VERSION,
                                "type": "scope_refusal",
                                "reason": "off_topic",
                            },
                        },
                        run_id=meta.get("run_id"),
                        request_id=meta.get("request_id"),
                    )
                    await db.commit()
                    yield make_chunk(
                        content=refusal,
                        status="loading",
                        stream_event={
                            "type": "message_delta",
                            "message_id": message_id,
                            "content": refusal,
                        },
                        meta=meta,
                    )
                    yield make_chunk(status="finished", meta=meta)
                    return

                from yuxi.services.global_knowledge_search_service import GlobalKnowledgeSearchService

                results, incomplete = await GlobalKnowledgeSearchService().search_with_status(current_user, query)
                if not results and not incomplete:
                    refusal, message_id = "抱歉，在现有知识库中未找到相关依据。", f"handoff-{meta['request_id']}"
                    if source_lang:
                        localized = await translate_from_chinese(refusal, source_lang)
                        if localized:
                            refusal = localized
                    await conv_repo.add_message_by_thread_id(
                        thread_id=thread_id,
                        role="assistant",
                        content=refusal,
                        message_type="text",
                        extra_metadata={
                            "id": message_id,
                            "handoff_available": True,
                            "handoff_query": query,
                        },
                        run_id=meta.get("run_id"),
                        request_id=meta.get("request_id"),
                    )
                    await db.commit()
                    yield make_chunk(
                        content=refusal,
                        status="loading",
                        stream_event={
                            "type": "message_delta",
                            "message_id": message_id,
                            "content": refusal,
                            "handoff_available": True,
                            "handoff_query": query,
                        },
                        meta=meta,
                    )
                    yield make_chunk(status="knowledge_handoff_available", query=query, meta=meta)
                    yield make_chunk(status="finished", meta=meta)
                    return
            except Exception as exc:
                logger.exception("Knowledge preflight failed; continuing with assistant: %s", exc)
        langgraph_config = {"configurable": {"thread_id": thread_id, "uid": uid}}
        # meta 可显式指定沙箱作用域（file_thread_id/skills_thread_id）：透传给运行时，
        # 让预冷与运行时命中同一沙箱，多个 run 可共用一个沙箱容器；缺省则退回 thread_id，行为不变。
        scope_file = str(meta.get("file_thread_id") or thread_id).strip()
        scope_skills = str(meta.get("skills_thread_id") or thread_id).strip()
        if scope_file != thread_id:
            langgraph_config["configurable"]["file_thread_id"] = scope_file
        if scope_skills != thread_id:
            langgraph_config["configurable"]["skills_thread_id"] = scope_skills

        # LangGraph 会自动从 checkpointer 恢复 state（包括 uploads）
        # 无需手动加载或传递

        # 后台预冷沙箱：大多数运行都会用沙箱（skills 默认全量、文件工具、附件），
        # 提前建容器可避免首个 read_file 阻塞在 ~4s 冷启动上。
        _schedule_sandbox_prewarm(thread_id=thread_id, uid=uid, meta=meta)

        protocol_message_ids: dict[tuple[str, str], str] = {}
        async for mode, payload in _stream_agent_events(
            agent,
            messages,
            input_context=input_context,
            callbacks=langfuse_run.callbacks,
            metadata=langfuse_run.metadata,
            tags=langfuse_run.tags,
        ):
            if mode == "values":
                agent_state = extract_agent_state(payload if isinstance(payload, dict) else {})
                signature = _agent_state_signature(agent_state)
                if signature and signature != last_agent_state_signature:
                    last_agent_state_signature = signature
                    yield make_chunk(status="agent_state", agent_state=agent_state, meta=meta)
                continue

            if mode == "custom":
                compression = _context_compression_payload(payload)
                if compression is not None:
                    yield make_chunk(status="context_compression", compression=compression, meta=meta)
                continue

            if mode == "stream_event":
                if not payload.get("thread_id") or payload.get("thread_id") == thread_id:
                    knowledge_evidence.observe(payload)
                yield make_chunk(
                    status="stream_event",
                    event=payload,
                    namespace=payload.get("namespace") if isinstance(payload, dict) else [],
                    meta=meta,
                    thread_id=payload.get("thread_id") if isinstance(payload, dict) else None,
                )
                continue

            msg, metadata = payload
            namespace = _metadata_namespace(metadata)
            chunk_thread_id = _metadata_thread_id(metadata, thread_id if not namespace else None)
            if namespace and not chunk_thread_id:
                continue

            is_subagent_chunk = bool(chunk_thread_id and chunk_thread_id != thread_id)
            stream_events = _message_payload_yuxi_events(
                msg,
                metadata=metadata,
                namespace=namespace,
                thread_id=chunk_thread_id,
                protocol_message_ids=protocol_message_ids,
            )

            for stream_event in stream_events:
                content = _stream_event_response(stream_event)
                if not is_subagent_chunk and content:
                    trace_info = get_trace_info(langfuse_run)
                    accumulated_content.append(content)
                    content_for_check = "".join(accumulated_content[-10:])
                    if conf.enable_content_guard and await content_guard.check_with_keywords(content_for_check):
                        full_msg = AIMessage(content="".join(accumulated_content))
                        await save_partial_message(
                            conv_repo,
                            thread_id,
                            full_msg,
                            "content_guard_blocked",
                            trace_info=trace_info,
                            run_id=meta.get("run_id"),
                            request_id=meta.get("request_id"),
                        )
                        meta["time_cost"] = asyncio.get_event_loop().time() - start_time
                        yield make_chunk(status="interrupted", message="检测到敏感内容，已中断输出", meta=meta)
                        return

                yield make_chunk(
                    content=content,
                    stream_event=stream_event,
                    metadata=metadata,
                    status="loading",
                    thread_id=chunk_thread_id,
                )

        full_msg = _ensure_full_msg(full_msg, accumulated_content)
        trace_info = get_trace_info(langfuse_run)

        if conf.enable_content_guard and hasattr(full_msg, "content") and await content_guard.check(full_msg.content):
            await save_partial_message(
                conv_repo,
                thread_id,
                full_msg,
                "content_guard_blocked",
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )
            meta["time_cost"] = asyncio.get_event_loop().time() - start_time
            yield make_chunk(status="interrupted", message="检测到敏感内容，已中断输出", meta=meta)
            return

        if knowledge_evidence.should_handoff:
            yield make_chunk(status="knowledge_handoff_available", query=query, meta=meta)

        interrupted = False
        async for chunk in check_and_handle_interrupts(agent, langgraph_config, make_chunk, meta, thread_id, context):
            interrupted = True
            yield chunk

        meta["time_cost"] = asyncio.get_event_loop().time() - start_time
        try:
            graph = await agent.get_graph(context=context)
            state = await graph.aget_state(langgraph_config)
            agent_state = extract_agent_state(getattr(state, "values", {})) if state else {}
        except Exception:
            agent_state = {}

        final_signature = _agent_state_signature(agent_state)
        if final_signature and final_signature != last_agent_state_signature:
            last_agent_state_signature = final_signature
            yield make_chunk(status="agent_state", agent_state=agent_state, meta=meta)

        # 先存储数据库，再返回 finished，避免前端查询时数据未落库
        output_message = None
        try:
            output_message = await save_messages_from_langgraph_state(
                agent_instance=agent,
                thread_id=thread_id,
                conv_repo=conv_repo,
                config_dict=langgraph_config,
                context=context,
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )
        except Exception as e:
            logger.exception(f"Error saving messages from LangGraph state: {e}")
            yield make_chunk(status="warning", message=f"消息保存失败: {e}", meta=meta)

        # 出口边界（正常作答路径）：最终 assistant 内容本地化到提问语言并写回 content。
        # 中文原文与 knowledge_disposition 仍在 extra_metadata 中——分类在保存阶段已按中文跑完，
        # 后续判定只读 metadata，覆写展示文本不影响判定链（LangGraph state 仍持中文供续答）。
        if source_lang and output_message is not None and str(getattr(output_message, "content", "") or "").strip():
            localized = await translate_from_chinese(str(output_message.content), source_lang)
            if localized and localized != str(output_message.content):
                await conv_repo.update_message_content(output_message.id, localized)

        if interrupted:
            return

        yield make_chunk(status="finished", meta=meta)

    except (asyncio.CancelledError, ConnectionError) as e:
        logger.warning(f"Client disconnected, cancelling stream: {e}")

        async def save_cleanup():
            nonlocal full_msg
            full_msg = _ensure_full_msg(full_msg, accumulated_content)

            async with pg_manager.get_async_session_context() as new_db:
                new_conv_repo = ConversationRepository(new_db)
                await save_partial_message(
                    new_conv_repo,
                    thread_id,
                    full_msg=full_msg,
                    error_message="对话已中断" if not full_msg else None,
                    error_type="interrupted",
                    trace_info=trace_info,
                    run_id=meta.get("run_id"),
                    request_id=meta.get("request_id"),
                )

        cleanup_task = asyncio.create_task(save_cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"Error during cleanup save: {exc}")

        yield make_chunk(status="interrupted", message="对话已中断", meta=meta)

    except Exception as e:
        logger.exception(f"Error streaming messages: {e}")

        error_msg = f"Error streaming messages: {e}"
        error_type = "unexpected_error"

        full_msg = _ensure_full_msg(full_msg, accumulated_content)

        async with pg_manager.get_async_session_context() as new_db:
            new_conv_repo = ConversationRepository(new_db)
            await save_partial_message(
                new_conv_repo,
                thread_id,
                full_msg=full_msg,
                error_message=error_msg,
                error_type=error_type,
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )

        yield make_chunk(status="error", error_type=error_type, error_message=error_msg, meta=meta)
    finally:
        flush_langfuse()


async def stream_agent_resume(
    *,
    thread_id: str,
    resume_input: Any,
    meta: dict,
    current_user,
    db,
) -> AsyncIterator[bytes]:
    start_time = asyncio.get_event_loop().time()

    def make_resume_chunk(content=None, **kwargs):
        chunk_thread_id = kwargs.pop("thread_id", None) or meta.get("thread_id") or thread_id
        return (
            json.dumps(
                {"request_id": meta.get("request_id"), "response": content, "thread_id": chunk_thread_id, **kwargs},
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )

    yield make_resume_chunk(status="init", meta=meta)

    resume_command = Command(resume=resume_input)

    uid = str(current_user.uid)
    try:
        agent_item, agent, agent_config = await _resolve_agent_runtime(
            db=db,
            user=current_user,
            requested_agent_slug=None,
            thread_id=thread_id,
        )
    except ValueError as e:
        yield make_resume_chunk(status="error", error_type="invalid_agent", error_message=str(e), meta=meta)
        return

    # 恢复流执行期间不访问业务数据库，先结束运行时解析事务并归还连接池。
    await db.commit()

    meta["agent_slug"] = agent_item.slug
    meta["backend_id"] = agent_item.backend_id
    input_context = await build_agent_input_context(
        agent_config or {},
        thread_id=thread_id,
        uid=uid,
        run_id=meta.get("run_id"),
        request_id=meta.get("request_id"),
        file_thread_id=meta.get("file_thread_id") or thread_id,
        skills_thread_id=meta.get("skills_thread_id") or thread_id,
    )
    _apply_model_override(input_context, meta)
    # 同 stream_agent_chat：门禁作用在 input_context，运行期 context 由它重建才会挂载。
    _apply_industry_solution_skill_gate(
        input_context,
        run_type=meta.get("run_type"),
        industry_enabled=bool(meta.get("industry_solution")),
    )
    context = _build_agent_context(agent, input_context)
    _apply_main_chat_read_scope(context, run_type=meta.get("run_type"))
    langfuse_run = _build_langfuse_run_context(
        current_user=current_user,
        thread_id=thread_id,
        agent_id=agent_item.slug,
        backend_id=agent_item.backend_id,
        request_id=meta.get("request_id") or str(uuid.uuid4()),
        operation="agent_chat_resume",
        message_type="resume",
        meta=meta,
    )
    trace_info: dict[str, Any] = {}
    last_agent_state_signature = ""

    stream_source = agent.stream_resume_with_state(
        resume_command,
        input_context=input_context,
        callbacks=langfuse_run.callbacks,
        metadata=langfuse_run.metadata,
        tags=langfuse_run.tags,
    )

    # 后台预冷沙箱，避免恢复执行的首个 read_file 阻塞在 ~4s 冷启动上
    _schedule_sandbox_prewarm(thread_id=thread_id, uid=uid, meta=meta)

    protocol_message_ids: dict[tuple[str, str], str] = {}

    try:
        async for mode, payload in stream_source:
            if mode == "values":
                agent_state = extract_agent_state(payload if isinstance(payload, dict) else {})
                signature = _agent_state_signature(agent_state)
                if signature and signature != last_agent_state_signature:
                    last_agent_state_signature = signature
                    yield make_resume_chunk(status="agent_state", agent_state=agent_state, meta=meta)
                continue

            if mode == "stream_event":
                event_payload = payload if isinstance(payload, dict) else {}
                yield make_resume_chunk(
                    status="stream_event",
                    event=event_payload,
                    namespace=event_payload.get("namespace") or [],
                    meta=meta,
                    thread_id=event_payload.get("thread_id"),
                )
                continue

            if mode == "custom":
                compression = _context_compression_payload(payload)
                if compression is not None:
                    yield make_resume_chunk(status="context_compression", compression=compression, meta=meta)
                continue

            if mode != "messages":
                continue

            msg, metadata = payload
            metadata = dict(metadata or {})
            namespace = _metadata_namespace(metadata)
            chunk_thread_id = _metadata_thread_id(metadata, thread_id if not namespace else None)
            if namespace and not chunk_thread_id:
                continue

            if chunk_thread_id == thread_id:
                trace_info = get_trace_info(langfuse_run)

            stream_events = _message_payload_yuxi_events(
                msg,
                metadata=metadata,
                namespace=namespace,
                thread_id=chunk_thread_id,
                protocol_message_ids=protocol_message_ids,
            )

            for stream_event in stream_events:
                content = _stream_event_response(stream_event)
                yield make_resume_chunk(
                    content=content,
                    stream_event=stream_event,
                    metadata=metadata,
                    status="loading",
                    thread_id=chunk_thread_id,
                )

        langgraph_config = {"configurable": {"thread_id": thread_id, "uid": uid}}
        interrupted = False
        async for chunk in check_and_handle_interrupts(
            agent, langgraph_config, make_resume_chunk, meta, thread_id, context
        ):
            interrupted = True
            yield chunk

        meta["time_cost"] = asyncio.get_event_loop().time() - start_time

        try:
            graph = await agent.get_graph(context=context)
            state = await graph.aget_state(langgraph_config)
            agent_state = extract_agent_state(getattr(state, "values", {})) if state else {}
        except Exception:
            agent_state = {}

        final_signature = _agent_state_signature(agent_state)
        if final_signature and final_signature != last_agent_state_signature:
            yield make_resume_chunk(status="agent_state", agent_state=agent_state, meta=meta)

        # 先存储数据库，再返回 finished，避免前端查询时数据未落库
        conv_repo = ConversationRepository(db)
        try:
            await save_messages_from_langgraph_state(
                agent_instance=agent,
                thread_id=thread_id,
                conv_repo=conv_repo,
                config_dict=langgraph_config,
                context=context,
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )
        except Exception as e:
            logger.exception(f"Error saving messages from LangGraph state: {e}")
            yield make_resume_chunk(status="warning", message=f"消息保存失败: {e}", meta=meta)

        if interrupted:
            return

        yield make_resume_chunk(status="finished", meta=meta)

    except (asyncio.CancelledError, ConnectionError) as e:
        logger.warning(f"Client disconnected during resume: {e}")

        async with pg_manager.get_async_session_context() as new_db:
            new_conv_repo = ConversationRepository(new_db)
            await save_partial_message(
                new_conv_repo,
                thread_id,
                error_message="对话恢复已中断",
                error_type="resume_interrupted",
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )

        yield make_resume_chunk(status="interrupted", message="对话恢复已中断", meta=meta)

    except Exception as e:
        logger.exception(f"Error during resume: {e}")

        async with pg_manager.get_async_session_context() as new_db:
            new_conv_repo = ConversationRepository(new_db)
            await save_partial_message(
                new_conv_repo,
                thread_id,
                error_message=f"Error during resume: {e}",
                error_type="resume_error",
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )

        yield make_resume_chunk(message=f"Error during resume: {e}", status="error")
    finally:
        flush_langfuse()


def _serialize_state_messages(values: dict[str, Any]) -> list[dict[str, Any]]:
    messages = values.get("messages") if isinstance(values, dict) else None
    if not isinstance(messages, list):
        return []
    serialized = []
    for message in messages:
        if hasattr(message, "model_dump"):
            serialized.append(message.model_dump())
        elif isinstance(message, dict):
            serialized.append(dict(message))
        else:
            serialized.append({"type": "unknown", "content": str(message)})
    return serialized


async def _read_checkpoint_state(agent, *, uid: str, thread_id: str, context):
    graph = await agent.get_graph(context=context)
    langgraph_config = {"configurable": {"uid": uid, "thread_id": thread_id}}
    return await graph.aget_state(langgraph_config)


async def get_agent_state_view(
    *,
    thread_id: str,
    current_user: User,
    db,
    include_messages: bool = False,
) -> dict:
    from fastapi import HTTPException

    current_uid = str(current_user.uid)
    conv_repo = ConversationRepository(db)
    agent_repo = AgentRepository(db)
    run_repo = AgentRunRepository(db)
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if conversation:
        if conversation.uid != str(current_uid) or conversation.status == "deleted":
            raise HTTPException(status_code=404, detail="对话线程不存在")

        agent_item = await agent_repo.get_by_slug(conversation.agent_id)
        if not agent_item:
            raise HTTPException(status_code=404, detail="智能体不存在")
        agent = agent_manager.get_agent(agent_item.backend_id)
        if not agent:
            raise HTTPException(status_code=404, detail="智能体后端不存在")
        agent_config = await normalize_agent_context_config(
            (agent_item.config_json or {}).get("context", {}),
            db=db,
            user=current_user,
            context_schema=agent.context_schema,
        )
        input_context = await build_agent_input_context(
            agent_config,
            thread_id=thread_id,
            uid=current_uid,
        )
        latest_run = await run_repo.get_latest_run_by_thread_for_user(thread_id, current_uid)
        if latest_run and isinstance(latest_run.input_payload, dict):
            model_spec = latest_run.input_payload.get("model_spec")
            if isinstance(model_spec, str) and model_spec.strip():
                input_context["model"] = model_spec.strip()
        context = _build_agent_context(agent, input_context)
        state = await _read_checkpoint_state(agent, uid=current_uid, thread_id=thread_id, context=context)
        values = getattr(state, "values", {}) if state else {}
        response = {"agent_state": extract_agent_state(values)}
        relation = await SubagentThreadRepository(db).get_by_child_conversation_for_user(
            conversation.id,
            str(current_uid),
        )
        if relation:
            parent_conversation = await conv_repo.get_conversation_by_id(relation.parent_conversation_id)
            if (
                not parent_conversation
                or parent_conversation.uid != str(current_uid)
                or parent_conversation.status == "deleted"
            ):
                raise HTTPException(status_code=404, detail="父对话线程不存在")
            response["parent_thread_id"] = parent_conversation.thread_id
            response["subagent_thread"] = relation.to_dict()
            latest_run = await run_repo.get_latest_subagent_run_by_thread_for_user(
                thread_id,
                str(current_uid),
            )
            if latest_run:
                try:
                    response["subagent_run"] = serialize_subagent_run_state(latest_run)
                except ValueError as exc:
                    logger.error(f"子智能体运行记录格式异常: thread_id={thread_id}, run_id={latest_run.id}, {exc}")
                    raise HTTPException(status_code=500, detail="子智能体运行记录格式异常") from exc
        if include_messages:
            response["messages"] = _serialize_state_messages(values)
        return response

    # 子智能体线程在创建时必然同时写入子对话与线程关系（见 SubagentRunService.start），
    # 由上面的 conversation 分支统一处理；走到这里说明该 thread 没有对应对话，即线程不存在。
    raise HTTPException(status_code=404, detail="对话线程不存在")
