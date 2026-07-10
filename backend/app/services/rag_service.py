from __future__ import annotations

import json
from typing import Any

from app.services.tools import KnowledgeTools


REJECT_MESSAGE = "抱歉，在现有知识库中未找到相关依据，已通知管理员补充。"


class RAGService:
    """RAG service using single-engine tool-use style orchestration."""

    def __init__(self, llm: Any, tools: KnowledgeTools | None = None) -> None:
        self.llm = llm
        self.tools = tools or KnowledgeTools()
        self.tool_definitions = [
            {"name": "retrieve", "description": "语义检索知识库文档片段"},
            {"name": "bm25_search", "description": "关键词精确检索知识库文档片段"},
            {"name": "graph_search", "description": "图谱检索实体关系信息"},
        ]

    async def ask(self, question: str, kb_id: str, conversation_id: str | None = None) -> dict[str, Any]:
        messages = self._build_messages(question)
        response = await self.llm.generate_with_tools(
            messages=messages,
            tools=self.tool_definitions,
            tool_choice="auto",
            stream=False,
        )

        sources: list[dict[str, Any]] = []
        tool_calls = self._get_tool_calls(response)
        if tool_calls:
            for tool_call in tool_calls:
                tool_result = await self._execute_tool(tool_call, kb_id)
                sources.extend(tool_result.get("results", []))
                if tool_call["name"] == "retrieve":
                    bm25_call = {
                        "name": "bm25_search",
                        "arguments": dict(tool_call.get("arguments", {})),
                    }
                    bm25_result = await self._execute_tool(bm25_call, kb_id)
                    sources.extend(bm25_result.get("results", []))
                sources = self._deduplicate_sources(sources)
                messages.append({"role": "tool", "content": json.dumps(tool_result, ensure_ascii=False)})

            final_response = await self.llm.generate_with_tools(
                messages=messages,
                tools=self.tool_definitions,
                tool_choice="none",
                stream=False,
            )
            answer = final_response.get("content", "")
        else:
            answer = response.get("content", "")

        if sources and max(source.get("score", 0.0) for source in sources) < 0.6:
            answer = REJECT_MESSAGE
            sources = []

        return {"answer": answer, "sources": sources[:5]}

    async def _execute_tool(self, tool_call: dict[str, Any], kb_id: str) -> dict[str, Any]:
        name = tool_call["name"]
        args = dict(tool_call.get("arguments", {}))
        args.setdefault("kb_id", kb_id)
        if name == "retrieve":
            results = await self.tools.retrieve(**args)
        elif name == "bm25_search":
            results = await self.tools.bm25_search(**args)
        elif name == "graph_search":
            results = await self.tools.graph_search(**args)
        else:
            results = []
        return {"status": "ok" if results else "no_relevant_results", "results": results}

    def _deduplicate_sources(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        deduplicated = []
        for source in sources:
            key = source.get("chunk_id") or source.get("content")
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(source)
        return deduplicated

    def _build_messages(self, question: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": "你是通信设备知识库助手。必须先使用工具检索知识库，再基于工具结果回答。",
            },
            {"role": "user", "content": question},
        ]

    def _get_tool_calls(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        return list(response.get("tool_calls", []))
