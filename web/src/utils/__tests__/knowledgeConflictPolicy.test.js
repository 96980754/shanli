import assert from 'node:assert/strict'
import {
  KNOWLEDGE_CONFLICT_RESOLUTIONS,
  canRetryKnowledgePublish,
  formatKnowledgeValue,
  knowledgeConflictClassificationLabel,
  knowledgeConflictStatusLabel,
  knowledgePublishStatusLabel
} from '../knowledge_conflict_policy.js'

assert.equal(knowledgeConflictClassificationLabel('DUPLICATE'), '重复')
assert.equal(knowledgeConflictClassificationLabel('COMPLETION'), '补全')
assert.equal(knowledgeConflictClassificationLabel('UPDATE'), '更新')
assert.equal(knowledgeConflictClassificationLabel('CONFLICT'), '冲突')
assert.equal(knowledgeConflictClassificationLabel('LINK_AMBIGUOUS'), '实体待确认')
assert.equal(knowledgeConflictClassificationLabel('INVALID'), '无效')
assert.equal(knowledgeConflictStatusLabel('pending'), '待处理')
assert.equal(formatKnowledgeValue(['Windows', 'Linux']), 'Windows、Linux')
assert.equal(formatKnowledgeValue(100, '人'), '100 人')

assert.notEqual(knowledgePublishStatusLabel('pending'), 'pending')
assert.notEqual(knowledgePublishStatusLabel('processing'), 'processing')
assert.notEqual(knowledgePublishStatusLabel('succeeded'), 'succeeded')
assert.notEqual(knowledgePublishStatusLabel('failed'), 'failed')
assert.notEqual(knowledgePublishStatusLabel('dead_letter'), 'dead_letter')
assert.equal(canRetryKnowledgePublish({ publish_status: 'failed' }, false), true)
assert.equal(canRetryKnowledgePublish({ publish_status: 'dead_letter' }, false), true)
assert.equal(canRetryKnowledgePublish({ publish_status: 'processing' }, false), false)
assert.equal(canRetryKnowledgePublish({ publish_status: 'failed' }, true), false)

const useNew = KNOWLEDGE_CONFLICT_RESOLUTIONS.find((item) => item.value === 'use_new')
assert.ok(useNew)
assert.equal(
  KNOWLEDGE_CONFLICT_RESOLUTIONS.some((item) => item.default),
  false
)

console.log('knowledge conflict policy tests passed')
