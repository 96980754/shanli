import assert from 'node:assert/strict'

import { normalizeKnowledgePreview } from '../knowledgePreview.js'

const preview = normalizeKnowledgePreview({
  query: '星河终端最大并发用户数是多少？',
  answer: '最大并发用户数为 200。',
  citations: [{ id: 'chunk-v2', file_id: 'file-v2' }],
  retrieved_chunks: [{ id: 'chunk-v2', score: 0.95 }],
  retrieval: {
    mode: 'hybrid',
    top_k: 1,
    rerank_enabled: true,
    rerank_applied: true
  },
  model_spec: 'provider:model'
})

assert.equal(preview.answer, '最大并发用户数为 200。')
assert.deepEqual(
  preview.citations.map((item) => item.id),
  ['chunk-v2']
)
assert.deepEqual(
  preview.retrieved_chunks.map((item) => item.id),
  ['chunk-v2']
)
assert.equal(preview.retrieval.mode, 'hybrid')
assert.equal(preview.retrieval.rerank_applied, true)

const empty = normalizeKnowledgePreview({ answer: null })
assert.equal(empty.answer, null)
assert.deepEqual(empty.citations, [])
assert.deepEqual(empty.retrieved_chunks, [])
assert.equal(empty.retrieval.mode, 'unknown')

console.log('knowledgePreview tests passed')
