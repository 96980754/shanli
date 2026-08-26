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
  assert.equal(
    dedupChunks.some((chunk) => chunk.kb_id === 'kb-other'),
    true
  )

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
  assert.deepEqual(
    MessageProcessor.extractKnowledgeChunksFromConversation(ignoredConv, databases),
    []
  )

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

  const industryConv = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'research_industry_products',
            tool_call_result: {
              content: JSON.stringify({
                products: [
                  {
                    product: '产品A',
                    evidence: [
                      {
                        content: '产品A证据',
                        source_reference: 1,
                        source: {
                          title: '共同手册.docx',
                          kb_id: 'kb-finance',
                          file_id: 'file-a',
                          chunk_id: 'shared-chunk'
                        }
                      }
                    ]
                  },
                  {
                    product: '产品B',
                    evidence: [
                      {
                        content: '产品B证据',
                        source_reference: 2,
                        source: {
                          title: '共同手册.docx',
                          kb_id: 'kb-finance',
                          file_id: 'file-a',
                          chunk_id: 'shared-chunk'
                        }
                      }
                    ]
                  }
                ]
              })
            }
          }
        ]
      }
    ]
  }
  const industryChunks = MessageProcessor.extractKnowledgeChunksFromConversation(
    industryConv,
    databases
  )
  assert.equal(industryChunks.length, 2)
  assert.deepEqual(
    industryChunks.map((chunk) => chunk.metadata.product),
    ['产品A', '产品B']
  )
  assert.deepEqual(
    industryChunks.map((chunk) => ({
      kb_id: chunk.kb_id,
      file_id: chunk.file_id,
      source: chunk.metadata.source,
      url: chunk.metadata.url
    })),
    [
      {
        kb_id: 'kb-finance',
        file_id: 'file-a',
        source: '共同手册.docx',
        url: undefined
      },
      {
        kb_id: 'kb-finance',
        file_id: 'file-a',
        source: '共同手册.docx',
        url: undefined
      }
    ]
  )
  assert.equal(MessageProcessor.hasKnowledgeRetrieval(industryConv), true)
  assert.equal(MessageProcessor.hasArtifactPresentation(industryConv), false)
  industryConv.messages[0].tool_calls.push({ name: 'present_artifacts' })
  assert.equal(MessageProcessor.hasArtifactPresentation(industryConv), true)

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
  assert.deepEqual(sourceCited.map((c) => c.metadata.source).sort(), [
    'C10单页-中文-1.pdf',
    'POCSTARS 定位产品解决方案介绍.pdf',
    '面向关键任务的群组通信（MCX）技术白皮书.pdf'
  ])

  // 句号后的补充说明不应被当作引用；「来源说明」分组名（如"定位资料"）作为候选被保留，
  // 匹配不到任何文件时自然忽略
  const citationNames = MessageProcessor.extractCitationNames(sourceAnswer)
  assert.equal(
    citationNames.some((n) => n.includes('如需进一步')),
    false
  )
  assert.equal(citationNames.includes('定位资料'), true)

  // 无任何引用时回退全量，不误删
  const noCiteAnswer = '这是一段不引用任何文档的普通回答。'
  assert.equal(
    MessageProcessor.filterKnowledgeChunksByAnswer(citationChunks, noCiteAnswer).length,
    4
  )
  assert.equal(MessageProcessor.filterKnowledgeChunksByAnswer(citationChunks, '').length, 4)

  // 有「来源说明」但匹配不到任何被引用文件时回退全量
  const noMatchChunks = [
    {
      kb_id: 'kb',
      file_id: 'f-a',
      content: 'x',
      metadata: { source: '完全无关文档.pdf', chunk_id: 'c-a' }
    }
  ]
  assert.equal(
    MessageProcessor.filterKnowledgeChunksByAnswer(noMatchChunks, sourceAnswer).length,
    1
  )

  // 回归：模型以「──来源：」+ 完整路径列表（poc资料/MNO/xxx.pptx（kb_id））引用文档时，
  // 来源面板也应只保留被引用的文件，而不是回退全量
  const pathCitedAnswer =
    '…正文内容…\n\n──来源：\n' +
    'poc资料/MNO/POCSTARS MNO产品解决方案-V1.2-202605.pptx（kb_3cm2gz6tyb）\n' +
    'poc资料/MNO/【修订中】POCSTARS MNO产品白皮书V1.1.docx（kb_3cm2gz6tyb）\n' +
    'poc资料/MNO/POCSTARS MNO全平台功能清单V1.0-20260522.xlsx（kb_3cm2gz6tyb）'
  const pathChunks = [
    {
      kb_id: 'kb_3cm2gz6tyb',
      file_id: 'p1',
      content: 'c1',
      metadata: { source: 'POCSTARS MNO产品解决方案-V1.2-202605.pptx', chunk_id: 'cp1' }
    },
    {
      kb_id: 'kb_3cm2gz6tyb',
      file_id: 'p2',
      content: 'c2',
      metadata: { source: '【修订中】POCSTARS MNO产品白皮书V1.1.docx', chunk_id: 'cp2' }
    },
    {
      kb_id: 'kb_3cm2gz6tyb',
      file_id: 'p3',
      content: 'c3',
      metadata: { source: 'POCSTARS MNO全平台功能清单V1.0-20260522.xlsx', chunk_id: 'cp3' }
    },
    {
      kb_id: 'kb_3cm2gz6tyb',
      file_id: 'p4',
      content: 'c4',
      metadata: { source: '无关营销资料.pdf', chunk_id: 'cp4' }
    }
  ]
  const pathFiltered = MessageProcessor.filterKnowledgeChunksByAnswer(pathChunks, pathCitedAnswer)
  assert.deepEqual(pathFiltered.map((c) => c.file_id).sort(), ['p1', 'p2', 'p3'])

  // 回归：真实模型输出常以「**来源**：」+「- 」无序列表列出完整路径（markdown 加粗把
  // 「来源」与冒号隔开，旧正则只能匹配紧邻冒号），来源面板同样应只保留被引用的文件
  const boldCitedAnswer =
    '…正文内容…\n\n**来源**：\n' +
    '- poc资料/MNO/POCSTARS MNO产品解决方案-V1.2-202605.pptx（kb_3cm2gz6tyb）\n' +
    '- poc资料/MNO/【修订中】POCSTARS MNO产品白皮书V1.1.docx（kb_3cm2gz6tyb）\n' +
    '- poc资料/MNO/POCSTARS MNO全平台功能清单V1.0-20260522.xlsx（kb_3cm2gz6tyb）'
  const boldFiltered = MessageProcessor.filterKnowledgeChunksByAnswer(pathChunks, boldCitedAnswer)
  assert.deepEqual(boldFiltered.map((c) => c.file_id).sort(), ['p1', 'p2', 'p3'])

  // 回归（conv 714）：模型答完在「**来源**：」列表后用空行另起提问句
  //（如「需要我进一步检索该机型的认证证书信息（如 CE、RoHS、IEC62133 等）吗？」），
  // 提问句不是引用，不应被当成文档名；否则 citation core「rohs」会子串命中文件名含 RoHS 的
  // 证书文件（对讲机 RoHS2.0证书.pdf），来源面板只显示无关证书、覆盖掉真正的依据文档
  // 与真实输出（conv 714 msg 7680）同形：路径用反引号包裹 + 空行后接提问句
  const trailingQuestionAnswer =
    '**来源：**\n' +
    '- `poc资料/主推机型规格和彩页/主推终端产品规格书20260118.xlsx`（E600 条目）\n' +
    '- `poc资料/主推机型规格和彩页/产品彩页和规格/E600 Brochure.pdf`\n\n' +
    '需要我进一步检索该机型的认证证书信息（如 CE、RoHS、IEC62133 等）吗？'
  const trailingCites = MessageProcessor.extractCitationNames(trailingQuestionAnswer)
  assert.equal(trailingCites.some((n) => /RoHS|IEC62133|认证证书/.test(n)), false)
  assert.equal(
    trailingCites.includes('poc资料/主推机型规格和彩页/主推终端产品规格书20260118.xlsx'),
    true
  )
  assert.equal(
    trailingCites.includes('poc资料/主推机型规格和彩页/产品彩页和规格/E600 Brochure.pdf'),
    true
  )
  // 端到端：混入 RoHS 证书 chunk 时，面板只保留真正被引用的 E600 规格书 + 彩页
  const rohsCertChunks = [
    {
      kb_id: 'kb',
      file_id: 'r1',
      content: 'c',
      metadata: { source: 'UNIB24061148HC-01 对讲机 RoHS2.0证书.pdf', chunk_id: 'cr1' }
    },
    {
      kb_id: 'kb',
      file_id: 'r2',
      content: 'c',
      metadata: { source: 'UNIB24061148HR-01 对讲机 RoHS2.0英文报告.pdf', chunk_id: 'cr2' }
    },
    {
      kb_id: 'kb',
      file_id: 'e1',
      content: '规格',
      metadata: { source: '主推终端产品规格书20260118.xlsx', chunk_id: 'ce1' }
    },
    {
      kb_id: 'kb',
      file_id: 'e2',
      content: '彩页',
      metadata: { source: 'E600 Brochure.pdf', chunk_id: 'ce2' }
    }
  ]
  const trailingFiltered = MessageProcessor.filterKnowledgeChunksByAnswer(
    rohsCertChunks,
    trailingQuestionAnswer
  )
  assert.deepEqual(trailingFiltered.map((c) => c.file_id).sort(), ['e1', 'e2'])

  // ---- 来源面板回归：find_kb_document / search_file 定位结果纳入来源 ----
  // 对话 a297b81d 场景：query_kbs 召回为空，但模型通过 find_kb_document 定位到文件，
  // 这些「定位到的文档」也应进入来源面板（此前只认 query_kb/query_kbs，来源面板为空）。
  const m200Source = 'poc资料/miniserver/Miniserver M200规格书20251125.xlsx'
  const locateConv = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'search_file',
            tool_call_result: {
              content: JSON.stringify({
                files: [
                  {
                    kb_id: 'kb-m200',
                    kb_name: 'poc-资料',
                    file_id: 'file-m200',
                    filename: m200Source,
                    file_type: 'xlsx'
                  }
                ],
                total: 1
              })
            }
          },
          {
            name: 'find_kb_document',
            tool_call_result: {
              content: JSON.stringify({
                kb_id: 'kb-m200',
                file_id: 'file-m200',
                semantic: false,
                match_mode: 'keyword',
                total_matches: 2,
                windows: [
                  {
                    start_line: 1,
                    end_line: 4,
                    matched_lines: [2],
                    content: '1: Miniserver M200\n2: 规格\n'
                  },
                  {
                    start_line: 10,
                    end_line: 12,
                    matched_lines: [11],
                    content: '10: 处理器\n11: 内存\n'
                  }
                ]
              })
            }
          }
        ]
      }
    ]
  }
  const locateChunks = MessageProcessor.extractKnowledgeChunksFromConversation(locateConv, [])
  assert.equal(locateChunks.length, 3) // 1 个定位文件卡片 + 2 个定位窗口
  // find_kb_document 窗口：来源名由同轮 search_file 结果解析，而非回退 file_id
  const findWindows = locateChunks.filter((c) => c.content.startsWith('1: Miniserver'))
  assert.equal(findWindows.length, 1)
  assert.equal(findWindows[0].metadata.source, m200Source)
  assert.equal(findWindows[0].kb_name, 'poc-资料')
  // search_file 定位文件：以完整路径文件名作为来源卡片内容
  const locatedCard = locateChunks.find((c) => c.content === m200Source)
  assert.equal(locatedCard.metadata.source, m200Source)
  assert.equal(locatedCard.file_id, 'file-m200')
  // 面板按 metadata.source 分组，定位卡片与窗口应同组（来源名一致）
  assert.equal(new Set(locateChunks.map((c) => c.metadata.source)).size, 1)

  // find_kb_document 无 search_file 兜底：来源名回退 file_id，仍不吞掉已定位内容
  const fallbackConv = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'find_kb_document',
            tool_call_result: {
              content: JSON.stringify({
                kb_id: 'kb-fb',
                file_id: 'file-fb',
                match_mode: 'keyword',
                total_matches: 1,
                windows: [{ start_line: 1, end_line: 1, matched_lines: [1], content: '1: 内容' }]
              })
            }
          }
        ]
      }
    ]
  }
  const fallbackChunks = MessageProcessor.extractKnowledgeChunksFromConversation(fallbackConv, [])
  assert.equal(fallbackChunks.length, 1)
  assert.equal(fallbackChunks[0].metadata.source, 'file-fb')

  // hasKnowledgeRetrieval：仅 find_kb_document/search_file（无 query_kb）也算发生过检索，
  // 前端据此保留来源按钮，避免「定位到了文档但不显示来源」
  assert.equal(MessageProcessor.hasKnowledgeRetrieval(locateConv), true)
  assert.equal(MessageProcessor.hasKnowledgeRetrieval(fallbackConv), true)

  // ---- 来源面板回归：open_kb_document 按行窗口读文档原文，也应纳入来源 ----
  // conv 714 场景：query_kbs 召回未命中，模型通过 open_kb_document 逐窗口读取规格书/彩页
  // 原文作答，前端此前不收集该工具，导致真正依据的文档不出现在来源面板；
  // 后端已随窗口返回 source（文件显示名），来源名优先取 source，缺失时回退同轮 search_file 解析
  const openWindow = {
    kb_id: 'kb-poc',
    file_id: 'file-a473a2',
    start_line: 1,
    end_line: 40,
    total_lines: 120,
    offset: 0,
    window_size: 40,
    has_more_before: false,
    has_more_after: true,
    next_offset: 40,
    content: '1: E600 产品介绍\n2: 参数表\n'
  }
  // 后端返回 source：来源名直接用文件显示名，无需同轮 search_file 兜底
  const openSourceConv = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'open_kb_document',
            tool_call_result: {
              content: JSON.stringify({ ...openWindow, source: 'E600 Brochure.pdf' })
            }
          }
        ]
      }
    ]
  }
  const openSourceChunks = MessageProcessor.extractKnowledgeChunksFromConversation(openSourceConv, [])
  assert.equal(openSourceChunks.length, 1)
  assert.equal(openSourceChunks[0].metadata.source, 'E600 Brochure.pdf')
  assert.equal(openSourceChunks[0].kb_id, 'kb-poc')
  assert.equal(openSourceChunks[0].file_id, 'file-a473a2')
  // open_kb_document 与 find_kb_document 同级，视为发生过检索，来源按钮保留
  assert.equal(MessageProcessor.hasKnowledgeRetrieval(openSourceConv), true)

  // 后端未返回 source：由同轮 search_file 结果解析文件名，缺失时回退 file_id
  const openFallbackConv = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'search_file',
            tool_call_result: {
              content: JSON.stringify({
                files: [
                  {
                    kb_id: 'kb-poc',
                    kb_name: 'poc-资料',
                    file_id: 'file-a473a2',
                    filename: 'poc资料/主推机型规格和彩页/产品彩页和规格/E600 Brochure.pdf',
                    file_type: 'pdf'
                  }
                ],
                total: 1
              })
            }
          },
          {
            name: 'open_kb_document',
            tool_call_result: { content: JSON.stringify({ ...openWindow, source: '' }) }
          }
        ]
      }
    ]
  }
  const openFallbackChunks = MessageProcessor.extractKnowledgeChunksFromConversation(
    openFallbackConv,
    []
  )
  // 1 个 search_file 定位卡片 + 1 个读取窗口
  assert.equal(openFallbackChunks.length, 2)
  const openWin = openFallbackChunks.find((c) => c.content.startsWith('1: E600'))
  assert.equal(openWin.metadata.source, 'poc资料/主推机型规格和彩页/产品彩页和规格/E600 Brochure.pdf')
  assert.equal(openWin.kb_name, 'poc-资料')

  // 同一文档跨多库命中（bug1）：来源面板分组与「来源 N」计数按文档名去重，避免重复卡片
  const duplicateDocChunks = [
    {
      kb_id: 'kb-yingxiao',
      file_id: 'file-fb4fa6',
      content: '话术正文',
      metadata: { source: 'POCSTARS定位产品介绍话术-海外版.docx', chunk_id: 'c1' }
    },
    {
      kb_id: 'kb-yingxiao',
      file_id: 'file-fb4fa6',
      content: '话术正文2',
      metadata: { source: 'POCSTARS定位产品介绍话术-海外版.docx', chunk_id: 'c2' }
    },
    {
      kb_id: 'kb-dingwei',
      file_id: 'file-e7711e',
      content: '话术正文3',
      metadata: { source: '定位资料/POCSTARS定位产品介绍话术-海外版.docx', chunk_id: 'c3' }
    },
    {
      kb_id: 'kb-dingwei',
      file_id: 'file-1846ce',
      content: '客群画像正文',
      metadata: {
        source: '定位资料/POCSTARS 产品目标客群画像、应用场景销售话术.pdf',
        chunk_id: 'c4'
      }
    }
  ]
  const grouped = MessageProcessor.groupKnowledgeChunksByDocument(duplicateDocChunks)
  assert.equal(grouped.length, 2) // 同名文档跨库合并为 1 组，共 2 个文档
  assert.equal(grouped[0].chunks.length + grouped[1].chunks.length, 4) // 全部 chunk 保留
  assert.equal(new Set(grouped.map((g) => g.displayName)).size, 2)
  // 「来源 N」计数直接取分组长度，与卡片数一致
  assert.equal(grouped.length, 2)

  console.log('messageProcessor query_kb source extraction: all assertions passed')
}

run()
