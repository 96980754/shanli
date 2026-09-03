from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuiltinSkillSpec:
    slug: str
    source_dir: Path
    description: str = ""
    version: str = "1.0.0"
    tool_dependencies: tuple[str, ...] = ()
    mcp_dependencies: tuple[str, ...] = ()
    skill_dependencies: tuple[str, ...] = ()
    enabled: bool = False


_SKILLS_ROOT = Path(__file__).resolve().parent

BUILTIN_SKILLS: list[BuiltinSkillSpec] = [
    BuiltinSkillSpec(
        slug="image-gen",
        source_dir=_SKILLS_ROOT / "image-gen",
        description="在 Agent 沙盒中生成图片并保存到 outputs，默认支持 Qwen-Image，也可接入其它图片生成接口。",
        version="2026.06.02",
        tool_dependencies=("present_artifacts",),
    ),
    BuiltinSkillSpec(
        slug="deep-research",
        source_dir=_SKILLS_ROOT / "deep-research",
        description="深度研究编排方法论：澄清范围、拆解规划、并行调度子智能体调研、对抗式核验、综合成带引用的结构化报告。",
        version="2026.06.05",
        tool_dependencies=("tavily_search",),
    ),
    BuiltinSkillSpec(
        slug="knowledge-base",
        source_dir=_SKILLS_ROOT / "knowledge-base",
        description="使用 Yuxi 知识库进行检索、打开文档、文档内定位和搜索文件。",
        version="2026.06.24",
        tool_dependencies=(
            "list_kbs",
            "query_kb",
            "query_kbs",
            "find_kb_document",
            "open_kb_document",
            "search_file",
            "read_document_version",
        ),
        enabled=True,
    ),
    BuiltinSkillSpec(
        slug="industry-solution",
        source_dir=_SKILLS_ROOT / "industry-solution",
        description="分别检索多个产品资料，生成带可追溯来源的行业解决方案并交付 Word 文档。",
        version="2026.08.15",
        tool_dependencies=(
            "list_kbs",
            "research_industry_products",
            "export_industry_solution_docx",
            "present_artifacts",
        ),
        skill_dependencies=("knowledge-base",),
        enabled=True,
    ),
    BuiltinSkillSpec(
        slug="mysql-reporter",
        source_dir=_SKILLS_ROOT / "mysql-reporter",
        description="基于 MySQL 数据库生成查询报表和可视化图表，适合分析业务指标、统计趋势，并用 Charts MCP 展示结果。",
        version="2026.06.05",
        mcp_dependencies=("mcp-server-chart",),
    ),
]
