import { i18n } from '@/i18n'
import {
  BookOpen,
  Bot,
  Calculator,
  CheckSquare,
  Database,
  FileEdit,
  FilePen,
  FileText,
  Folder,
  FolderOutput,
  FolderSearch,
  Globe,
  HelpCircle,
  Image,
  Network,
  RefreshCw,
  SquareTerminal
} from 'lucide-vue-next'

export const TOOL_ICON_MAP = {
  ask_user_question: HelpCircle,
  bash: SquareTerminal,
  calculator: Calculator,
  cmd: SquareTerminal,
  edit_file: FilePen,
  execute: SquareTerminal,
  find_kb_document: FolderSearch,
  get_mindmap: Network,
  glob: FolderSearch,
  grep: FolderSearch,
  list_directory: Folder,
  list_kbs: BookOpen,
  ls: Folder,
  mysql_describe_table: Database,
  mysql_list_tables: Database,
  mysql_query: Database,
  ocr_parse_file: FileText,
  open_kb_document: FileText,
  present_artifacts: FolderOutput,
  query_kb: BookOpen,
  query_kbs: BookOpen,
  read_file: FileText,
  replace: FilePen,
  run_shell_command: SquareTerminal,
  search_file: FolderSearch,
  search_file_content: FolderSearch,
  subagent_await: Bot,
  subagent_cancel: Bot,
  subagent_events: RefreshCw,
  subagent_start: Bot,
  subagent_status: RefreshCw,
  task: Bot,
  tavily_search: Globe,
  text_to_img_qwen_image: Image,
  write_file: FileEdit,
  write_todos: CheckSquare
}

// 工具 ID → 展示名。用户看到的是「检索知识库」，不是 query_kb。
// 与 TOOL_ICON_MAP 同一份 ID 列表；新增工具时两处一起补。
export const TOOL_LABEL_KEY_MAP = {
  ask_user_question: 'toolCall.badge.askUser',
  bash: 'toolCall.badge.bash',
  calculator: 'toolCall.badge.calculator',
  cmd: 'toolCall.badge.cmd',
  edit_file: 'toolCall.badge.editFile',
  execute: 'toolCall.badge.execute',
  find_kb_document: 'toolCall.badge.findKbDocument',
  get_mindmap: 'toolCall.badge.getMindmap',
  glob: 'toolCall.badge.glob',
  grep: 'toolCall.badge.grep',
  list_directory: 'toolCall.badge.listDirectory',
  list_kbs: 'toolCall.badge.listKbs',
  ls: 'toolCall.badge.ls',
  mysql_describe_table: 'toolCall.badge.mysqlDescribeTable',
  mysql_list_tables: 'toolCall.badge.mysqlListTables',
  mysql_query: 'toolCall.badge.mysqlQuery',
  ocr_parse_file: 'toolCall.badge.ocrParseFile',
  open_kb_document: 'toolCall.badge.openKbDocument',
  present_artifacts: 'toolCall.badge.presentArtifacts',
  query_kb: 'toolCall.badge.queryKb',
  query_kbs: 'toolCall.badge.queryKbs',
  read_file: 'toolCall.badge.readFile',
  replace: 'toolCall.badge.replace',
  run_shell_command: 'toolCall.badge.runShellCommand',
  search_file: 'toolCall.badge.searchFile',
  search_file_content: 'toolCall.badge.searchFileContent',
  subagent_await: 'toolCall.badge.subagentAwait',
  subagent_cancel: 'toolCall.badge.subagentCancel',
  subagent_events: 'toolCall.badge.subagentEvents',
  subagent_start: 'toolCall.badge.subagentStart',
  subagent_status: 'toolCall.badge.subagentStatus',
  task: 'toolCall.badge.task',
  tavily_search: 'toolCall.badge.tavilySearch',
  text_to_img_qwen_image: 'toolCall.badge.textToImg',
  write_file: 'toolCall.badge.writeFile',
  write_todos: 'toolCall.badge.writeTodos'
}

// Keep intentionally hidden tool calls centralized so group summaries and renderers stay consistent.
export const HIDDEN_TOOL_CALL_IDS = ['present_artifacts']

export const getToolCallId = (toolCall) => toolCall?.name || toolCall?.function?.name || ''

export const isHiddenToolCall = (toolCall) => HIDDEN_TOOL_CALL_IDS.includes(getToolCallId(toolCall))

export const isValidToolCall = (toolCall) => {
  return Boolean(
    toolCall &&
    (toolCall.id || toolCall.name || toolCall.function?.name) &&
    (toolCall.args !== undefined ||
      toolCall.function?.arguments !== undefined ||
      toolCall.tool_call_result !== undefined)
  )
}

export const parseToolCallArgs = (toolCall) => {
  const args = toolCall?.args ?? toolCall?.function?.arguments
  if (!args) return {}
  if (typeof args === 'object') return args
  try {
    return JSON.parse(args)
  } catch {
    return {}
  }
}

export const SUBAGENT_TOOL_IDS = [
  'task',
  'subagent_start',
  'subagent_status',
  'subagent_events',
  'subagent_cancel',
  'subagent_await'
]

export const isSubagentToolCall = (toolCall) => SUBAGENT_TOOL_IDS.includes(getToolCallId(toolCall))

export const parseToolCallResult = (toolCall) => {
  const content = toolCall?.tool_call_result?.content ?? toolCall?.result
  if (!content) return null
  if (typeof content === 'object') return content
  try {
    return JSON.parse(content)
  } catch {
    return null
  }
}

export const enrichSubagentToolCall = (
  toolCall,
  { subagentRunById, subagentRunByThreadId, subagentOptionBySlug } = {}
) => {
  if (!isSubagentToolCall(toolCall)) return toolCall

  const args = parseToolCallArgs(toolCall)
  const result = parseToolCallResult(toolCall)
  const subagentRun =
    (toolCall.id ? subagentRunById?.get?.(String(toolCall.id)) : null) ||
    (result?.run_id ? subagentRunById?.get?.(String(result.run_id)) : null) ||
    (args.thread_id ? subagentRunByThreadId?.get?.(String(args.thread_id)) : null) ||
    (result?.thread_id ? subagentRunByThreadId?.get?.(String(result.thread_id)) : null)
  const subagentOption = args.subagent_slug
    ? subagentOptionBySlug?.get?.(String(args.subagent_slug))
    : null
  const displayLabel =
    result?.subagent_name ||
    subagentRun?.subagent_name ||
    subagentOption?.name ||
    result?.subagent_slug ||
    subagentRun?.subagent_slug ||
    undefined

  return {
    ...toolCall,
    ...(subagentRun ? { subagent_run: subagentRun } : {}),
    ...(displayLabel ? { display_label: displayLabel } : {})
  }
}

export const normalizeToolCalls = (toolCalls, { includeHidden = false, mapToolCall } = {}) => {
  if (!Array.isArray(toolCalls)) return []

  return toolCalls
    .filter((toolCall) => {
      if (!isValidToolCall(toolCall)) return false
      return includeHidden || !isHiddenToolCall(toolCall)
    })
    .map((toolCall) => (mapToolCall ? mapToolCall(toolCall) : toolCall))
}

export const enrichTaskToolCalls = (toolCalls, options = {}) =>
  normalizeToolCalls(toolCalls, {
    mapToolCall: (toolCall) => enrichSubagentToolCall(toolCall, options)
  })

export const getToolIcon = (toolId) => TOOL_ICON_MAP[toolId] || null

// 未登记的工具返回 ''，由调用方决定兜底文案——别再猜名字（下划线换空格会渲染出 "Query kb"）
export const getToolLabel = (toolId) => {
  const key = TOOL_LABEL_KEY_MAP[toolId]
  return key ? i18n.global.t(key) : ''
}
