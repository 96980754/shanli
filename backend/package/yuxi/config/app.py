"""应用配置模块。"""

from __future__ import annotations

import copy
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import tomli
import tomli_w
from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator
from pydantic_core import PydanticUndefined

from yuxi.config import cache as runtime_cache
from yuxi.knowledge.parser.registry import PROCESSOR_TYPES
from yuxi.utils.logging_config import logger

READONLY_CONFIG_FIELDS = frozenset({"save_dir"})
DEFAULT_OCR_ENGINE = "rapid_ocr"


def _get_available_ocr_engines() -> set[str]:
    return {"disable", *PROCESSOR_TYPES}


def _normalize_default_ocr_engine(value: Any) -> str:
    engine = str(value or "").strip() or DEFAULT_OCR_ENGINE
    if engine not in _get_available_ocr_engines():
        raise ValueError(f"不支持的默认 OCR 引擎: {engine}")
    return engine


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _normalize_wecom_service_urls(value: Any) -> list[str]:
    """把单个客服条目的入口配置归一为 https URL 列表（行/英文逗号/全角逗号分隔，去重）。"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n,，]", value)
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        raise ValueError("客服入口配置必须为 URL 字符串或 URL 列表")

    urls: list[str] = []
    for raw in parts:
        url = str(raw or "").strip()
        if not url:
            continue
        if not _is_https_url(url):
            raise ValueError(f"企微客服 URL 必须是 https 地址: {url[:80]}")
        if url not in urls:
            urls.append(url)
    return urls


class CustomerServiceEntry(BaseModel):
    """客服命名条目。id 为业务线绑定的稳定键；name 为界面显示名；urls 为 1..N 个企微入口
    （一个客服团队多个账号轮替扛量，见 P1 多客服接入）。"""

    id: str
    name: str
    urls: list[str]

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        cid = str(value or "").strip()
        if not cid:
            raise ValueError("客服 id 不能为空")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid):
            raise ValueError("客服 id 仅允许字母/数字/下划线/中划线，≤64 字符")
        return cid

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValueError("客服名称不能为空")
        if len(name) > 40:
            raise ValueError("客服名称过长（≤40）")
        return name


def _customer_services_from_urls(urls: list[str]) -> list[dict[str, Any]]:
    """存量裸 URL 列表 / 环境变量首启迁移：每个 URL 建成一条命名条目（保序），未绑定走兜底池。"""
    return [{"id": f"cs{i + 1}", "name": f"客服{i + 1}", "urls": [url]} for i, url in enumerate(urls)]


def _normalize_wecom_customer_services(value: Any) -> list[dict[str, Any]]:
    """把客服条目配置归一为规范 dict 行列表（{id, name, urls} 落库/热同步；运行时容错读取）。

    接受条目 dict（或 dict 列表）；缺 id 的补发稳定随机 id；urls 归一 + https 校验 + 非空；
    id 全局唯一。历史「按业务域 URL 映射」dict 在此语义下无 url 列表 → 抛错提示（不再是客服池语义）。
    """
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in items:
        if isinstance(item, BaseModel):
            item = item.model_dump()
        if not isinstance(item, dict):
            raise ValueError("客服条目配置必须为对象列表")
        raw = dict(item)
        raw.setdefault("id", uuid.uuid4().hex[:12])
        try:
            urls = _normalize_wecom_service_urls(raw.get("urls"))
            if not urls:
                raise ValueError("客服至少需要一个 https 入口 URL")
            entry = CustomerServiceEntry(id=str(raw["id"]).strip(), name=raw.get("name"), urls=urls)
        except ValidationError as exc:
            raise ValueError(f"客服条目配置非法: {exc}") from exc
        if entry.id in seen_ids:
            raise ValueError(f"客服 id 重复: {entry.id}")
        seen_ids.add(entry.id)
        rows.append(entry.model_dump())
    return rows


# 业务线（拒答分类域值）默认清单。code 为稳定英文 snake_case（数据库/统计用，一经使用不更名），
# name 为显示名；keywords 主要供跑题门词表认词（业务特有词，避免泛词误放行跑题），judge 靠 name 语义判定。
# unknown 为系统保留兜底值，不进入清单。部署方可按业务在设置页增删/改名/补词。
_DEFAULT_BUSINESS_LINES: list[dict[str, Any]] = [
    {"code": "diaodutai", "name": "调度台", "keywords": ["调度台", "mcx", "指挥调度"]},
    {"code": "terminal", "name": "终端", "keywords": ["cat1", "cat1模组", "安卓", "f10"]},
    {"code": "ops", "name": "运营平台", "keywords": ["运营平台", "网管"]},
    {"code": "mno", "name": "MNO网优", "keywords": ["网络优化", "覆盖", "信号"]},
    {"code": "kefu", "name": "通用客服", "keywords": []},
]
# 关键词上限：单条限长、每行限条数，防误配撑爆 prompt/词表。
_BUSINESS_LINE_KEYWORD_LIMIT = 20
_BUSINESS_LINE_KEYWORD_MAX_CHARS = 40


class BusinessLine(BaseModel):
    """业务线（拒答分类的 domain 可选值）单行。code/name/keywords 均为规范化的规范输入。

    customer_service_ids：该线转人工绑定的客服条目 id（引用 wecom_customer_services）；
    空表示未绑定，转接走通用客服兜底链。保存时经 Config 跨字段校验存在性。
    """

    code: str
    name: str
    keywords: list[str] = Field(default_factory=list)
    customer_service_ids: list[str] = Field(default_factory=list, description="绑定的企微客服条目 id")

    @field_validator("customer_service_ids")
    @classmethod
    def _validate_customer_service_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value or []:
            cid = str(raw or "").strip()
            if cid and cid not in seen:
                seen.add(cid)
                cleaned.append(cid)
        return cleaned

    @field_validator("code")
    @classmethod
    def _validate_code(cls, value: str) -> str:
        code = str(value or "").strip().lower()
        if code == "unknown":
            raise ValueError("code 'unknown' 为系统保留兜底值，不可自定义")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", code):
            raise ValueError("code 须为小写字母开头的 snake_case，≤32 字符，仅含小写字母/数字/下划线")
        return code

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValueError("业务线名称不能为空")
        if len(name) > 40:
            raise ValueError("业务线名称过长（≤40）")
        return name

    @field_validator("keywords", mode="before")
    @classmethod
    def _validate_keywords(cls, value: Any) -> list[str]:
        # before 模式：字符串按分隔符拆分；list[str] 原样逐条清洗（去空/去重/限长限条）。
        tokens = re.split(r"[,，、/\s]+", value) if isinstance(value, str) else value or []
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in tokens:
            term = str(raw or "").strip()
            if not term or term.casefold() in seen:
                continue
            seen.add(term.casefold())
            cleaned.append(term[:_BUSINESS_LINE_KEYWORD_MAX_CHARS])
            if len(cleaned) >= _BUSINESS_LINE_KEYWORD_LIMIT:
                break
        return cleaned


def _default_business_lines() -> list[dict[str, Any]]:
    return copy.deepcopy(_DEFAULT_BUSINESS_LINES)


def _normalize_business_lines(value: Any) -> list[dict[str, Any]]:
    """把业务线配置归一为规范行列表（dict 形式落库/热同步；运行时经 resolve_business_lines 校验读取）。

    接受：单行 dict/BusinessLine、行列表。code 清单内唯一；unknown 保留不可配。
    """
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, BaseModel):
            item = item.model_dump()
        try:
            line = BusinessLine.model_validate(item)
        except ValidationError as exc:
            raise ValueError(f"业务线配置非法: {exc}") from exc
        if line.code in seen:
            raise ValueError(f"业务线 code 重复: {line.code}")
        seen.add(line.code)
        row = line.model_dump()
        if not row["customer_service_ids"]:  # 空绑定不落盘，保持存量行紧凑
            row.pop("customer_service_ids", None)
        rows.append(row)
    return rows


class Config(BaseModel):
    """应用配置类。

    `save_dir` 只在启动时决定配置文件位置，运行时不可修改。管理员保存配置时先写
    `base.toml`，再把可运行时同步的字段写入 Redis 快照（`yuxi:runtime_config`）。
    其他进程通过 `start_runtime_sync()` 启动的后台线程周期性拉取该快照刷新内存值。
    """

    save_dir: str = Field(default="saves", description="保存目录", exclude=True)
    enable_content_guard: bool = Field(default=False, description="是否启用内容审查")
    enable_content_guard_llm: bool = Field(default=False, description="是否启用LLM内容审查")
    enable_multilingual: bool = Field(
        default=False, description="是否启用多语言边界翻译（非中文问题翻译成中文处理，回答再译回提问语言）"
    )
    default_model: str = Field(
        default="siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        description="默认对话模型",
    )
    fast_model: str = Field(
        default="siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        description="快速响应模型",
    )
    embed_model: str = Field(
        default="siliconflow-cn:Pro/BAAI/bge-m3",
        description="默认 Embedding 模型",
    )
    reranker: str = Field(
        default="siliconflow-cn:Pro/BAAI/bge-reranker-v2-m3",
        description="默认 Re-Ranker 模型",
    )
    transcription_model: str | None = Field(default=None, description="默认语音转写模型")
    content_guard_llm_model: str = Field(
        default="siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        description="内容审查LLM模型",
    )
    default_ocr_engine: str = Field(default=DEFAULT_OCR_ENGINE, description="默认 OCR 解析引擎")

    # 文档清洗链路（PR12 吸收）
    document_cleaning_auto_confirm: bool = Field(
        default=True,
        description="文档规则清洗后是否默认自动确认并入库",
    )
    document_ai_cleaning_enabled: bool = Field(default=False, description="是否启用可选 AI 文档清洗")
    document_ai_cleaning_model: str | None = Field(default=None, description="AI 文档清洗模型 spec")
    document_ai_cleaning_temperature: float = Field(default=0.0, description="AI 文档清洗温度")
    document_ai_cleaning_timeout_seconds: int = Field(default=60, description="AI 文档清洗单块超时秒数")
    document_ai_cleaning_chunk_chars: int = Field(default=12000, description="AI 文档清洗单块最大字符数")
    document_cleaning_max_chars: int = Field(default=2_000_000, description="文档清洗草稿最大字符数")
    # 文档信息增强（PR12 吸收）
    document_enrichment_auto_generate: bool = Field(default=False, description="文档确认入库后是否自动生成信息增强")
    document_enrichment_model: str | None = Field(default=None, description="文档信息增强模型 spec")
    document_enrichment_temperature: float = Field(default=0.0, description="文档信息增强模型温度")
    document_enrichment_timeout_seconds: int = Field(default=60, description="文档信息增强单次模型调用超时秒数")
    document_enrichment_chunk_chars: int = Field(default=12000, description="文档信息增强单块最大字符数")
    document_enrichment_output_attempts: int = Field(default=2, description="文档信息增强结构化输出最大尝试次数")
    document_enrichment_summary_max_chars: int = Field(default=1000, description="文档摘要最大字符数")
    document_enrichment_keyword_limit: int = Field(default=12, description="文档关键词最大数量")
    document_enrichment_tag_limit: int = Field(default=8, description="文档标签最大数量")
    document_enrichment_max_chars: int = Field(default=2_000_000, description="文档信息增强正文最大字符数")
    # 文档 QA 知识对（PR12 吸收）
    document_qa_auto_generate: bool = Field(default=False, description="文档确认入库后是否自动生成 QA 草稿")
    document_qa_model: str | None = Field(default=None, description="文档 QA 生成模型 spec")
    document_qa_temperature: float = Field(default=0.0, description="文档 QA 生成温度")
    document_qa_timeout_seconds: int = Field(default=60, description="文档 QA 单次模型调用超时秒数")
    document_qa_output_attempts: int = Field(default=2, description="文档 QA 结构化输出最大尝试次数")
    document_qa_max_pairs_per_document: int = Field(default=20, description="单文档最大 QA 数量")
    document_qa_max_pairs_per_chunk: int = Field(default=3, description="单 chunk 最大 QA 数量")
    document_qa_question_max_chars: int = Field(default=300, description="QA 问题最大字符数")
    document_qa_answer_max_chars: int = Field(default=2000, description="QA 答案最大字符数")
    document_qa_batch_size: int = Field(default=20, description="文档 QA 批量生成大小")

    sandbox_provider: str = Field(default="provisioner", description="沙箱提供者")
    sandbox_provisioner_url: str = Field(default="http://sandbox-provisioner:8002", description="沙箱服务地址")
    sandbox_virtual_path_prefix: str = Field(default="/home/gem/user-data", description="沙箱用户目录前缀")
    sandbox_exec_timeout_seconds: int = Field(default=180, description="沙箱执行超时时间（秒）")
    sandbox_max_output_bytes: int = Field(default=262144, description="沙箱最大输出字节数")
    sandbox_keepalive_interval_seconds: int = Field(default=30, description="沙箱保活间隔")

    # 拒答转人工：企微客服命名条目（管理界面可配，保存后立即生效并同步到各进程）。
    # 每条 {id, name, urls[]}；业务线绑定到条目 id，转人工按线在绑定条目 URL 间轮替转接。
    # 环境变量 WECOM_CUSTOMER_SERVICE_URL 仅作为首次启动默认（每条一个 URL）。
    wecom_customer_services: list[dict[str, Any]] = Field(
        default_factory=list,
        description="企微客服（微信客服）命名条目：每行 {id, name, urls[]}；"
        "转人工按业务线绑定在条目 URL 间轮替转接；全空时转人工不可用",
    )
    # 业务线（拒答分类 domain 可选值）清单：设置页可维护，judge/跑题门/域校验共用。
    # 存规范 dict 行（code/name/keywords/customer_service_ids）以便 toml/Redis JSON 直落；
    # 运行时经 resolve_business_lines 校验读取。
    business_lines: list[dict[str, Any]] = Field(
        default_factory=_default_business_lines,
        description="业务线（拒答分类）清单：每行 {code, name, keywords, customer_service_ids}；"
        "code 唯一稳定、unknown 系统保留",
    )

    _config_file: Path | None = PrivateAttr(default=None)
    _runtime_sync_thread: Any = PrivateAttr(default=None)

    model_config = {"arbitrary_types_allowed": True, "extra": "allow"}

    def __init__(self, **data):
        super().__init__(**data)
        self._setup_paths()
        self._load_user_config()
        self._handle_environment()

    def _setup_paths(self) -> None:
        self._config_file = Path(self.save_dir) / "config" / "base.toml"
        self._config_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_user_config(self) -> None:
        if not self._config_file or not self._config_file.exists():
            logger.info(f"Config file not found, using defaults: {self._config_file}")
            return

        logger.info(f"Loading config from {self._config_file}")
        try:
            with open(self._config_file, "rb") as f:
                user_config = tomli.load(f)

            for key, value in user_config.items():
                if key in READONLY_CONFIG_FIELDS:
                    logger.warning(f"Readonly config key ignored: {key}")
                elif key in {"wecom_customer_service_url", "wecom_customer_service_urls"}:
                    # 旧客服入口（单值 / 裸 URL 列表）：迁移为命名条目，下面统一处理。
                    continue
                elif key in type(self).model_fields:
                    try:
                        setattr(self, key, self._normalize_config_value(key, value))
                    except ValueError as exc:
                        logger.warning(f"Invalid config key ignored: {key} ({exc})")
                else:
                    logger.warning(f"Unknown config key: {key}")

            # 旧客服入口迁移：wecom_customer_service_url（单值）→ wecom_customer_service_urls（裸 URL 列表）
            # → wecom_customer_services 命名条目（新列表为空时每 URL 建一条，保序；手配的域名映射 dict 忽略）。
            if not self.wecom_customer_services:
                legacy_urls: list[str] = []
                for legacy_key in ("wecom_customer_service_urls", "wecom_customer_service_url"):
                    raw = user_config.get(legacy_key)
                    if raw is None:
                        continue
                    try:
                        if isinstance(raw, dict):  # 拆域路由前的域映射残留：忽略。
                            continue
                        legacy_urls = _normalize_wecom_service_urls(raw)
                        break
                    except ValueError as exc:
                        logger.warning(f"Invalid legacy wecom URL ignored: {exc}")
                if legacy_urls:
                    self.wecom_customer_services = _customer_services_from_urls(legacy_urls)

        except Exception as e:
            logger.error(f"Failed to load config from {self._config_file}: {e}")

    def _handle_environment(self) -> None:
        self.sandbox_provider = (os.getenv("SANDBOX_PROVIDER") or self.sandbox_provider or "provisioner").strip()
        self.sandbox_provisioner_url = (
            os.getenv("SANDBOX_PROVISIONER_URL") or self.sandbox_provisioner_url or "http://sandbox-provisioner:8002"
        ).strip()
        self.sandbox_virtual_path_prefix = (
            os.getenv("SANDBOX_VIRTUAL_PATH_PREFIX") or self.sandbox_virtual_path_prefix or "/home/gem/user-data"
        ).strip()
        self.sandbox_exec_timeout_seconds = int(
            os.getenv("SANDBOX_EXEC_TIMEOUT_SECONDS") or self.sandbox_exec_timeout_seconds or 180
        )
        self.sandbox_max_output_bytes = int(
            os.getenv("SANDBOX_MAX_OUTPUT_BYTES") or self.sandbox_max_output_bytes or 262144
        )
        self.sandbox_keepalive_interval_seconds = int(
            os.getenv("SANDBOX_KEEPALIVE_INTERVAL_SECONDS") or self.sandbox_keepalive_interval_seconds or 30
        )

        # 企微客服入口：环境变量仅作首次启动默认（每个 URL 建一条条目）；
        # 管理界面保存过（base.toml 已持久化）后以此为准。
        if not self.wecom_customer_services:
            env_wecom = (os.getenv("WECOM_CUSTOMER_SERVICE_URL") or "").strip()
            if env_wecom:
                try:
                    self.wecom_customer_services = _customer_services_from_urls(
                        _normalize_wecom_service_urls(env_wecom)
                    )
                except ValueError as exc:
                    logger.warning(f"Invalid WECOM_CUSTOMER_SERVICE_URL ignored: {exc}")

        if self.sandbox_provider.lower() != "provisioner":
            raise ValueError("Only sandbox_provider=provisioner is supported.")
        if not self.sandbox_provisioner_url:
            raise ValueError("SANDBOX_PROVISIONER_URL is required when sandbox provider is provisioner.")
        if not self.sandbox_virtual_path_prefix.startswith("/"):
            self.sandbox_virtual_path_prefix = f"/{self.sandbox_virtual_path_prefix}"

    def start_runtime_sync(self, interval: float = runtime_cache.RUNTIME_CONFIG_SYNC_INTERVAL_SECONDS) -> None:
        """启动后台线程周期性从 Redis 同步运行时配置。多次调用仅启动一次。"""
        self._runtime_sync_thread = runtime_cache.start_runtime_sync(
            self,
            self._runtime_sync_thread,
            interval=interval,
        )

    def refresh(self) -> None:
        """从 Redis 快照刷新公开配置字段到内存；Redis 不可用或无快照时保持当前值。"""
        runtime_cache.refresh_runtime_config(self)

    def save(self) -> None:
        if not self._config_file:
            logger.warning("Config file path not set")
            return

        logger.info(f"Saving config to {self._config_file}")
        user_modified = {}
        for field_name, field_info in type(self).model_fields.items():
            if field_info.exclude:
                continue
            current_value = getattr(self, field_name)
            # default_factory 字段没有字面 default（PydanticUndefined），按工厂值比较，避免空容器被无谓落盘。
            default_value = field_info.default
            if default_value is PydanticUndefined and field_info.default_factory is not None:
                default_value = field_info.default_factory()
            if current_value != default_value:
                user_modified[field_name] = current_value

        try:
            with open(self._config_file, "wb") as f:
                tomli_w.dump(user_modified, f)
            logger.info(f"Config saved to {self._config_file}")
            runtime_cache.save_runtime_config(self)
        except Exception as e:
            logger.error(f"Failed to save config to {self._config_file}: {e}")

    def dump_config(self) -> dict[str, Any]:
        config_dict = self.model_dump()
        fields_info = {}
        for field_name, field_info in Config.model_fields.items():
            if field_info.exclude:
                continue
            fields_info[field_name] = {
                "des": field_info.description,
                # default_factory 字段没有字面 default（PydanticUndefined），落到 JSON 会触发
                # FastAPI `-> dict` 严格序列化报 PydanticSerializationError → 配置保存接口 500。
                "default": None if field_info.default is PydanticUndefined else field_info.default,
                "type": field_info.annotation.__name__
                if hasattr(field_info.annotation, "__name__")
                else str(field_info.annotation),
                "exclude": field_info.exclude if hasattr(field_info, "exclude") else False,
            }
        config_dict["_config_items"] = fields_info
        return config_dict

    def update(self, other: dict[str, Any]) -> None:
        # 批量应用：先全部写入，再统一做跨字段（业务线↔客服）校验；失败回滚内存快照并抛错，
        # 保证请求键顺序无关且失败后内存与落盘一致（save 由路由在 update 成功后调用）。
        touched = {key for key in other if self.can_update(key)}
        if touched:
            snapshot = {key: getattr(self, key) for key in touched}
            try:
                for key, value in other.items():
                    if self.can_update(key):
                        setattr(self, key, self._normalize_config_value(key, value))
                    elif key in READONLY_CONFIG_FIELDS:
                        logger.warning(f"Readonly config key ignored: {key}")
                    else:
                        logger.warning(f"Unknown config key: {key}")
                if "business_lines" in touched or "wecom_customer_services" in touched:
                    self._validate_customer_service_bindings()
            except ValueError:
                for key, value in snapshot.items():
                    setattr(self, key, value)
                raise

    def can_update(self, key: object) -> bool:
        return isinstance(key, str) and key in type(self).model_fields and key not in READONLY_CONFIG_FIELDS

    def set_value(self, key: str, value: Any) -> None:
        if not self.can_update(key):
            raise ValueError(f"配置项不可修改: {key}")
        # 单键保存同样要回滚：跨字段校验失败后内存必须与已持久化值一致，
        # 否则页面下次加载（或再保存一次）会带着脏绑定继续。
        previous = getattr(self, key)
        try:
            setattr(self, key, self._normalize_config_value(key, value))
            if key in {"business_lines", "wecom_customer_services"}:
                self._validate_customer_service_bindings()
        except ValueError:
            setattr(self, key, previous)
            raise

    def _validate_customer_service_bindings(self) -> None:
        """跨字段引用校验：业务线绑定的客服 id 必须存在于 wecom_customer_services。"""
        rows = self.business_lines or []
        services = self.wecom_customer_services or []
        known_ids = {
            str(entry["id"]) for entry in services if isinstance(entry, dict) and entry.get("id")
        }
        invalid: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            bound = row.get("customer_service_ids") or []
            unknown = [cid for cid in bound if cid not in known_ids]
            if unknown:
                invalid.append(f"{row.get('code')} → {', '.join(unknown)}")
        if invalid:
            raise ValueError("业务线绑定了不存在的客服: " + "; ".join(invalid))

    def _normalize_config_value(self, key: str, value: Any) -> Any:
        if key == "default_ocr_engine":
            return _normalize_default_ocr_engine(value)
        if key == "wecom_customer_services":
            return _normalize_wecom_customer_services(value)
        if key == "business_lines":
            return _normalize_business_lines(value)
        return value


config = Config()


def resolve_business_lines() -> list[BusinessLine]:
    """读取配置中的业务线（校验为 BusinessLine 列表）。仅被拒答分类/跑题门/域校验低频读取，逐行容错。"""
    rows = getattr(config, "business_lines", None) or []
    lines: list[BusinessLine] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            lines.append(BusinessLine.model_validate(row))
        except ValidationError:
            continue
    return lines


def resolve_customer_services() -> list[CustomerServiceEntry]:
    """读取配置中的企微客服条目（校验为 CustomerServiceEntry 列表），非法行容错跳过。"""
    rows = getattr(config, "wecom_customer_services", None) or []
    entries: list[CustomerServiceEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            entries.append(CustomerServiceEntry.model_validate(row))
        except ValidationError:
            continue
    return entries


def find_business_line(code: str) -> BusinessLine | None:
    """按 code 找业务线；unknown 或未配置返回 None。"""
    for line in resolve_business_lines():
        if line.code == code:
            return line
    return None


def known_business_domain_codes() -> frozenset[str]:
    """当前已配置的业务线 code 集合（不含保留值 unknown）。"""
    return frozenset(line.code for line in resolve_business_lines())


def sanitize_business_domain(value: str | None) -> str:
    """把 domain 归一为合法值：空/未配置 code/非清单值一律回退 unknown（保留兜底）。"""
    domain = str(value or "").strip() or "unknown"
    if domain == "unknown":
        return domain
    return domain if domain in known_business_domain_codes() else "unknown"


def resolve_embedding_model(spec: str | None = None) -> str:
    """知识库未显式指定向量模型时，跟随设置-基本设置的全局默认 embed_model。"""
    return spec or config.embed_model


def resolve_reranker_model(spec: str | None = None) -> str:
    """知识库未显式指定重排序模型时，跟随设置-基本设置的全局默认 reranker。"""
    return spec or config.reranker
