import { i18n } from '@/i18n'

const RESOLUTION_LABEL_KEYS = {
  keep_old: 'conflict.resolutionKeepOld',
  use_new: 'conflict.resolutionUseNew',
  merge: 'conflict.resolutionMerge',
  keep_both_by_version: 'conflict.resolutionKeepBothByVersion',
  mark_as_completion: 'conflict.resolutionMarkAsCompletion',
  link_existing_entity: 'conflict.resolutionLinkExistingEntity',
  create_new_entity: 'conflict.resolutionCreateNewEntity',
  defer: 'conflict.resolutionDefer',
  reject_incoming: 'conflict.resolutionRejectIncoming'
}

export const KNOWLEDGE_CONFLICT_RESOLUTIONS = Object.entries(RESOLUTION_LABEL_KEYS).map(
  ([value, key]) => ({ value, label: i18n.global.t(key) })
)

export const knowledgeConflictClassificationLabel = (classification) => {
  const key = {
    DUPLICATE: 'conflict.classificationDuplicate',
    COMPLETION: 'conflict.classificationCompletion',
    UPDATE: 'conflict.classificationUpdate',
    CONFLICT: 'conflict.classificationConflict',
    LINK_AMBIGUOUS: 'conflict.classificationLinkAmbiguous',
    INVALID: 'conflict.classificationInvalid'
  }[classification]
  return key ? i18n.global.t(key) : classification
}

export const knowledgeConflictClassificationColor = (classification) =>
  ({
    DUPLICATE: 'default',
    COMPLETION: 'green',
    UPDATE: 'blue',
    CONFLICT: 'red',
    LINK_AMBIGUOUS: 'orange',
    INVALID: 'volcano'
  })[classification] || 'default'

export const knowledgeConflictStatusLabel = (status) => {
  const key = {
    pending: 'conflict.statusPending',
    resolved: 'conflict.statusResolved',
    deferred: 'conflict.statusDeferred',
    ignored: 'conflict.statusIgnored'
  }[status]
  return key ? i18n.global.t(key) : status
}

export const knowledgePublishStatusLabel = (status) => {
  const key = {
    not_requested: 'conflict.publishNotRequested',
    pending: 'conflict.publishPending',
    processing: 'conflict.publishProcessing',
    succeeded: 'conflict.publishSucceeded',
    failed: 'conflict.publishFailed',
    dead_letter: 'conflict.publishDeadLetter'
  }[status]
  return key ? i18n.global.t(key) : status || i18n.global.t('conflict.publishNotRequested')
}
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
