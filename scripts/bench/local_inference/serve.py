"""本地 GPU 推理服务：bge-m3 嵌入 + bge-reranker-v2-m3 精排。

暴露 OpenAI 兼容接口，与现有 OtherEmbedding / OpenAIReranker 无缝对接：
  POST /v1/embeddings  -> {"data": [{"index": i, "embedding": [...]}]}
  POST /v1/rerank      -> {"results": [{"index": i, "relevance_score": s}]}

模型默认从 hf-mirror.com 下载（HF_ENDPOINT 环境变量可覆盖）。
"""
import os

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import asyncio
import functools
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, Request
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

app = FastAPI(title="Local Inference")

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
EMBED_BATCH = int(os.getenv("EMBED_BATCH", "16"))
RERANK_BATCH = int(os.getenv("RERANK_BATCH", "32"))
# GPU 推理放进线程池（事件循环不阻塞）+ 全局 GPU 锁（6GB 显存，并发推理会争抢导致数十秒抖动）
_pool = ThreadPoolExecutor(max_workers=4)
_gpu_lock = threading.Lock()

device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if device == "cuda" else torch.float32
print(f"[init] device={device} dtype={dtype}", flush=True)

t0 = time.perf_counter()
_embed_tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL)
_embed_model = AutoModel.from_pretrained(
    EMBED_MODEL, torch_dtype=dtype, use_safetensors=True
).to(device).eval()
_rerank_tokenizer = AutoTokenizer.from_pretrained(RERANK_MODEL)
_rerank_model = AutoModelForSequenceClassification.from_pretrained(
    RERANK_MODEL, torch_dtype=dtype, use_safetensors=True
).to(device).eval()
print(f"[init] models loaded in {time.perf_counter() - t0:.1f}s", flush=True)


def _embed(texts: list[str]) -> np.ndarray:
    vecs = []
    with _gpu_lock, torch.no_grad():
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i : i + EMBED_BATCH]
            enc = _embed_tokenizer(
                batch, padding=True, truncation=True, max_length=8192, return_tensors="pt"
            ).to(device)
            # bge-m3 官方 dense 向量取 CLS 并归一化
            cls = _embed_model(**enc)[0][:, 0]
            vecs.append(torch.nn.functional.normalize(cls, p=2, dim=1).cpu().float().numpy())
    return np.concatenate(vecs)


def _rerank_sync(query: str, docs: list[str], max_len: int) -> list[float]:
    scores: list[float] = []
    with _gpu_lock, torch.no_grad():
        for i in range(0, len(docs), RERANK_BATCH):
            batch = docs[i : i + RERANK_BATCH]
            enc = _rerank_tokenizer(
                [(query, d) for d in batch],
                padding=True,
                truncation=True,
                max_length=max_len,
                return_tensors="pt",
            ).to(device)
            logits = _rerank_model(**enc).logits[:, 0].float().cpu().tolist()
            scores.extend(logits)
    return scores


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    body = await req.json()
    inp = body["input"]
    single = isinstance(inp, str)
    texts = [inp] if single else list(inp)
    t = time.perf_counter()
    vecs = await asyncio.get_running_loop().run_in_executor(_pool, functools.partial(_embed, texts))
    latency = (time.perf_counter() - t) * 1000
    data = [
        {"object": "embedding", "index": i, "embedding": vecs[i].astype(float).tolist()}
        for i in range(len(texts))
    ]
    print(f"[bench] /v1/embeddings n={len(texts)} {latency:.0f}ms", flush=True)
    return {"object": "list", "data": data, "model": body.get("model", "")}


@app.post("/v1/rerank")
async def rerank(req: Request):
    body = await req.json()
    query = body["query"]
    docs = list(body["documents"])
    max_len = int(body.get("max_chunks_per_doc", 512))
    t = time.perf_counter()
    scores = await asyncio.get_running_loop().run_in_executor(
        _pool, functools.partial(_rerank_sync, query, docs, max_len)
    )
    latency = (time.perf_counter() - t) * 1000
    results = [{"index": i, "relevance_score": float(s)} for i, s in enumerate(scores)]
    print(f"[bench] /v1/rerank query={query[:20]!r} n={len(docs)} {max_len=} {latency:.0f}ms", flush=True)
    return {"results": results, "model": body.get("model", "")}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
