from __future__ import annotations

from typing import Any


class MergeEngine:
    """Deterministic merge rules for KG bootstrap stage."""

    @staticmethod
    def merge_scalar_field(values: list[dict[str, Any]]) -> dict[str, Any]:
        unique: dict[str, dict[str, Any]] = {}
        for item in values:
            key = str(item.get("数值", item.get("值")))
            existing = unique.get(key)
            if existing:
                existing["来源图片"].extend(item.get("来源图片", []))
            else:
                unique[key] = {
                    "值": item.get("数值", item.get("值")),
                    "来源图片": list(item.get("来源图片", [])),
                    "冲突": False,
                }

        if len(unique) > 1:
            merged_values = list(unique.values())
            for merged in merged_values:
                merged["冲突"] = True
            return {"冲突": True, "各值": merged_values}

        if not unique:
            return {"冲突": False, "值": None, "来源图片": []}

        merged = next(iter(unique.values()))
        return {
            "冲突": False,
            "值": merged["值"],
            "来源图片": merged["来源图片"],
        }

    @staticmethod
    def merge_list_field(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for item in items:
            value = str(item.get("值", ""))
            if value in seen:
                seen[value]["来源图片"].extend(item.get("来源图片", []))
            else:
                seen[value] = {
                    "值": value,
                    "来源图片": list(item.get("来源图片", [])),
                }
        return list(seen.values())

    @staticmethod
    def detect_identity_conflict(records: list[dict[str, Any]]) -> dict[str, Any]:
        values = []
        images: list[Any] = []
        seen_values = set()
        for record in records:
            value = record.get("值")
            if value not in seen_values:
                seen_values.add(value)
                values.append({"值": value})
            images.extend(record.get("来源图片", []))

        if len(values) > 1:
            return {
                "冲突": True,
                "各值": values,
                "涉及图片": images,
            }

        return {
            "冲突": False,
            "值": values[0]["值"] if values else None,
        }
