import { i18n } from '@/i18n'

export const FILE_ACTIONS = {
  PARSE: 'parse',
  INDEX: 'index',
  REPLACEMENT_CLEANUP: 'replacement_cleanup'
}

const STATUS_VIEW = {
  uploaded: { key: 'kbFile.statusUploaded', tone: 'status-warning', icon: 'clock' },
  parsing: { key: 'kbFile.statusParsing', tone: 'status-info', icon: 'progress' },
  parsed: { key: 'kbFile.statusParsed', tone: 'status-primary', icon: 'file' },
  error_parsing: { key: 'kbFile.statusErrorParsing', tone: 'status-error', icon: 'error' },
  indexing: { key: 'kbFile.statusIndexing', tone: 'status-info', icon: 'progress' },
  indexed: { key: 'kbFile.statusIndexed', tone: 'status-success', icon: 'success' },
  error_indexing: { key: 'kbFile.statusErrorIndexing', tone: 'status-error', icon: 'error' },
  done: { key: 'kbFile.statusDone', tone: 'status-success', icon: 'success' },
  failed: { key: 'kbFile.statusFailed', tone: 'status-error', icon: 'error' },
  processing: { key: 'kbFile.statusProcessing', tone: 'status-info', icon: 'progress' },
  waiting: { key: 'kbFile.statusWaiting', tone: 'status-warning', icon: 'clock' },
  conflict_detecting: { key: 'kbFile.statusConflictDetecting', tone: 'status-info', icon: 'progress' },
  conflict_clear: { key: 'kbFile.statusConflictClear', tone: 'status-success', icon: 'success' },
  conflict_review: { key: 'kbFile.statusConflictReview', tone: 'status-warning', icon: 'clock' },
  conflict_inconclusive: { key: 'kbFile.statusConflictInconclusive', tone: 'status-warning', icon: 'clock' },
  conflict_detection_failed: { key: 'kbFile.statusConflictDetectionFailed', tone: 'status-error', icon: 'error' },
  version_task_failed: { key: 'kbFile.statusVersionTaskFailed', tone: 'status-error', icon: 'error' },
  validation_processing: { key: 'kbFile.statusValidationProcessing', tone: 'status-info', icon: 'progress' },
  validation_review: { key: 'kbFile.statusValidationReview', tone: 'status-warning', icon: 'clock' },
  validation_accepted: { key: 'kbFile.statusValidationAccepted', tone: 'status-success', icon: 'success' },
  validation_failed: { key: 'kbFile.statusValidationFailed', tone: 'status-error', icon: 'error' },
  validation_rejected: { key: 'kbFile.statusValidationRejected', tone: 'status-error', icon: 'error' },
  error_replacement_cleanup: { key: 'kbFile.statusErrorReplacementCleanup', tone: 'status-error', icon: 'error' }
}

const STATUS_ACTION = {
  uploaded: { type: FILE_ACTIONS.PARSE, key: 'kbFile.actionParseFile' },
  error_parsing: { type: FILE_ACTIONS.PARSE, key: 'kbFile.actionRetryParse' },
  parsed: { type: FILE_ACTIONS.INDEX, key: 'kbFile.actionIndex' },
  error_indexing: { type: FILE_ACTIONS.INDEX, key: 'kbFile.actionRetryIndex' },
  error_replacement_cleanup: { type: FILE_ACTIONS.REPLACEMENT_CLEANUP, key: 'kbFile.actionRetryVersionCleanup' }
}

const PROCESSING_STAGE_LABELS = {
  replacement_preparing: 'kbFile.stageReplacementPreparing',
  switching_version: 'kbFile.stageSwitchingVersion',
  replacement_cleanup: 'kbFile.stageReplacementCleanup'
}

const VERSION_MAINTENANCE_STAGES = new Set(['switching_version', 'replacement_cleanup'])

const PARSED_PREVIEW_STATUSES = new Set(['done', 'parsed', 'indexed', 'error_indexing'])
const SOURCE_ONLY_PREVIEW_STATUSES = new Set(['uploaded', 'error_parsing'])
const TABLE_SELECTION_BLOCKED_STATUSES = new Set(['processing', 'waiting'])
const DELETE_BLOCKED_STATUSES = new Set(['processing', 'parsing', 'indexing'])
const PROCESSING_STATUSES = new Set(['processing', 'waiting', 'parsing', 'indexing'])
const INDEXABLE_STATUSES = new Set(['parsed', 'error_indexing', 'done', 'indexed'])
const PARSEABLE_STATUSES = new Set(['uploaded', 'error_parsing'])
const DOWNLOADABLE_STATUSES = new Set(['done', 'indexed', 'parsed', 'error_indexing', 'error_replacement_cleanup'])
const CHUNK_PREVIEW_STATUSES = new Set(['done', 'indexed', 'error_replacement_cleanup'])
const STATUS_SORT_ORDER = {
  done: 1,
  indexed: 1,
  processing: 2,
  indexing: 2,
  parsing: 2,
  waiting: 3,
  uploaded: 3,
  parsed: 3,
  failed: 4,
  error_indexing: 4,
  error_parsing: 4,
  error_replacement_cleanup: 4
}

export const FILE_STATUS_FILTER_OPTIONS = [
  { label: i18n.global.t('kbFile.statusUploaded'), value: 'uploaded' },
  { label: i18n.global.t('kbFile.statusParsing'), value: 'parsing' },
  { label: i18n.global.t('kbFile.statusParsed'), value: 'parsed' },
  { label: i18n.global.t('kbFile.statusErrorParsing'), value: 'error_parsing' },
  { label: i18n.global.t('kbFile.statusIndexing'), value: 'indexing' },
  { label: i18n.global.t('kbFile.statusIndexed'), value: 'indexed' },
  { label: i18n.global.t('kbFile.statusErrorIndexing'), value: 'error_indexing' }
]

export const getFileStatusView = (status) => {
  const meta = STATUS_VIEW[status]
  if (!meta) return { label: status || '', tone: '', icon: null }
  return { label: i18n.global.t(meta.key), tone: meta.tone, icon: meta.icon }
}

export const getFilePrimaryAction = (record) => {
  if (!record || record.is_folder) return null
  const action = STATUS_ACTION[record.status]
  if (!action) return null
  return { type: action.type, label: i18n.global.t(action.key) }
}

export const canParseFile = (record) =>
  Boolean(record && !record.is_folder && PARSEABLE_STATUSES.has(record.status))

export const canIndexFile = (record) =>
  Boolean(record && !record.is_folder && INDEXABLE_STATUSES.has(record.status))

export const canReindexFile = (record) =>
  Boolean(record && !record.is_folder && (record.status === 'done' || record.status === 'indexed'))

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

export const getProcessingStageLabel = (stage) => {
  const key = PROCESSING_STAGE_LABELS[stage]
  return key ? i18n.global.t(key) : stage || ''
}

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
