export const isKnowledgeMountSource = (activeSourceKey, database) => {
  const kbId = String(database?.kb_id || '').trim()
  return Boolean(kbId) && activeSourceKey === `database:${kbId}`
}

export const canManageWorkspaceSource = (activeSourceKey, database) => {
  if (!isKnowledgeMountSource(activeSourceKey, database)) return true
  return database?.can_manage === true
}

export const getWorkspaceActionDisabledReason = (activeSourceKey, database) => {
  if (canManageWorkspaceSource(activeSourceKey, database)) return ''
  return '当前知识库为只读，无法执行此操作'
}
