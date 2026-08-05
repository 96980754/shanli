export const KNOWLEDGE_CONFLICT_RESOLUTIONS = [
  { value: 'keep_old', label: '保留旧值' },
  { value: 'use_new', label: '使用新值' },
  { value: 'merge', label: '合并' },
  { value: 'keep_both_by_version', label: '按版本分别保留' },
  { value: 'mark_as_completion', label: '标记为补全' },
  { value: 'link_existing_entity', label: '关联已有实体' },
  { value: 'create_new_entity', label: '创建新实体' },
  { value: 'defer', label: '暂缓' },
  { value: 'reject_incoming', label: '拒绝新候选' }
]

export const knowledgeConflictClassificationLabel = (classification) =>
  ({
    DUPLICATE: '重复',
    COMPLETION: '补全',
    UPDATE: '更新',
    CONFLICT: '冲突',
    LINK_AMBIGUOUS: '实体待确认',
    INVALID: '无效'
  })[classification] || classification

export const knowledgeConflictClassificationColor = (classification) =>
  ({
    DUPLICATE: 'default',
    COMPLETION: 'green',
    UPDATE: 'blue',
    CONFLICT: 'red',
    LINK_AMBIGUOUS: 'orange',
    INVALID: 'volcano'
  })[classification] || 'default'

export const knowledgeConflictStatusLabel = (status) =>
  ({ pending: '待处理', resolved: '已处理', deferred: '已暂缓', ignored: '已忽略' })[status] ||
  status

export const knowledgePublishStatusLabel = (status) =>
  ({
    not_requested: '未请求发布',
    pending: '待发布',
    processing: '发布中',
    succeeded: '已发布',
    failed: '发布失败',
    dead_letter: '需人工处理'
  })[status] ||
  status ||
  '未请求发布'

export const canRetryKnowledgePublish = (item, readonly) =>
  !readonly && ['failed', 'dead_letter'].includes(item?.publish_status)

export const formatKnowledgeValue = (value, unit = '') => {
  if (value === null || value === undefined) return '-'
  const text = Array.isArray(value)
    ? value.join('、')
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value)
  return unit ? `${text} ${unit}` : text
}
