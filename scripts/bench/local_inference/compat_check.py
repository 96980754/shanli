"""校验本地 bge-m3 与 SiliconFlow bge-m3 向量兼容性（同一文本 cosine 相似度）。

在 api-dev 容器内运行（env 里已有 SILICONFLOW_API_KEY，脚本不回显 key）：
  python -u /tmp/compat_check.py
"""
import asyncio
import os

import httpx
import numpy as np

LOCAL_URL = "http://local-inference:8000/v1/embeddings"
REMOTE_URL = "https://api.siliconflow.cn/v1/embeddings"
REMOTE_KEY = os.getenv("SILICONFLOW_API_KEY", "")
QUERY = "F10定位对讲一体机的技术规格和防护等级"
DOC = "F10 是一款防爆定位对讲一体机，支持公网对讲与北斗/GPS 定位。"


async def get_vec(url: str, key: str | None, text: str) -> list[float]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx.AsyncClient() as c:
        r = await c.post(
            url, json={"model": "BAAI/bge-m3", "input": text}, headers=headers, timeout=60
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]


async def main():
    for text, name in [(QUERY, "query"), (DOC, "doc")]:
        local = await get_vec(LOCAL_URL, None, text)
        remote = await get_vec(REMOTE_URL, REMOTE_KEY, text)
        a, b = np.array(local), np.array(remote)
        cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        dim_local, dim_remote = len(a), len(b)
        print(f"{name}: dim local={dim_local} remote={dim_remote} cosine={cos:.4f}", flush=True)


asyncio.run(main())
