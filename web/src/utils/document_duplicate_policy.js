export const DUPLICATE_STRATEGIES = {
  PROMPT: 'prompt',
  SKIP: 'skip',
  REPLACE: 'replace',
  KEEP_BOTH: 'keep_both'
}

export const buildKnowledgeUploadUrl = (
  kbId,
  strategy = DUPLICATE_STRATEGIES.PROMPT,
  replaceFileId = null
) => {
  const query = new URLSearchParams({
    kb_id: kbId,
    duplicate_strategy: strategy
  })
  if (replaceFileId) query.set('replace_file_id', replaceFileId)
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

export const getDuplicateConflictMessage = (detail) => {
  if (detail?.conflict_type === 'exact_content') {
    return '检测到重复文档，不能重复上传'
  }
  if (detail?.conflict_type === 'same_name') {
    return '已存在同名文件，当前暂不支持更新或替换'
  }
  return '文件上传失败，请稍后重试'
}

export const getSafeUploadErrorMessage = (response) => {
  const conflict = getDuplicateConflictDetail(response)
  if (conflict) return getDuplicateConflictMessage(conflict)
  if (getReplacementInProgressDetail(response)) {
    return '文件正在被其他用户更新，请稍后重试'
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
  return '文件上传失败，请稍后重试'
}
