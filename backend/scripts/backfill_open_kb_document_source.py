"""一次性回填脚本：为历史 open_kb_document 工具调用补上 source 字段。

历史 tool_output 早于 source 字段引入，缺少 source 时前端来源面板会退化为
file_id 显示，无法与答案中的文件名归因匹配。本脚本按 OpenOutputSchema 的字段
顺序在 file_id 后注入 source（真实文件名，取文件记录的 filename，缺省回退 file_id）。
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select, text, update

from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import ToolCall
from yuxi.storage.postgres.models_knowledge import KnowledgeFile


async def main() -> None:
    async with pg_manager.get_async_session_context() as session:
        result = await session.execute(
            select(ToolCall).where(
                ToolCall.tool_name == "open_kb_document",
                ToolCall.status == "success",
                ToolCall.tool_output.is_not(None),
                ToolCall.tool_output.not_like('%"source"%'),
            )
        )
        calls = result.scalars().all()
        print(f"待回填记录数: {len(calls)}", flush=True)

        # 一次取齐所有相关文件记录，避免逐条查询
        file_ids: set[str] = set()
        for call in calls:
            if not call.tool_output:
                continue
            try:
                parsed = json.loads(call.tool_output)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("file_id"):
                file_ids.add(parsed["file_id"])
        files_result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id.in_(sorted(file_ids))))
        filename_by_file_id = {record.file_id: record.filename for record in files_result.scalars().all()}
        print(f"关联文件记录数: {len(filename_by_file_id)}", flush=True)

        updated = 0
        skipped = 0
        for call in calls:
            try:
                payload = json.loads(call.tool_output)
            except (json.JSONDecodeError, TypeError):
                skipped += 1
                continue
            if not isinstance(payload, dict) or "file_id" not in payload or "source" in payload:
                skipped += 1
                continue
            file_id = payload["file_id"]
            source = filename_by_file_id.get(file_id) or file_id
            # 在 file_id 后注入 source，与 OpenOutputSchema 字段顺序保持一致
            reordered = {}
            for key, value in payload.items():
                reordered[key] = value
                if key == "file_id":
                    reordered["source"] = source
            call.tool_output = json.dumps(reordered, ensure_ascii=False)
            updated += 1

        print(f"更新记录数: {updated}, 跳过: {skipped}", flush=True)
        await session.commit()
        print("回填完成", flush=True)

        # 校验
        check = await session.execute(
            text("SELECT count(*) FROM tool_calls WHERE tool_name='open_kb_document' AND status='success' AND tool_output NOT LIKE '%\"source\"%'")
        )
        print(f"回填后仍缺 source 的记录数: {check.scalar()}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
