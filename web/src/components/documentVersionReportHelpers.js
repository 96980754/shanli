export const CHANGE_TYPE_VIEW = {
  new: { label: '新增', color: 'blue' },
  changed: { label: '变更', color: 'orange' },
  removed: { label: '删除', color: 'red' },
  conflict: { label: '冲突', color: 'red' }
}

export const getChangeTypeView = (changeType) =>
  CHANGE_TYPE_VIEW[changeType] || { label: changeType || '未知', color: 'default' }

export const canReviewValidationReport = (report, canManage) =>
  Boolean(canManage && report?.status === 'review_required' && report?.decision === 'pending')

export const getEvidenceQuote = (evidence) => {
  const first = Array.isArray(evidence) ? evidence[0] : evidence
  return first?.quote || '无可用证据原文'
}
