import assert from 'node:assert/strict'

import { MessageProcessor } from '../messageProcessor.js'

// 来源面板顺序 = 相关性顺序：组间按组内最高分降序，组内按分数降序
const grouped = MessageProcessor.groupKnowledgeChunksByDocument([
  {
    kb_id: 'kb-a',
    file_id: 'file-low',
    content: '低分文档正文',
    score: 0.21,
    metadata: { source: '低相关文档.pdf', chunk_id: 'c1' }
  },
  {
    kb_id: 'kb-a',
    file_id: 'file-high',
    content: '高分文档第一段',
    score: 0.62,
    metadata: { source: '高相关文档.pdf', chunk_id: 'c2' }
  },
  {
    kb_id: 'kb-b',
    file_id: 'file-high',
    content: '高分文档第二段',
    score: 0.93,
    metadata: { source: '另一个库/高相关文档.pdf', chunk_id: 'c3' }
  }
])

assert.deepEqual(
  grouped.map((group) => group.displayName),
  ['高相关文档.pdf', '低相关文档.pdf']
)
assert.deepEqual(
  grouped[0].chunks.map((chunk) => chunk.content),
  ['高分文档第二段', '高分文档第一段']
)
assert.equal(grouped.length, 2) // 同名文档跨库仍合并为一组

// 无分数的片段（open_kb_document 等）沉底且保持原相对序
const withUnscored = MessageProcessor.groupKnowledgeChunksByDocument([
  {
    kb_id: 'kb-a',
    file_id: 'file-opened',
    content: '打开文档第一段',
    metadata: { source: '被打开的文档.pdf', chunk_id: 'c1' }
  },
  {
    kb_id: 'kb-a',
    file_id: 'file-opened',
    content: '打开文档第二段',
    metadata: { source: '被打开的文档.pdf', chunk_id: 'c2' }
  },
  {
    kb_id: 'kb-a',
    file_id: 'file-scored',
    content: '检索命中正文',
    score: 0.4,
    metadata: { source: '检索命中文档.pdf', chunk_id: 'c3' }
  }
])

assert.deepEqual(
  withUnscored.map((group) => group.displayName),
  ['检索命中文档.pdf', '被打开的文档.pdf']
)
assert.deepEqual(
  withUnscored[1].chunks.map((chunk) => chunk.content),
  ['打开文档第一段', '打开文档第二段']
)

console.log('knowledgeSourceOrdering: all assertions passed')
