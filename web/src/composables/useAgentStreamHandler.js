import { message } from 'ant-design-vue'
import { handleChatError } from '@/utils/errorHandler'
import { unref } from 'vue'
import { extractPendingInterrupt } from '@/composables/useApproval'

const serializeToolArgs = (args) => {
  if (typeof args === 'string') return args
  if (args === undefined || args === null) return ''
  return JSON.stringify(args)
}

const streamEventToMessageChunk = (streamEvent) => {
  if (!streamEvent || typeof streamEvent !== 'object') return null
  const messageId = streamEvent.message_id
  if (!messageId) return null

  if (streamEvent.type === 'message_delta') {
    const chunk = {
      id: messageId,
      type: 'AIMessageChunk',
      content: streamEvent.content || ''
    }
    if (streamEvent.reasoning_content) {
      chunk.reasoning_content = streamEvent.reasoning_content
    }
    if (streamEvent.additional_reasoning_content) {
      chunk.additional_kwargs = { reasoning_content: streamEvent.additional_reasoning_content }
    }
    if (streamEvent.handoff_available) {
      chunk.extra_metadata = {
        handoff_available: true,
        ...(streamEvent.handoff_query ? { handoff_query: streamEvent.handoff_query } : {})
      }
    }
    return chunk
  }

  if (streamEvent.type === 'tool_call' || streamEvent.type === 'tool_call_delta') {
    return {
      id: messageId,
      type: 'AIMessageChunk',
      content: '',
      tool_call_chunks: [
        {
          index: streamEvent.index || 0,
          id: streamEvent.tool_call_id,
          name: streamEvent.name,
          args:
            streamEvent.type === 'tool_call_delta'
              ? streamEvent.args_delta || ''
              : serializeToolArgs(streamEvent.args)
        }
      ]
    }
  }

  return null
}

const loadingMessageChunk = (chunk) => {
  const semanticChunk = streamEventToMessageChunk(chunk?.stream_event)
  if (semanticChunk) return semanticChunk

  const msg = chunk?.msg
  if (msg?.event) return null
  return msg || null
}

// 工具结果不走 messages 流，而是以 method=tools 的 stream_event 事件返回（tool-started/tool-finished）。
// 取出 tool-finished 的 output（一条 ToolMessage 字典），交给 msgChunks 与 AI 消息按 tool_call_id 关联。
const toolFinishedMessage = (chunk) => {
  const streamEvent = chunk?.event
  if (!streamEvent || streamEvent.method !== 'tools') return null

  const data = streamEvent.data
  if (!data || data.event !== 'tool-finished') return null

  const output = data.output
  if (!output || typeof output !== 'object') return null

  const id = output.id || output.tool_call_id || data.tool_call_id
  if (!id) return null
  return { ...output, type: 'tool', id }
}

// tool-started 事件用于：1) 切换“正在查询知识库…”等状态文案；2) 记录工具开始时刻，供 tool-finished 计算单次工具调用耗时。
const toolStartedInfo = (chunk) => {
  const streamEvent = chunk?.event
  if (!streamEvent || streamEvent.method !== 'tools') return null

  const data = streamEvent.data
  if (!data || data.event !== 'tool-started') return null

  const toolName = data.tool_name || data.tool
  if (!toolName) return null
  return { toolName, toolCallId: data.tool_call_id }
}

export function useAgentStreamHandler({
  getThreadState,
  processApprovalInStream,
  currentAgentId,
  supportsFiles,
  streamSmoother
}) {
  const debugPrefix = '[AgentStateDebug]'
  // 记录每个工具调用（按 tool_call_id）的开始时刻，用于 tool-finished 时计算单次调用耗时。
  const toolStartedAt = new Map()
  /**
   * Process a single stream chunk based on its status
   * @param {Object} chunk - The parsed JSON chunk
   * @param {String} threadId - The current thread ID
   * @returns {Boolean} - Returns true if processing should stop (e.g. error, finished, interrupted)
   */
  const handleStreamChunk = (chunk, threadId) => {
    const { status, msg, request_id, message: chunkMessage } = chunk
    const threadState = getThreadState(threadId)

    if (!threadState) return false

    switch (status) {
      case 'init':
        {
          const resolvedRequestId = request_id || threadState.pendingRequestId
          if (resolvedRequestId) {
            threadState.pendingRequestId = resolvedRequestId
          }
          if (resolvedRequestId && msg && msg.type !== 'system') {
            const localHumanMessage = threadState.onGoingConv.msgChunks[resolvedRequestId]?.find(
              (item) => item?.type === 'human' || item?.role === 'user'
            )
            const initMessage = {
              ...msg,
              id: msg?.id || resolvedRequestId,
              extra_metadata: {
                ...(msg?.extra_metadata || {}),
                request_id: resolvedRequestId
              }
            }
            if (localHumanMessage?.image_content && !initMessage.image_content) {
              initMessage.message_type = localHumanMessage.message_type || initMessage.message_type
              initMessage.image_content = localHumanMessage.image_content
            }
            threadState.onGoingConv.msgChunks[resolvedRequestId] = [initMessage]
          }
          threadState.replyLoadingVisible = true
          threadState.contextCompressing = false
          threadState.generationPhase = 'generating'
          threadState.activeToolName = null
        }
        return false

      case 'loading':
        {
          const messageChunk = loadingMessageChunk(chunk)
          if (messageChunk?.id) {
            messageChunk.run_id = chunk.run_id || messageChunk.run_id
            messageChunk.thread_id = threadId || messageChunk.thread_id
            messageChunk.extra_metadata = {
              ...(messageChunk.extra_metadata || {}),
              ...(chunk.run_id ? { run_id: chunk.run_id } : {}),
              ...(chunk.request_id ? { request_id: chunk.request_id } : {}),
              ...(threadId ? { thread_id: threadId } : {})
            }
            // 思考增量（reasoning 非空、正文为空）绕过平滑层直接落地，实时滚动思考过程；
            // 正文仍走平滑层，保持原有流式节奏。
            const isThinkingDelta =
              !messageChunk.content &&
              (messageChunk.reasoning_content || messageChunk.additional_kwargs?.reasoning_content)
            if (isThinkingDelta) {
              if (!threadState.onGoingConv.msgChunks[messageChunk.id]) {
                threadState.onGoingConv.msgChunks[messageChunk.id] = []
              }
              threadState.onGoingConv.msgChunks[messageChunk.id].push(messageChunk)
            } else if (streamSmoother) {
              streamSmoother.pushChunk(messageChunk, threadId)
            } else {
              if (!threadState.onGoingConv.msgChunks[messageChunk.id]) {
                threadState.onGoingConv.msgChunks[messageChunk.id] = []
              }
              threadState.onGoingConv.msgChunks[messageChunk.id].push(messageChunk)
            }
          }
          // 最终回答正文开始流式输出时隐藏生成状态；仅 reasoning_content 的增量切换为"正在思考"。
          const finalDelta = chunk?.stream_event
          if (finalDelta?.type === 'message_delta') {
            if (typeof finalDelta.content === 'string' && finalDelta.content) {
              threadState.replyLoadingVisible = false
              threadState.generationPhase = null
              threadState.activeToolName = null
            } else if (finalDelta.reasoning_content || finalDelta.additional_reasoning_content) {
              threadState.generationPhase = 'thinking'
              threadState.replyLoadingVisible = true
            }
          }
        }
        return false

      case 'stream_event':
        {
          // 工具开始：切换到具体工具状态文案（“正在查询知识库…”），并记录开始时刻。
          const startedInfo = toolStartedInfo(chunk)
          if (startedInfo) {
            if (startedInfo.toolCallId) {
              toolStartedAt.set(startedInfo.toolCallId, Date.now())
            }
            threadState.activeToolName = startedInfo.toolName
            threadState.generationPhase = 'tool'
            // 兜底：即使极短的过渡文本已提前隐藏加载状态，工具执行期间也要重新显示。
            threadState.replyLoadingVisible = true
            break
          }

          // 工具结果需立即落地（不经平滑层），写入 msgChunks 后由 convertToolResultToMessages
          // 按 tool_call_id 关联到对应 AI 消息的 tool_call，驱动其完成态。
          const toolMessage = toolFinishedMessage(chunk)
          if (toolMessage) {
            const start = toolMessage.id ? toolStartedAt.get(toolMessage.id) : undefined
            if (start != null) {
              toolMessage.duration_ms = Date.now() - start
              toolStartedAt.delete(toolMessage.id)
            }
            if (!threadState.onGoingConv.msgChunks[toolMessage.id]) {
              threadState.onGoingConv.msgChunks[toolMessage.id] = []
            }
            threadState.onGoingConv.msgChunks[toolMessage.id].push(toolMessage)
            threadState.generationPhase = 'finalizing'
          }
        }
        return false

      case 'error':
        streamSmoother?.flushThread(threadId)
        handleChatError({ message: chunkMessage }, 'stream')
        // Stop the loading indicator
        if (threadState) {
          threadState.isStreaming = false
          threadState.replyLoadingVisible = false
          threadState.pendingRequestId = null
          threadState.pendingInterrupt = null
          threadState.contextCompressing = false
          threadState.generationPhase = null
          threadState.activeToolName = null
        }
        return true

      case 'ask_user_question_required':
      case 'human_approval_required':
        streamSmoother?.flushThread(threadId)
        threadState.replyLoadingVisible = false
        threadState.generationPhase = null
        threadState.activeToolName = null
        console.log(`${debugPrefix}[approval_required]`, {
          threadId,
          currentAgentId: unref(currentAgentId)
        })
        // 使用审批 composable 处理审批请求
        return processApprovalInStream(chunk, threadId, unref(currentAgentId))

      case 'knowledge_handoff_available':
        return false

      case 'agent_state':
        console.log(`${debugPrefix}[agent_state_chunk]`, {
          threadId,
          supportsFiles: unref(supportsFiles),
          currentAgentId: unref(currentAgentId),
          hasAgentState: !!chunk.agent_state,
          todoCount: Array.isArray(chunk.agent_state?.todos) ? chunk.agent_state.todos.length : 0,
          uploadCount: Array.isArray(chunk.agent_state?.uploads)
            ? chunk.agent_state.uploads.length
            : 0
        })
        if (chunk.agent_state) {
          console.log(`${debugPrefix}[agent_state_apply]`, {
            threadId,
            todos: chunk.agent_state?.todos || [],
            uploads: chunk.agent_state?.uploads || []
          })
          threadState.agentState = chunk.agent_state
        } else {
          console.warn(`${debugPrefix}[agent_state_skip]`, {
            reason: 'empty_state',
            supportsFiles: unref(supportsFiles),
            hasAgentState: !!chunk.agent_state,
            currentAgentId: unref(currentAgentId),
            threadId
          })
        }
        return false

      case 'context_compression':
        if (chunk.compression) {
          threadState.contextCompressing = chunk.compression.status === 'started'
        }
        return false

      case 'finished':
        streamSmoother?.flushThread(threadId)
        // 先标记流式结束，但保持消息显示直到历史记录加载完成
        if (threadState) {
          threadState.isStreaming = false
          threadState.replyLoadingVisible = false
          threadState.pendingRequestId = null
          threadState.pendingInterrupt = null
          threadState.contextCompressing = false
          threadState.generationPhase = null
          threadState.activeToolName = null
          console.log(`${debugPrefix}[finished]`, {
            threadId,
            currentAgentId: unref(currentAgentId),
            hasThreadAgentState: !!threadState.agentState,
            supportsFiles: unref(supportsFiles)
          })
          if (unref(supportsFiles) && threadState.agentState) {
            console.log(
              `[AgentState|Final] ${new Date().toLocaleTimeString()}.${new Date().getMilliseconds()}`,
              {
                threadId,
                todos: threadState.agentState?.todos || [],
                uploads: threadState.agentState?.uploads || []
              }
            )
          }
        }
        return true

      case 'interrupted':
        streamSmoother?.flushThread(threadId)
        // 中断状态，刷新消息历史
        console.warn(`${debugPrefix}[interrupted]`, {
          threadId,
          message: chunkMessage,
          currentAgentId: unref(currentAgentId)
        })
        if (threadState) {
          threadState.isStreaming = false
          threadState.replyLoadingVisible = false
          threadState.pendingRequestId = null
          threadState.contextCompressing = false
          threadState.generationPhase = null
          threadState.activeToolName = null
          const pendingInterrupt = extractPendingInterrupt(chunk, threadId)
          if (pendingInterrupt) {
            threadState.pendingInterrupt = pendingInterrupt
          }
        }
        // 如果有 message 字段，显示提示（例如：敏感内容检测）
        if (chunkMessage) {
          message.info(chunkMessage)
        }
        return true

      case 'warning':
        if (chunkMessage) {
          message.warning(chunkMessage)
        }
        return false
    }

    return false
  }

  return {
    handleStreamChunk
  }
}
