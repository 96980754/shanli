"""config.dump_config() 必须可被 FastAPI 严格 dict 序列化（回归）。

背景：default_factory 字段（business_lines / wecom_customer_services 等）没有字面 default，
`field_info.default` 是 PydanticUndefined；此前 dump_config() 把该值原样塞进 _config_items，
导致标注 `-> dict` 的配置保存路由（POST /api/system/config{,/update}）响应序列化时报
PydanticSerializationError → 500（配置其实已落盘，前端收不到响应，编辑态与库态脱钩）。
GET /api/system/config 因无返回注解走 jsonable_encoder（把 PydanticUndefined 归一为 None）而幸免。
"""

import json

from pydantic import TypeAdapter
from pydantic_core import PydanticUndefined

from yuxi.config.app import Config


def _factory_field_names() -> list[str]:
    """返回所有 default_factory 字段名——这些字段最容易在 dump 时泄漏 PydanticUndefined。

    按字段遍历而非硬编码名单：字段改名/新增/删除都能被这里自动覆盖，不再需要同步维护。
    """
    return [name for name, info in Config.model_fields.items() if info.default is PydanticUndefined]


def test_dump_config_factory_field_default_is_none(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    dumped = cfg.dump_config()
    names = _factory_field_names()
    assert names, "模型里应存在 default_factory 字段，否则本测试失去了回归意义"
    for name in names:
        item = dumped["_config_items"][name]
        assert item["default"] is None, f"{name}.default 不应泄漏 PydanticUndefined"
        assert item["default"] is not PydanticUndefined


def test_dump_config_is_json_serializable(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    json.dumps(cfg.dump_config())  # 不应抛 TypeError


def test_dump_config_survives_strict_dict_serialization(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    # 镜像 FastAPI 对 `-> dict` 路由的响应序列化路径（TypeAdapter(dict) → PydanticSerializationError 重灾区）。
    TypeAdapter(dict).dump_python(cfg.dump_config(), mode="json")
