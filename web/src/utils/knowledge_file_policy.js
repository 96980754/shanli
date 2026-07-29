export const FILE_ACTIONS = {
  PARSE: 'parse',
  INDEX: 'index',
  REPLACEMENT_CLEANUP: 'replacement_cleanup',
  CLEANING: 'cleaning'
}

const STATUS_VIEW = {
  uploaded: { label: '待解析', tone: 'status-warning', icon: 'clock' },
  parsing: { label: '解析中', tone: 'status-info', icon: 'progress' },
  parsed: { label: '待入库', tone: 'status-primary', icon: 'file' },
  cleaning: { label: '清洗中', tone: 'status-info', icon: 'progress' },
  waiting_confirmation: { label: '待确认清洗', tone: 'status-warning', icon: 'clock' },
  confirmed: { label: '已确认', tone: 'status-primary', icon: 'file' },
  error_parsing: { label: '重试解析', tone: 'status-error', icon: 'error' },
  error_cleaning: { label: '清洗失败', tone: 'status-error', icon: 'error' },
  indexing: { label: '入库中', tone: 'status-info', icon: 'progress' },
  indexed: { label: '已入库', tone: 'status-success', icon: 'success' },
  error_indexing: { label: '重试入库', tone: 'status-error', icon: 'error' },
  error_replacement_cleanup: { label: '重试版本清理', tone: 'status-error', icon: 'error' },
  done: { label: '已入库', tone: 'status-success', icon: 'success' },
  failed: { label: '入库失败', tone: 'status-error', icon: 'error' },
  processing: { label: '处理中', tone: 'status-info', icon: 'progress' },
  waiting: { label: '等待中', tone: 'status-warning', icon: 'clock' }
}

const STATUS_ACTION = {
  uploaded: { type: FILE_ACTIONS.PARSE, label: '解析文件' },
  error_parsing: { type: FILE_ACTIONS.PARSE, label: '重试解析' },
  parsed: { type: FILE_ACTIONS.CLEANING, label: '生成清洗草稿' },
  waiting_confirmation: { type: FILE_ACTIONS.CLEANING, label: '确认清洗' },
  error_cleaning: { type: FILE_ACTIONS.CLEANING, label: '重试清洗' },
  error_indexing: { type: FILE_ACTIONS.INDEX, label: '重试入库' },
  error_replacement_cleanup: {
    type: FILE_ACTIONS.REPLACEMENT_CLEANUP,
    label: '重试版本清理'
  }
}

const PROCESSING_STAGE_LABELS = {
  duplicate_check: '重复检查',
  validating: '文件校验',
  uploading: '上传中',
  converting: '格式转换',
  extracting_text: '提取文本',
  parsing: '解析中',
  ocr_processing: 'OCR 识别',
  cleaning: '清洗中',
  replacement_preparing: '准备新版本',
  indexing: '入库中',
  verifying: '验证新向量',
  switching_version: '切换版本',
  replacement_cleanup: '清理旧版本'
}

const PARSED_PREVIEW_STATUSES = new Set([
  'done',
  'parsed',
  'cleaning',
  'waiting_confirmation',
  'confirmed',
  'indexed',
  'error_cleaning',
  'error_indexing',
  'error_replacement_cleanup'
])
const SOURCE_ONLY_PREVIEW_STATUSES = new Set(['uploaded', 'error_parsing'])
const TABLE_SELECTION_BLOCKED_STATUSES = new Set(['processing', 'waiting', 'cleaning'])
const DELETE_BLOCKED_STATUSES = new Set(['processing', 'parsing', 'cleaning', 'indexing'])
const VERSION_MAINTENANCE_STAGES = new Set(['switching_version', 'replacement_cleanup'])
const PROCESSING_STATUSES = new Set(['processing', 'waiting', 'parsing', 'cleaning', 'indexing'])
const INDEXABLE_STATUSES = new Set(['parsed', 'error_indexing', 'done', 'indexed'])
const PARSEABLE_STATUSES = new Set(['uploaded', 'error_parsing'])
const DOWNLOADABLE_STATUSES = new Set([
  'done',
  'indexed',
  'parsed',
  'waiting_confirmation',
  'confirmed',
  'error_cleaning',
  'error_indexing',
  'error_replacement_cleanup'
])
const CHUNK_PREVIEW_STATUSES = new Set(['done', 'indexed', 'error_replacement_cleanup'])
const CLEANING_PREVIEW_STATUSES = new Set([
  'done',
  'parsed',
  'cleaning',
  'waiting_confirmation',
  'confirmed',
  'indexed',
  'error_cleaning',
  'error_indexing',
  'error_replacement_cleanup'
])
const STATUS_SORT_ORDER = {
  done: 1,
  indexed: 1,
  processing: 2,
  indexing: 2,
  parsing: 2,
  waiting: 3,
  uploaded: 3,
  parsed: 3,
  waiting_confirmation: 3,
  confirmed: 3,
  failed: 4,
  error_cleaning: 4,
  error_indexing: 4,
  error_replacement_cleanup: 4,
  error_parsing: 4
}

export const FILE_STATUS_FILTER_OPTIONS = [
  { label: '待解析', value: 'uploaded' },
  { label: '解析中', value: 'parsing' },
  { label: '待入库', value: 'parsed' },
  { label: '待确认清洗', value: 'waiting_confirmation' },
  { label: '清洗失败', value: 'error_cleaning' },
  { label: '重试解析', value: 'error_parsing' },
  { label: '入库中', value: 'indexing' },
  { label: '已入库', value: 'indexed' },
  { label: '重试入库', value: 'error_indexing' },
  { label: '重试版本清理', value: 'error_replacement_cleanup' }
]

export const getFileStatusView = (status) =>
  STATUS_VIEW[status] || { label: status || '', tone: '', icon: null }

export const getProcessingStageLabel = (stage) => PROCESSING_STAGE_LABELS[stage] || stage || ''

export const getFilePrimaryAction = (record) => {
  if (!record || record.is_folder) return null
  return STATUS_ACTION[record.status] || null
}

export const canParseFile = (record) =>
  Boolean(record && !record.is_folder && PARSEABLE_STATUSES.has(record.status))

export const canIndexFile = (record) =>
  Boolean(record && !record.is_folder && INDEXABLE_STATUSES.has(record.status))

export const canReindexFile = (record) =>
  Boolean(record && !record.is_folder && (record.status === 'done' || record.status === 'indexed'))

export const canOpenCleaning = (record) =>
  Boolean(record && !record.is_folder && CLEANING_PREVIEW_STATUSES.has(record.status))

export const canOpenEnrichment = (record) =>
  Boolean(
    record &&
    !record.is_folder &&
    ['done', 'indexed', 'error_replacement_cleanup'].includes(record.status)
  )

export const canOpenDocumentQA = (record) =>
  Boolean(
    record &&
    !record.is_folder &&
    record.is_active !== false &&
    ['done', 'indexed', 'error_replacement_cleanup'].includes(record.status)
  )

export const canDownloadFile = (record) =>
  Boolean(
    record &&
    !record.is_folder &&
    record.file_type !== 'url' &&
    DOWNLOADABLE_STATUSES.has(record.status)
  )

export const canSelectFile = (record, locked = false) =>
  Boolean(
    record &&
    !record.is_folder &&
    !locked &&
    !TABLE_SELECTION_BLOCKED_STATUSES.has(record.status) &&
    !VERSION_MAINTENANCE_STAGES.has(record.processing_stage)
  )

export const canDeleteFile = (record, locked = false) =>
  Boolean(
    record &&
    !record.is_folder &&
    !locked &&
    !DELETE_BLOCKED_STATUSES.has(record.status) &&
    !VERSION_MAINTENANCE_STAGES.has(record.processing_stage)
  )

export const isProcessingFile = (record) =>
  Boolean(record && PROCESSING_STATUSES.has(record.status))

export const matchesStatusFilter = (record, status) => {
  if (!record || status === 'all') return true
  return (
    record.status === status ||
    (status === 'indexed' && record.status === 'done') ||
    (status === 'error_indexing' && record.status === 'failed')
  )
}

export const getFileStatusSortWeight = (record) => STATUS_SORT_ORDER[record?.status] || 5

export const canPreviewParsed = (record) => {
  if (!record || record.is_folder) return false
  if ('has_parsed_markdown' in record) return Boolean(record.has_parsed_markdown)
  return PARSED_PREVIEW_STATUSES.has(record.status)
}

export const canPreviewOriginal = (record) => {
  if (!record || record.is_folder || record.file_type === 'url') return false
  if ('has_original_file' in record) return Boolean(record.has_original_file)
  return true
}

export const canPreviewChunks = (record) =>
  Boolean(record && !record.is_folder && CHUNK_PREVIEW_STATUSES.has(record.status))

export const canOpenFileDetail = (record) =>
  canPreviewParsed(record) ||
  Boolean(record && SOURCE_ONLY_PREVIEW_STATUSES.has(record.status) && canPreviewOriginal(record))

export const getDefaultDetailView = (record) => {
  if (!canPreviewParsed(record) && canPreviewOriginal(record)) return 'source'
  return 'markdown'
}
