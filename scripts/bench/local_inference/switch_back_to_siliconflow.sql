-- 回滚：模型路由切回 SiliconFlow（恢复远端模型，纯配置）
-- 用法: docker exec -i postgres psql -U postgres -d yuxi < switch_back_to_siliconflow.sql

-- 1) 6 个交付库：嵌入模型恢复远端 bge-m3
UPDATE knowledge_bases
SET embedding_model_spec = 'siliconflow-cn:Pro/BAAI/bge-m3'
WHERE kb_type = 'milvus'
  AND kb_id IN ('kb_3cm2gz6tyb','kb_mvng8u1201','kb_0368jjmecb','kb_fhhcq7kf8a','kb_2ncrgy5nr1','kb_abeqi4880k');

-- 2) 3 个资料库：rerank 恢复远端 bge-reranker-v2-m3
UPDATE knowledge_bases
SET query_params = jsonb_set(
        query_params,
        '{options,reranker_model}',
        '"siliconflow-cn:Pro/BAAI/bge-reranker-v2-m3"'
    )
WHERE kb_id IN ('kb_3cm2gz6tyb','kb_mvng8u1201','kb_0368jjmecb');

-- 3) 停用 local provider（不删除，保留记录）
UPDATE model_providers SET is_enabled = false WHERE provider_id = 'local';
