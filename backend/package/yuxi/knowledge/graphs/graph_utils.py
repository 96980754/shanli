"""图谱构建相关的纯函数工具集。

将数据变换逻辑从 MilvusGraphService 中抽离，
使 service 类专注于 I/O 和业务编排。
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from yuxi.utils import hashstr


def normalize_entity_name(text: str) -> str:
    """统一实体名称：去首尾空白、小写化、压缩内部连续空白。"""
    return " ".join(text.strip().lower().split())


# 强归一化：剥离尾部描述/型号后缀（如 "F10定位对讲一体机"→"F10"、"RTK定位"→"RTK"）。
# 形态：前缀是字母/数字/型号 token，后缀是中文或常见描述词。
_STRONG_SUFFIX_RE = re.compile(
    r"^(?P<base>[A-Za-z0-9][A-Za-z0-9\-_/ .]*?)"
    r"(?P<suffix>[定位服务平台系统终端设备对讲机一体机融合定位高功率系列]+)$"
)


def normalize_entity_name_strong(text: str) -> str:
    """强归一化实体名：NFKC + 去括号 + 去符号 + 剥离尾部描述后缀。

    用于跨 chunk 实体合并——把 "IP68"/"IP-68"、"RTK"/"RTK定位"、
    "F10定位对讲一体机"/"F10" 归一到可比较的 canonical key。
    """
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    # 去括号内容
    normalized = re.sub(r"\([^)]*\)|（[^）]*）", "", normalized).strip()
    # 剥离尾部描述后缀（仅当 base 部分是字母/数字开头的型号类）
    match = _STRONG_SUFFIX_RE.match(normalized)
    if match and len(match.group("base")) >= 2:
        normalized = match.group("base").strip()
    # 去符号、压缩
    normalized = re.sub(r"[\s\-_/]+", "", normalized).lower()
    return normalized


# 型号 token：字母/数字组合（如 F10、S700、POCSTARS-MNO）视为具体型号
_MODEL_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*[-_/]?\d+|\d+[A-Za-z]+[A-Za-z0-9]*|^[A-Za-z0-9]+-\d+")


def _looks_like_model(text: str) -> bool:
    """判断实体名是否含具体型号 token（如 F10、S700、POCSTARS-MNO）。"""
    return bool(_MODEL_TOKEN_RE.search(text))


def merge_generic_into_unique_model(names: list[str]) -> dict[str, str]:
    """把"无型号泛称"归并到同一组里唯一的"具体型号"。

    例如 Product 组同时含 "对讲机"、"定位对讲机"、"F10定位对讲一体机"，
    且 F10 是唯一型号 → 把 "对讲机"/"定位对讲机" 归并到 "F10定位对讲一体机"。
    若组内有多个型号，则不做泛称归并（避免误并到错误型号）。
    """
    models = [name for name in names if _looks_like_model(name)]
    if len(models) != 1:
        return {}
    canonical = models[0]
    result: dict[str, str] = {}
    for name in names:
        if name != canonical and not _looks_like_model(name):
            result[name] = canonical
    return result


def merge_entity_names(names: list[str], *, threshold: float = 0.72) -> dict[str, str]:
    """按强归一化 + 相似度把近义实体名聚为同一 canonical，返回 {原text: canonical}。

    canonical 选择策略（确定性）：
    1. 强归一化后完全相同的归为一组，取**最短原 text** 作为 canonical；
    2. 组内按 SequenceMatcher 阈值再合并强归一化仍不同但高度相似的。
    3. 最终 canonical 取组内最短原名（避免不稳定）。
    """
    strong_map: dict[str, list[str]] = {}
    for name in names:
        key = normalize_entity_name_strong(name) or name
        strong_map.setdefault(key, []).append(name)

    # 按强归一化 key 分组（已合并 IP68/IP-68、RTK/RTK定位 等）
    groups: list[list[str]] = [list(orig_names) for orig_names in strong_map.values()]

    # 相似度二次合并：组间 SequenceMatcher 阈值
    merged: list[list[str]] = []
    for group in groups:
        merged.append(list(group))
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            if i >= len(merged):
                break
            for j in range(i + 1, len(merged)):
                if j >= len(merged):
                    break
                g1_key = normalize_entity_name_strong(merged[i][0])
                g2_key = normalize_entity_name_strong(merged[j][0])
                if not g1_key or not g2_key:
                    continue
                ratio = SequenceMatcher(None, g1_key, g2_key).ratio()
                if ratio >= threshold:
                    merged[i] = merged[i] + merged[j]
                    del merged[j]
                    changed = True
                    break
            if changed:
                break

    result: dict[str, str] = {}
    for group in merged:
        canonical = min(group, key=lambda n: (len(n), n))
        for name in group:
            result[name] = canonical
    return result


def normalize_evidence_text(text: str) -> str:
    """归一化文本用于证据定位比较：NFKC、删除 NBSP、剥离加粗标记、去除表格分隔符、压缩连续空白。

    抽取与校验两端必须使用同一归一化规则，保证 start/end 与文本切片一致性。
    docx 表格解析出的 NBSP 是不可见填充字符、`|` 是 markdown 单元格分隔符，
    LLM 引用原文时通常不会保留它们，因此这里直接删除，让"标配：\\xa01200mAh"
    能匹配"标配：1200mAh"、"| **AI双麦降噪** | ... |"能匹配"AI双麦降噪 ..."。
    """
    # 先删 NBSP 再 NFKC：NFKC 会把 \xa0 折叠成普通空格，后删就删不掉了
    normalized = str(text or "").replace("\xa0", "")
    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = re.sub(r"\*{2,}|\|", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def locate_evidence_quote(source_text: str, quote: str) -> tuple[int, int] | None:
    """在 source_text 中定位 quote，返回 (start, end)；先精确匹配，失败后归一化匹配。

    归一化匹配返回的位置基于归一化后的文本；调用方需对源文本做同样归一化后再切片比较，
    因此证据校验（_evidence_snapshot）与抽取端（_enrich_evidence）必须复用本函数。
    """
    if not quote:
        return None
    start = source_text.find(quote)
    if start >= 0:
        return start, start + len(quote)
    normalized_source = normalize_evidence_text(source_text)
    normalized_quote = normalize_evidence_text(quote)
    start = normalized_source.find(normalized_quote)
    if start < 0:
        return None
    return start, start + len(normalized_quote)


def compute_entity_id(kb_id: str, normalized_name: str, label: str) -> str:
    return hashstr(f"{kb_id}:{normalized_name}:{label}", length=32)


def compute_triple_id(
    kb_id: str,
    source_normalized_name: str,
    source_label: str,
    relation_type: str,
    target_normalized_name: str,
    target_label: str,
) -> str:
    return hashstr(
        f"{kb_id}:{source_normalized_name}:{source_label}:{relation_type}:{target_normalized_name}:{target_label}",
        length=32,
    )


def graph_entity_collection_name(kb_id: str) -> str:
    return f"{kb_id}_entity"


def graph_triple_collection_name(kb_id: str) -> str:
    return f"{kb_id}_triple"


def build_graph_payload(normalized_result: dict[str, Any]) -> dict[str, Any]:
    """将抽取器产出的标准化结果转换为 Neo4j 写入所需的图结构。

    返回的 entities 已完成去重合并：同名同 label 的实体只保留一份，
    属性（attributes）取并集。
    """
    entities: list[dict[str, Any]] = []
    entity_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    def add_entity(entity: dict[str, Any]) -> str:
        key = (normalize_entity_name(entity["text"]), entity.get("label") or "Entity")
        existing = entity_by_key.get(key)
        if existing is not None:
            known_attributes = {(attr["text"], attr["label"]) for attr in existing.get("attributes") or []}
            for attribute in entity.get("attributes") or []:
                attribute_key = (attribute["text"], attribute["label"])
                if attribute_key not in known_attributes:
                    existing.setdefault("attributes", []).append(attribute)
                    known_attributes.add(attribute_key)
            return existing["id"]

        graph_entity = {
            "id": f"e{len(entities) + 1}",
            "text": entity["text"],
            "label": entity.get("label") or "Entity",
            "attributes": list(entity.get("attributes") or []),
        }
        entities.append(graph_entity)
        entity_by_key[key] = graph_entity
        return graph_entity["id"]

    relations = []
    for relation in normalized_result["relations"]:
        if relation.get("polarity", "positive") != "positive" or relation.get("assertion_kind", "fact") != "fact":
            continue
        relations.append(
            {
                "source": add_entity(relation["source"]),
                "target": add_entity(relation["target"]),
                "text": relation["text"],
                "label": relation.get("label") or "RELATED_TO",
            }
        )

    return {"entities": entities, "relations": relations, "metadata": normalized_result["metadata"]}


# ─── Cypher 模板 ────────────────────────────────────────────────
# 将大段 Cypher 字符串集中管理，提升 write_chunk_graph 的可读性。


def cypher_merge_chunk(db_label: str) -> str:
    """MERGE Chunk 节点并写入元数据。"""
    return f"""
    MERGE (c:Chunk:MilvusKB:`{db_label}` {{chunk_id: $chunk_id}})
    SET c.file_id = $file_id,
        c.kb_id = $kb_id,
        c.chunk_index = $chunk_index,
        c.content_preview = $content_preview,
        c.start_char_pos = $start_char_pos,
        c.end_char_pos = $end_char_pos
    """


def cypher_merge_entity_mention(db_label: str) -> str:
    """MERGE Entity 节点并创建 Chunk → Entity 的 MENTIONS 关系。"""
    return f"""
    MATCH (c:Chunk:MilvusKB:`{db_label}` {{chunk_id: $chunk_id}})
    MERGE (e:Entity:MilvusKB:`{db_label}` {{
        kb_id: $kb_id,
        normalized_name: $normalized_name,
        label: $entity_label
    }})
    SET e.entity_id = $entity_id,
        e.name = $name,
        e.attributes = $attributes
    MERGE (c)-[m:MENTIONS {{chunk_id: $chunk_id, file_id: $file_id, kb_id: $kb_id}}]->(e)
    """


def cypher_merge_relation(db_label: str) -> str:
    """MERGE 两个 Entity 之间的 RELATION 边。"""
    return f"""
    MATCH (source:Entity:MilvusKB:`{db_label}` {{
        kb_id: $kb_id,
        normalized_name: $source_name,
        label: $source_label
    }})
    MATCH (target:Entity:MilvusKB:`{db_label}` {{
        kb_id: $kb_id,
        normalized_name: $target_name,
        label: $target_label
    }})
    MERGE (source)-[r:RELATION {{
        kb_id: $kb_id,
        chunk_id: $chunk_id,
        source_name: $source_name,
        target_name: $target_name,
        type: $relation_type
    }}]->(target)
    SET r.triple_id = $triple_id,
        r.text = $text,
        r.file_id = $file_id,
        r.extractor_type = $extractor_type
    """
