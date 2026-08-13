/**
 * 消息处理工具类
 */
export class MessageProcessor {
  /**
   * 将工具结果与消息合并
   * @param {Array} msgs - 消息数组
   * @returns {Array} 合并后的消息数组
   */
  static convertToolResultToMessages(msgs) {
    const toolResponseMap = new Map()

    // 构建工具响应映射
    for (const item of msgs) {
      if (item.type === 'tool') {
        // 使用多种可能的ID字段来匹配工具调用
        const toolCallId = item.tool_call_id || item.id
        if (toolCallId) {
          toolResponseMap.set(toolCallId, item)
        }
      }
    }

    // 合并工具调用和响应
    const convertedMsgs = msgs.map((item) => {
      if (item.type === 'ai' && item.tool_calls && item.tool_calls.length > 0) {
        return {
          ...item,
          tool_calls: item.tool_calls.map((toolCall) => {
            const toolResponse = toolResponseMap.get(toolCall.id)
            return {
              ...toolCall,
              tool_call_result: toolResponse || null
            }
          })
        }
      }
      return item
    })

    return convertedMsgs
  }

  /**
   * 将服务器历史记录转换为对话格式
   * @param {Array} serverHistory - 服务器历史记录
   * @returns {Array} 对话数组
   */
  static convertServerHistoryToMessages(serverHistory) {
    // Filter out standalone 'tool' messages since tool results are already in AI messages' tool_calls
    // Backend new storage: tool results are embedded in AI messages' tool_calls array with tool_call_result field
    const filteredHistory = serverHistory.filter(
      (item) =>
        item.type !== 'tool' &&
        !(item.type === 'human' && item.extra_metadata?.source === 'ask_user_question_resume')
    )

    // 按照对话分组
    const conversations = []
    let currentConv = null

    for (const item of filteredHistory) {
      if (item.type === 'human') {
        // Start new conversation, finalize previous one
        if (currentConv) {
          // Find the last AI message and mark it as final
          for (let i = currentConv.messages.length - 1; i >= 0; i--) {
            if (currentConv.messages[i].type === 'ai') {
              currentConv.messages[i].isLast = true
              currentConv.status = 'finished'
              break
            }
          }
        }
        currentConv = {
          messages: [item],
          status: 'loading'
        }
        conversations.push(currentConv)
      } else if (item.type === 'ai' && currentConv) {
        currentConv.messages.push(item)
      }
    }

    // Mark the last conversation as finished
    if (currentConv && currentConv.messages.length > 0) {
      // Find the last AI message and mark it as final
      for (let i = currentConv.messages.length - 1; i >= 0; i--) {
        if (currentConv.messages[i].type === 'ai') {
          currentConv.messages[i].isLast = true
          currentConv.status = 'finished'
          break
        }
      }
    }

    return conversations
  }

  /**
   * 提取一轮对话中所有知识库检索块
   * @param {Object} conv - 单轮对话
   * @param {Array} databases - 知识库列表
   * @returns {Array} 归一化后的检索块
   */
  static extractKnowledgeChunksFromConversation(conv, databases = []) {
    if (!conv || !Array.isArray(conv.messages) || conv.messages.length === 0) return []

    const databaseNames = new Map(
      (databases || [])
        .filter((db) => db?.kb_id)
        .map((db) => [db.kb_id, db.name || db.kb_id])
    )
    const normalizedChunks = []
    const dedupSet = new Set()

    const appendChunk = (chunk, outputKbId) => {
      if (!chunk || typeof chunk !== 'object') return
      const content = typeof chunk.content === 'string' ? chunk.content.trim() : ''
      if (!content) return

      const metadata = chunk.metadata && typeof chunk.metadata === 'object' ? chunk.metadata : {}
      const kbId = chunk.kb_id || outputKbId || ''
      const fileId = chunk.file_id || metadata.file_id || ''
      if (!kbId) return

      const chunkId = metadata.chunk_id || chunk.id || ''
      const dedupKey = chunkId
        ? `${kbId}::${chunkId}`
        : `${kbId}::${fileId}::${content}`
      if (dedupSet.has(dedupKey)) return
      dedupSet.add(dedupKey)

      const score =
        typeof chunk.score === 'number'
          ? chunk.score
          : typeof metadata.score === 'number'
            ? metadata.score
            : null
      normalizedChunks.push({
        kb_id: kbId,
        file_id: fileId,
        kb_name: databaseNames.get(kbId) || kbId,
        content,
        score,
        metadata: {
          ...metadata,
          source: metadata.source || '',
          file_id: fileId,
          chunk_id: chunkId,
          chunk_index: metadata.chunk_index
        }
      })
    }

    const parseToolResultContent = (content) => {
      if (Array.isArray(content)) return content
      if (content && typeof content === 'object') return content
      if (typeof content === 'string') {
        try {
          return JSON.parse(content)
        } catch {
          return null
        }
      }
      return null
    }

    for (const msg of conv.messages) {
      if (!msg || msg.type !== 'ai' || !Array.isArray(msg.tool_calls)) continue

      for (const toolCall of msg.tool_calls) {
        const toolName = toolCall?.name || toolCall?.function?.name
        // query_kb 与 query_kbs 都返回同样的 schema_v1 检索结果，均为知识来源数据
        if (toolName !== 'query_kb' && toolName !== 'query_kbs') continue

        const content = toolCall?.tool_call_result?.content
        const parsed = parseToolResultContent(content)
        if (
          !parsed ||
          parsed.schema_version !== 1 ||
          parsed.status !== 'ok' ||
          !Array.isArray(parsed.results)
        ) {
          continue
        }

        for (const chunk of parsed.results) appendChunk(chunk, parsed.kb_id)
      }
    }

    normalizedChunks.sort((a, b) => {
      const scoreA = typeof a.score === 'number' ? a.score : Number.NEGATIVE_INFINITY
      const scoreB = typeof b.score === 'number' ? b.score : Number.NEGATIVE_INFINITY
      return scoreB - scoreA
    })

    return normalizedChunks
  }

  /**
   * 判断一轮对话是否发生过知识检索（query_kb/query_kbs）。
   * 与 extractKnowledgeChunksFromConversation 共享相同的工具识别规则，
   * 但不要求召回结果可用——召回不足/检索失败时仍视为发生过检索，
   * 前端据此保留来源入口，避免「答了但来源不显示」。
   */
  static hasKnowledgeRetrieval(conv) {
    if (!conv || !Array.isArray(conv.messages)) return false
    for (const msg of conv.messages) {
      if (!msg || msg.type !== 'ai' || !Array.isArray(msg.tool_calls)) continue
      for (const toolCall of msg.tool_calls) {
        const toolName = toolCall?.name || toolCall?.function?.name
        if (toolName === 'query_kb' || toolName === 'query_kbs') return true
      }
    }
    return false
  }

  /**
   * 依据 AI 回答正文中实际引用的文档名，过滤知识库检索块。
   *
   * query_kbs 一次并行检索多个库，结果会带上大量与最终回答无关的候选文件；
   * 而模型的「依据来源」区段只列出真正引用的文档。这里提取该区段的引用名
   * （《》包裹名 + 表格「来源文档」列首格），与每个检索块的文件名做
   * 「归一化编辑距离」匹配，只保留被引用的文件，并按引用顺序 + 相关度排序。
   * 提取不到引用名或匹配不到任何文件时，回退为全量，避免误删。
   *
   * @param {Array} chunks - 归一化后的知识库检索块
   * @param {string} answerText - AI 回答正文
   * @returns {Array} 过滤并排序后的检索块
   */
  static filterKnowledgeChunksByAnswer(chunks, answerText) {
    if (!Array.isArray(chunks) || chunks.length === 0) return chunks
    const text = typeof answerText === 'string' ? answerText : ''
    if (!text) return chunks

    const citationNames = MessageProcessor.extractCitationNames(text)
    if (citationNames.length === 0) return chunks
    const citationCores = citationNames.map((n) => MessageProcessor.normalizeDocName(n)).filter(Boolean)
    if (citationCores.length === 0) return chunks

    // 引用在正文中出现的位置，用于让面板顺序跟随回答的引用顺序
    const citationPositions = citationNames.map((n) => {
      const pos = text.indexOf(n)
      return pos === -1 ? Number.MAX_SAFE_INTEGER : pos
    })

    const matched = chunks
      .map((chunk) => {
        const metadata = chunk.metadata && typeof chunk.metadata === 'object' ? chunk.metadata : {}
        const source = metadata.source || ''
        const basename = source.split(/[\\/]/).filter(Boolean).pop() || ''
        const core = MessageProcessor.normalizeDocName(basename)
        if (!core) return null

        let bestSim = 0
        let bestIdx = -1
        for (let i = 0; i < citationCores.length; i++) {
          const c = citationCores[i]
          const maxLen = Math.max(core.length, c.length)
          if (!maxLen) continue
          const sim = 1 - MessageProcessor.levenshtein(core, c) / maxLen
          if (sim > bestSim) {
            bestSim = sim
            bestIdx = i
          }
        }
        if (bestSim < 0.8 || bestIdx === -1) return null
        return {
          chunk,
          score: typeof chunk.score === 'number' ? chunk.score : Number.NEGATIVE_INFINITY,
          position: citationPositions[bestIdx]
        }
      })
      .filter(Boolean)

    // 匹配不到任何被引用文件时回退全量，避免误删
    if (matched.length === 0) return chunks

    matched.sort((a, b) => a.position - b.position || b.score - a.score)
    return matched.map((m) => m.chunk)
  }

  /**
   * 从回答正文提取引用文档名：《》包裹的名字 + 表格「来源文档」列首格。
   * @param {string} text - AI 回答正文
   * @returns {Array} 引用文档名列表
   */
  static extractCitationNames(text) {
    const names = []
    const angleRe = /《([^》]+)》/g
    let match
    while ((match = angleRe.exec(text))) names.push(match[1])

    // 表格行：首格含文档特征词才视为引用行（跳过表头与分隔行）
    const docKwRe =
      /规格书|datasheet|说明书|解决方案|白皮书|部署指南|指南|手册|工卡|一纸禅|协议|介绍|报告|规格|证书|认证|单页|资料|白皮/i
    for (const line of text.split('\n')) {
      const trimmed = line.trim()
      if (!(trimmed.startsWith('|') && trimmed.endsWith('|'))) continue
      const cells = trimmed
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map((c) => c.trim())
      if (cells.length >= 2 && docKwRe.test(cells[0])) names.push(cells[0])
    }

    const seen = new Set()
    const out = []
    for (const name of names) {
      const core = MessageProcessor.normalizeDocName(name)
      if (core && core.length >= 4 && !seen.has(core)) {
        seen.add(core)
        out.push(name)
      }
    }
    return out
  }

  /**
   * 文件名/引用名归一化：去括号注释 → 小写去空白标点 → 去扩展名 → 去尾部版本/日期。
   * 例：'Miniserver M200规格书（更新日期 2025-11-25）.xlsx' → 'miniserverm200规格书'
   * @param {string} str - 原始文件名或引用名
   * @returns {string} 归一化后的核心串
   */
  static normalizeDocName(str) {
    if (!str) return ''
    let s = String(str)
      .replace(/[（(][^（）()]*[）)]/g, '')
      .toLowerCase()
      .replace(/[\s\p{P}\p{S}_]+/gu, '')
    s = s.replace(/(pdf|docx?|xlsx?|pptx?)$/, '')
    s = s.replace(/(?:[v]\d+(?:\.\d+)*|\d{4,8})?$/, '')
    return s
  }

  /**
   * Levenshtein 编辑距离（用于近似匹配模型改写过的文档名，如 Datasheet/Datesheet 拼写差异）。
   * @param {string} a - 字符串 A
   * @param {string} b - 字符串 B
   * @returns {number} 编辑距离
   */
  static levenshtein(a, b) {
    const la = a.length
    const lb = b.length
    if (la === 0) return lb
    if (lb === 0) return la
    const dp = new Array(lb + 1)
    for (let j = 0; j <= lb; j++) dp[j] = j
    for (let i = 1; i <= la; i++) {
      let prev = dp[0]
      dp[0] = i
      for (let j = 1; j <= lb; j++) {
        const tmp = dp[j]
        dp[j] = Math.min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1))
        prev = tmp
      }
    }
    return dp[lb]
  }

  /**
   * 提取一轮对话中的网络搜索来源
   * @param {Object} conv - 单轮对话
   * @returns {Array} 归一化后的网络来源
   */
  static extractWebSourcesFromConversation(conv) {
    if (!conv || !Array.isArray(conv.messages) || conv.messages.length === 0) return []

    const webSources = []
    const dedupSet = new Set()

    const parseToolResultContent = (content) => {
      if (Array.isArray(content)) return content
      if (content && typeof content === 'object') return content
      if (typeof content === 'string') {
        try {
          return JSON.parse(content)
        } catch {
          return null
        }
      }
      return null
    }

    for (const msg of conv.messages) {
      if (!msg || msg.type !== 'ai' || !Array.isArray(msg.tool_calls)) continue

      for (const toolCall of msg.tool_calls) {
        const toolName = (toolCall?.name || toolCall?.function?.name || '').toLowerCase()
        if (!toolName.includes('tavily_search')) continue

        const content = toolCall?.tool_call_result?.content
        const parsed = parseToolResultContent(content)
        const results = Array.isArray(parsed?.results) ? parsed.results : []
        if (results.length === 0) continue

        for (const item of results) {
          const title = typeof item?.title === 'string' ? item.title.trim() : ''
          const url = typeof item?.url === 'string' ? item.url.trim() : ''
          if (!title || !url) continue
          if (dedupSet.has(url)) continue
          dedupSet.add(url)

          webSources.push({
            tool_name: toolCall?.name || toolCall?.function?.name || '网络搜索',
            title,
            url,
            score: typeof item?.score === 'number' ? item.score : null,
            content: typeof item?.content === 'string' ? item.content : '',
            published_date: typeof item?.published_date === 'string' ? item.published_date : ''
          })
        }
      }
    }

    webSources.sort((a, b) => {
      const scoreA = typeof a.score === 'number' ? a.score : Number.NEGATIVE_INFINITY
      const scoreB = typeof b.score === 'number' ? b.score : Number.NEGATIVE_INFINITY
      return scoreB - scoreA
    })

    return webSources
  }

  /**
   * 提取单个消息中的来源
   * @param {Object} message - 消息对象
   * @param {Array} databases - 知识库列表
   * @returns {{knowledgeChunks: Array, webSources: Array}}
   */
  static extractSourcesFromMessage(message, databases = []) {
    if (!message || message.type !== 'ai') return { knowledgeChunks: [], webSources: [] }

    // 复用提取逻辑，通过构建临时对话对象
    const mockConv = { messages: [message] }
    return {
      knowledgeChunks: MessageProcessor.extractKnowledgeChunksFromConversation(mockConv, databases),
      webSources: MessageProcessor.extractWebSourcesFromConversation(mockConv)
    }
  }

  /**
   * 提取一轮对话中的全部来源（知识库+网络搜索）
   * @param {Object} conv - 单轮对话
   * @param {Array} databases - 知识库列表
   * @returns {{knowledgeChunks: Array, webSources: Array}}
   */
  static extractSourcesFromConversation(conv, databases = []) {
    return {
      knowledgeChunks: MessageProcessor.extractKnowledgeChunksFromConversation(conv, databases),
      webSources: MessageProcessor.extractWebSourcesFromConversation(conv)
    }
  }

  /**
   * 解析助手消息正文与推理内容，保持渲染和列表拆分使用同一套规则。
   * @param {Object} message - AI 消息对象
   * @returns {{content: string, reasoningContent: string}}
   */
  static parseAssistantMessageBody(message) {
    let content = typeof message?.content === 'string' ? message.content.trim() : ''
    let reasoningContent = message?.additional_kwargs?.reasoning_content || ''

    if (!reasoningContent && content) {
      const thinkRegex = /<think>(.*?)<\/think>|<think>(.*?)$/s
      const thinkMatch = content.match(thinkRegex)

      if (thinkMatch) {
        reasoningContent = (thinkMatch[1] || thinkMatch[2] || '').trim()
        content = content.replace(thinkMatch[0], '').trim()
      }
    }

    return { content, reasoningContent }
  }

  /**
   * 合并消息块
   * @param {Array} chunks - 消息块数组
   * @returns {Object|null} 合并后的消息
   */
  static mergeMessageChunk(chunks) {
    if (chunks.length === 0) return null

    // 深拷贝第一个chunk作为结果
    const result = JSON.parse(JSON.stringify(chunks[0]))

    // 处理用户消息的内容格式 - 确保显示纯文本
    if (result.type === 'human' || result.role === 'user') {
      // 如果content是数组格式（LangChain多模态消息），提取文本部分
      if (Array.isArray(result.content)) {
        const textPart = result.content.find((item) => item.type === 'text')
        result.content = textPart ? textPart.text : ''
      } else {
        result.content = result.content || ''
      }
    } else {
      result.content = result.content || ''
    }

    // 合并后续chunks
    for (let i = 1; i < chunks.length; i++) {
      const chunk = chunks[i]

      // 合并内容
      if (chunk.content) {
        result.content += chunk.content
      }

      // 合并reasoning_content
      if (chunk.reasoning_content) {
        if (!result.reasoning_content) {
          result.reasoning_content = ''
        }
        result.reasoning_content += chunk.reasoning_content
      }

      // 合并additional_kwargs中的reasoning_content
      if (chunk.additional_kwargs?.reasoning_content) {
        if (!result.additional_kwargs) result.additional_kwargs = {}
        if (!result.additional_kwargs.reasoning_content) {
          result.additional_kwargs.reasoning_content = ''
        }
        result.additional_kwargs.reasoning_content += chunk.additional_kwargs.reasoning_content
      }

      // 合并tool_calls (处理新的数据结构)
      MessageProcessor._mergeToolCalls(result, chunk)
    }

    // 处理AIMessageChunk类型
    if (result.type === 'AIMessageChunk') {
      result.type = 'ai'
    }

    return result
  }

  /**
   * 合并工具调用
   * @private
   * @param {Object} result - 结果对象
   * @param {Object} chunk - 当前块
   */
  static _mergeToolCalls(result, chunk) {
    if (chunk.tool_call_chunks && chunk.tool_call_chunks.length > 0) {
      // 确保 result 有 tool_calls 数组
      if (!result.tool_calls) result.tool_calls = []

      for (const toolCallChunk of chunk.tool_call_chunks) {
        // 使用 index 来标识工具调用（因为可能有多个工具调用）
        const existingToolCallIndex = result.tool_calls.findIndex(
          (t) => t.index === toolCallChunk.index
        )

        if (existingToolCallIndex !== -1) {
          // 合并相同index的tool call
          const existingToolCall = result.tool_calls[existingToolCallIndex]

          // 更新名称和ID（如果存在）
          if (toolCallChunk.name && !existingToolCall.function?.name) {
            if (!existingToolCall.function) existingToolCall.function = {}
            existingToolCall.function.name = toolCallChunk.name
          }

          if (toolCallChunk.id && !existingToolCall.id) {
            existingToolCall.id = toolCallChunk.id
          }

          // 合并参数
          if (toolCallChunk.args) {
            if (!existingToolCall.function) existingToolCall.function = {}
            if (!existingToolCall.function.arguments) existingToolCall.function.arguments = ''
            existingToolCall.function.arguments += toolCallChunk.args
          }
        } else {
          // 添加新的tool call
          const newToolCall = {
            index: toolCallChunk.index,
            id: toolCallChunk.id,
            function: {
              name: toolCallChunk.name || null,
              arguments: toolCallChunk.args || ''
            }
          }
          result.tool_calls.push(newToolCall)
        }
      }
    }
  }
}

export default MessageProcessor
