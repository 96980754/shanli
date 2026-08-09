"""RAGAS 内部评估的 ragas 兼容层。

ragas==0.4.3 在 ragas/llms/base.py 顶层硬导入
``langchain_community.chat_models.vertexai``，而 langchain-community>=0.4 已把
VertexAI 拆分为独立包并移除该模块。这里在 import ragas 之前注入一个惰性 stub，
使不需要 VertexAI 的指标路径（本系统全部指标）可以正常工作。

客户端镜像未安装 ragas，本模块只做无害的 sys.modules 注入，不影响产品功能。
"""

import sys
import types

_VERTEXAI_MODULE = "langchain_community.chat_models.vertexai"


class _VertexAIStub:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("VertexAI 未接入本系统，仅用于满足 ragas 的导入要求")


def _ensure_vertexai_stub() -> None:
    if _VERTEXAI_MODULE in sys.modules:
        return
    module = types.ModuleType(_VERTEXAI_MODULE)
    module.ChatVertexAI = _VertexAIStub
    sys.modules[_VERTEXAI_MODULE] = module


_ensure_vertexai_stub()
