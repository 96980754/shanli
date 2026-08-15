import assert from 'node:assert/strict'

import { MessageProcessor } from '../messageProcessor.js'

const databases = [
  { kb_id: 'kb-finance', name: '财税库' },
  { kb_id: 'kb-other', name: '其他知识库' }
]

const queryOutput = (overrides = {}) => ({
  schema_version: 1,
  status: 'ok',
  reason: null,
  kb_id: 'kb-finance',
  results: [
    {
      id: 'c1',
      kb_id: 'kb-finance',
      file_id: 'f1',
      content: 'A',
      score: 0.9,
      metadata: { source: 'doc-a.pdf', chunk_id: 'c1', chunk_index: 1 }
    }
  ],
  error: null,
  ...overrides
})

const run = () => {
  const realtimeMessages = MessageProcessor.convertToolResultToMessages([
    {
      type: 'ai',
      tool_calls: [{ id: 'call-1', name: 'query_kb', args: { kb_id: 'kb-finance' } }]
    },
    {
      type: 'tool',
      tool_call_id: 'call-1',
      content: JSON.stringify(queryOutput())
    }
  ])
  const realtimeChunks = MessageProcessor.extractKnowledgeChunksFromConversation(
    { messages: realtimeMessages },
    databases
  )

  assert.equal(realtimeChunks.length, 1)
  assert.deepEqual(
    {
      kb_id: realtimeChunks[0].kb_id,
      file_id: realtimeChunks[0].file_id,
      kb_name: realtimeChunks[0].kb_name,
      source: realtimeChunks[0].metadata.source
    },
    { kb_id: 'kb-finance', file_id: 'f1', kb_name: '财税库', source: 'doc-a.pdf' }
  )

  // 单次工具调用耗时（duration_ms）随 tool_call_result 透传，供前端标记「本次检索耗时」
  const durationMessages = MessageProcessor.convertToolResultToMessages([
    {
      type: 'ai',
      tool_calls: [{ id: 'call-dur', name: 'query_kb', args: { kb_id: 'kb-finance' } }]
    },
    {
      type: 'tool',
      tool_call_id: 'call-dur',
      content: JSON.stringify(queryOutput()),
      duration_ms: 1234
    }
  ])
  const durationToolCall = durationMessages.find((msg) => msg.type === 'ai').tool_calls[0]
  assert.equal(durationToolCall.tool_call_result.duration_ms, 1234)
  assert.equal(durationToolCall.tool_call_result.content.includes('kb-finance'), true)

  const historyConv = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            function: { name: 'query_kb' },
            tool_call_result: {
              content: queryOutput({
                results: [
                  {
                    id: 'c2',
                    content: 'B',
                    metadata: {
                      source: 'doc-b.docx',
                      file_id: 'f2',
                      chunk_id: 'c2',
                      chunk_index: 2,
                      score: 0.4
                    }
                  }
                ]
              })
            }
          }
        ]
      }
    ]
  }
  const historyChunks = MessageProcessor.extractKnowledgeChunksFromConversation(historyConv, [])

  assert.equal(historyChunks.length, 1)
  assert.equal(historyChunks[0].kb_id, 'kb-finance')
  assert.equal(historyChunks[0].file_id, 'f2')
  assert.equal(historyChunks[0].kb_name, 'kb-finance')
  assert.equal(historyChunks[0].score, 0.4)

  const dedupConv = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'query_kb',
            tool_call_result: {
              content: JSON.stringify(
                queryOutput({
                  results: [
                    queryOutput().results[0],
                    { ...queryOutput().results[0], score: 0.8 },
                    {
                      ...queryOutput().results[0],
                      kb_id: 'kb-other',
                      file_id: 'f-other',
                      content: 'C'
                    }
                  ]
                })
              )
            }
          }
        ]
      }
    ]
  }
  const dedupChunks = MessageProcessor.extractKnowledgeChunksFromConversation(dedupConv, databases)

  assert.equal(dedupChunks.length, 2)
  assert.equal(dedupChunks[0].score, 0.9)
  assert.equal(dedupChunks.some((chunk) => chunk.kb_id === 'kb-other'), true)

  const ignoredConv = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'query_kb',
            tool_call_result: {
              content: JSON.stringify(
                queryOutput({ status: 'insufficient', reason: 'no_results', results: [] })
              )
            }
          },
          {
            name: 'other_tool',
            tool_call_result: { content: JSON.stringify(queryOutput()) }
          }
        ]
      }
    ]
  }
  assert.deepEqual(MessageProcessor.extractKnowledgeChunksFromConversation(ignoredConv, databases), [])

  // query_kbs 批量检索：多库结果合并，每条自带来源 kb_id，应与 query_kb 一样进入来源列表
  const batchConv = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'query_kbs',
            tool_call_result: {
              content: JSON.stringify(
                queryOutput({
                  kb_id: 'kb-finance,kb-other',
                  results: [
                    queryOutput().results[0],
                    {
                      ...queryOutput().results[0],
                      kb_id: 'kb-other',
                      file_id: 'f-other',
                      id: 'c-other',
                      content: 'D',
                      metadata: {
                        source: 'doc-other.pdf',
                        file_id: 'f-other',
                        chunk_id: 'c-other',
                        chunk_index: 1
                      }
                    }
                  ]
                })
              )
            }
          }
        ]
      }
    ]
  }
  const batchChunks = MessageProcessor.extractKnowledgeChunksFromConversation(batchConv, databases)

  assert.equal(batchChunks.length, 2)
  assert.equal(
    batchChunks.some((chunk) => chunk.kb_id === 'kb-finance' && chunk.kb_name === '财税库'),
    true
  )
  assert.equal(
    batchChunks.some((chunk) => chunk.kb_id === 'kb-other' && chunk.kb_name === '其他知识库'),
    true
  )

  const conversations = MessageProcessor.convertServerHistoryToMessages([
    { type: 'human', content: '请选择语言' },
    { type: 'ai', content: '请选择输出语言' },
    {
      type: 'human',
      content: '{"language":"python"}',
      extra_metadata: { source: 'ask_user_question_resume' }
    },
    { type: 'ai', content: '这是 Python 版本' }
  ])

  assert.equal(conversations.length, 1)
  assert.equal(conversations[0].messages.length, 3)
  assert.equal(conversations[0].messages.at(-1).content, '这是 Python 版本')
  assert.equal(conversations[0].messages.at(-1).isLast, true)
  assert.equal(conversations[0].status, 'finished')

  const assistantBody = MessageProcessor.parseAssistantMessageBody({
    type: 'ai',
    content: '<think>推理过程</think>最终答案'
  })
  assert.deepEqual(assistantBody, { content: '最终答案', reasoningContent: '推理过程' })

  // ---- 来源面板回归：filterKnowledgeChunksByAnswer / extractCitationNames ----
  // 回归场景：回答以《》/表格 + 「来源说明」段落列出实际使用的文档。
  // 旧实现只解析《》与表格，忽略「来源说明」，导致来源面板折叠（只显示《》恰好命中的 1 个文件）。
  const citedFiles = [
    'POCSTARS 定位产品解决方案介绍.pdf',
    'C10单页-中文-1.pdf',
    '面向关键任务的群组通信（MCX）技术白皮书.pdf',
    '无关营销资料.pdf'
  ]
  const citationChunks = citedFiles.map((source, i) => ({
    kb_id: 'kb',
    file_id: `f${i}`,
    content: `chunk-${i}`,
    score: 1 - i / 10,
    metadata: { source, chunk_id: `c${i}` }
  }))
  const sourceAnswer =
    '…正文引用《面向关键任务的群组通信（MCX）技术白皮书》…\n\n' +
    '**来源说明**：以上内容依据知识库中以下材料整理——' +
    '定位资料（POCSTARS 定位产品解决方案介绍、C10单页）、' +
    'MCX-资料（MCSTARS 产品白皮书及销售一纸禅）。\n\n' +
    '如需进一步输出成投标方案文档或按具体客户规模细化配置清单，请告知。'

  // 「来源说明」中被点名且能匹配到文件的文档应进入来源面板；未点名的文件不应混入；
  // 同时保留《》命中的 MCX 白皮书（回归前唯一能命中的来源）
  const sourceCited = MessageProcessor.filterKnowledgeChunksByAnswer(citationChunks, sourceAnswer)
  assert.deepEqual(
    sourceCited.map((c) => c.metadata.source).sort(),
    [
      'C10单页-中文-1.pdf',
      'POCSTARS 定位产品解决方案介绍.pdf',
      '面向关键任务的群组通信（MCX）技术白皮书.pdf'
    ]
  )

  // 句号后的补充说明不应被当作引用；「来源说明」分组名（如"定位资料"）作为候选被保留，
  // 匹配不到任何文件时自然忽略
  const citationNames = MessageProcessor.extractCitationNames(sourceAnswer)
  assert.equal(citationNames.some((n) => n.includes('如需进一步')), false)
  assert.equal(citationNames.includes('定位资料'), true)

  // 无任何引用时回退全量，不误删
  const noCiteAnswer = '这是一段不引用任何文档的普通回答。'
  assert.equal(MessageProcessor.filterKnowledgeChunksByAnswer(citationChunks, noCiteAnswer).length, 4)
  assert.equal(MessageProcessor.filterKnowledgeChunksByAnswer(citationChunks, '').length, 4)

  // 有「来源说明」但匹配不到任何被引用文件时回退全量
  const noMatchChunks = [
    { kb_id: 'kb', file_id: 'f-a', content: 'x', metadata: { source: '完全无关文档.pdf', chunk_id: 'c-a' } }
  ]
  assert.equal(MessageProcessor.filterKnowledgeChunksByAnswer(noMatchChunks, sourceAnswer).length, 1)

  console.log('messageProcessor query_kb source extraction: all assertions passed')
}

run()
