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

// 事实快照的展示值：优先用抽取原文（keyed 槽位如 "电池容量: 3800mAh" 自带槽位名），
// 缺失时退回归一化值，两者都没有则返回空串由调用方渲染场景文案。
export const getFactValue = (fact) => {
  if (!fact) return ''
  return fact.target?.text || fact.normalized_value || ''
}

// 按变更场景给出"该侧不存在事实/证据"的文案，避免一律显示"无可用证据原文"误导用户
export const getMissingFactText = (changeType, side) => {
  if (changeType === 'new' && side === 'old') return '旧版无此事实'
  if (changeType === 'removed' && side === 'new') return '新版已删除此事实'
  if (changeType === 'conflict' && side === 'old') return '旧版无此事实'
  return '无可用证据原文'
}

// 报告项某一侧应展示的值：新增项的旧侧、删除项的新侧及否定不存在事实的冲突项旧侧
// 按场景文案处理，其余情况展示事实值，缺失时退回场景文案。
export const getSideValue = (item, side) => {
  if (!item) return getMissingFactText('', side)
  if (item.change_type === 'new' && side === 'old') return getMissingFactText('new', 'old')
  if (item.change_type === 'removed' && side === 'new') return getMissingFactText('removed', 'new')
  const fact = side === 'old' ? item.old_fact : item.new_fact
  return getFactValue(fact) || getMissingFactText(item.change_type, side)
}

export const getEvidenceQuote = (evidence, changeType, side) => {
  const first = Array.isArray(evidence) ? evidence[0] : evidence
  return first?.quote || getMissingFactText(changeType, side)
}
