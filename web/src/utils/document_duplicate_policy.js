export const DUPLICATE_STRATEGIES = {
  PROMPT: 'prompt',
  SKIP: 'skip',
  REPLACE: 'replace',
  KEEP_BOTH: 'keep_both'
}

export const buildKnowledgeUploadUrl = (kbId, strategy = DUPLICATE_STRATEGIES.PROMPT, replaceFileId = null) => {
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
  return conflict ? getDuplicateConflictMessage(conflict) : '文件上传失败，请稍后重试'
}
