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

// 编号只映射真实检索来源；同一 PDF 的多个 chunk 保持同一来源卡片。
const citedChunks = MessageProcessor.extractKnowledgeChunksFromConversation({
  messages: [
    {
      type: 'ai',
      tool_calls: [
        {
          name: 'query_kb',
          tool_call_result: {
            content: {
              schema_version: 1,
              status: 'ok',
              kb_id: 'kb-a',
              results: [
                {
                  id: 'c1',
                  kb_id: 'kb-a',
                  file_id: 'flow-pdf',
                  content: '角色',
                  metadata: { source: '流程.pdf', source_reference: 1 }
                },
                {
                  id: 'c2',
                  kb_id: 'kb-a',
                  file_id: 'flow-pdf',
                  content: '顺序',
                  metadata: { source: '流程.pdf', source_reference: 1 }
                },
                {
                  id: 'c3',
                  kb_id: 'kb-a',
                  file_id: 'ordinary-pdf',
                  content: '文档',
                  metadata: { source: '普通.pdf', source_reference: 2 }
                }
              ]
            }
          }
        }
      ]
    }
  ]
})
const cited = MessageProcessor.filterKnowledgeChunksByAnswer(citedChunks, '流程步骤。[1]')
assert.equal(cited.length, 2)
assert.deepEqual([...new Set(cited.map((chunk) => chunk.file_id))], ['flow-pdf'])
const citedGroups = MessageProcessor.groupKnowledgeChunksByDocument(cited)
assert.equal(citedGroups.length, 1)
assert.equal(citedGroups[0].sourceReference, 1)
assert.equal(citedGroups[0].file_id, 'flow-pdf')
assert.equal(citedGroups[0].displayName, '流程.pdf')
assert.equal(
  MessageProcessor.filterKnowledgeChunksByAnswer(citedChunks, '普通内容。[2]')[0].file_id,
  'ordinary-pdf'
)

console.log('knowledgeSourceOrdering: all assertions passed')
