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

  console.log('messageProcessor query_kb source extraction: all assertions passed')
}

run()
