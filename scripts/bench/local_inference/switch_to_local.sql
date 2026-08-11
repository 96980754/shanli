-- 本地推理配置切换：模型路由指向 local-inference 容器（纯配置，可随时切回）
-- 用法: docker exec -i postgres psql -U postgres -d yuxi < switch_to_local.sql

-- 1) 新增 local provider（OpenAI 兼容，指向本地 GPU 推理容器）
INSERT INTO model_providers (
    provider_id, display_name, provider_type, base_url,
    embedding_base_url, rerank_base_url, api_key_env,
    capabilities, enabled_models, is_enabled, is_builtin
) VALUES (
    'local', 'Local Inference (GPU)', 'openai',
    'http://local-inference:8000/v1',
    'http://local-inference:8000/v1/embeddings',
    'http://local-inference:8000/v1/rerank',
    'LOCAL_INFERENCE_KEY',
    '["embedding", "rerank"]',
    '[
        {"id": "BAAI/bge-m3", "type": "embedding", "display_name": "BAAI/bge-m3", "dimension": 1024, "batch_size": 40, "source": "local", "extra": {}},
        {"id": "BAAI/bge-reranker-v2-m3", "type": "rerank", "display_name": "BAAI/bge-reranker-v2-m3", "source": "local", "extra": {}}
    ]',
    true, false
)
ON CONFLICT (provider_id) DO NOTHING;

-- 2) 6 个交付库：嵌入模型指向本地 bge-m3
UPDATE knowledge_bases
SET embedding_model_spec = 'local:BAAI/bge-m3'
WHERE kb_type = 'milvus'
  AND kb_id IN ('kb_3cm2gz6tyb','kb_mvng8u1201','kb_0368jjmecb','kb_fhhcq7kf8a','kb_2ncrgy5nr1','kb_abeqi4880k');

-- 3) 3 个资料库：rerank 指向本地 bge-reranker-v2-m3（保持 hybrid + graph + rerank）
UPDATE knowledge_bases
SET query_params = jsonb_set(
        query_params,
        '{options,reranker_model}',
        '"local:BAAI/bge-reranker-v2-m3"'
    )
WHERE kb_id IN ('kb_3cm2gz6tyb','kb_mvng8u1201','kb_0368jjmecb');
