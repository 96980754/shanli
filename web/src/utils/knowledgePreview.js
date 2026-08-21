export const normalizeKnowledgePreview = (response) => {
  const payload = response && typeof response === 'object' ? response : {}
  return {
    query: String(payload.query || ''),
    answer: typeof payload.answer === 'string' ? payload.answer : null,
    citations: Array.isArray(payload.citations) ? payload.citations : [],
    retrieved_chunks: Array.isArray(payload.retrieved_chunks) ? payload.retrieved_chunks : [],
    retrieval: {
      mode: String(payload.retrieval?.mode || 'unknown'),
      top_k: Number(payload.retrieval?.top_k || 0),
      rerank_enabled: Boolean(payload.retrieval?.rerank_enabled),
      rerank_applied: Boolean(payload.retrieval?.rerank_applied),
      graph_enabled: Boolean(payload.retrieval?.graph_enabled)
    },
    model_spec: String(payload.model_spec || '')
  }
}
