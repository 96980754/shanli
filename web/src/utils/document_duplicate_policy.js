import { i18n } from '@/i18n'

export const DUPLICATE_STRATEGIES = {
  PROMPT: 'prompt',
  SKIP: 'skip',
  REPLACE: 'replace',
  KEEP_BOTH: 'keep_both'
}
export const buildKnowledgeUploadUrl = (
  kbId,
  strategy = DUPLICATE_STRATEGIES.PROMPT,
  replaceFileId = null,
  parentId = null
) => {
  const query = new URLSearchParams({
    kb_id: kbId,
    duplicate_strategy: strategy
  })
  if (replaceFileId) query.set('replace_file_id', replaceFileId)
  if (parentId) query.set('parent_id', parentId)
  return `/api/knowledge/files/upload?${query.toString()}`
}
export const getDuplicateConflictDetail = (response) => {
  const detail = response?.detail
  if (!detail || typeof detail !== 'object' || detail.code !== 'duplicate_conflict') return null
  if (!Array.isArray(detail.allowed_strategies) || !Array.isArray(detail.conflicts)) return null
  return detail
}
export const isDuplicateStrategyAllowed = (detail, strategy) =>
  Boolean(detail?.allowed_strategies?.includes(strategy))
export const buildDuplicateResolution = (detail, strategy) => {
  if (detail?.conflict_type !== 'same_name' || !isDuplicateStrategyAllowed(detail, strategy)) {
    return null
  }
  if (strategy === DUPLICATE_STRATEGIES.KEEP_BOTH) {
    return { duplicateStrategy: strategy, replaceFileId: null }
  }
  if (strategy === DUPLICATE_STRATEGIES.REPLACE) {
    const target = detail.conflicts?.find((item) => item?.is_active !== false)
    if (!target?.file_id) return null
    return { duplicateStrategy: strategy, replaceFileId: target.file_id }
  }
  return null
}
export const getReplacementInProgressDetail = (response) => {
  const detail = response?.detail
  if (!detail || typeof detail !== 'object' || detail.code !== 'replacement_in_progress')
    return null
  return detail
}
export const getInvalidReplacementTargetDetail = (response) => {
  const detail = response?.detail
  if (!detail || typeof detail !== 'object' || detail.code !== 'invalid_replacement_target') {
    return null
  }
  return detail
}
export const getDuplicateConflictMessage = (detail) => {
  if (detail?.conflict_type === 'exact_content') {
    const existing = detail.conflicts?.[0]
    const location = existing?.display_path || existing?.filename
    return location
      ? i18n.global.t('kbFile.duplicateExactContent', { location })
      : i18n.global.t('kbFile.duplicateDetected')
  }
  if (detail?.conflict_type === 'same_name') {
    return i18n.global.t('kbFile.duplicateSameName')
  }
  return i18n.global.t('kbFile.uploadFailedGeneric')
}
export const getSafeUploadErrorMessage = (response) => {
  const conflict = getDuplicateConflictDetail(response)
  if (conflict) return getDuplicateConflictMessage(conflict)
  if (getReplacementInProgressDetail(response)) {
    return i18n.global.t('kbFile.uploadReplacementInProgress')
  }
  if (getInvalidReplacementTargetDetail(response)) {
    return i18n.global.t('kbFile.uploadInvalidReplacementTarget')
  }
  const detail = response?.detail
  const formatErrorCodes = new Set([
    'unsupported_format',
    'converter_unavailable',
    'conversion_timeout',
    'conversion_failed',
    'encrypted_document',
    'invalid_file_signature',
    'invalid_converted_output',
    'file_too_large',
    'image_too_large',
    'image_decode_failed',
    'empty_parsing_result'
  ])
  if (
    detail &&
    typeof detail === 'object' &&
    formatErrorCodes.has(detail.code) &&
    typeof detail.message === 'string' &&
    detail.message.trim()
  ) {
    return detail.message
  }
  return i18n.global.t('kbFile.uploadFailedGeneric')
}
