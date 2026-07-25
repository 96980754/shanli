import assert from 'node:assert/strict'

import {
  DUPLICATE_STRATEGIES,
  buildKnowledgeUploadUrl,
  getDuplicateConflictDetail,
  getDuplicateConflictMessage,
  getSafeUploadErrorMessage,
  isDuplicateStrategyAllowed
} from '../document_duplicate_policy.js'

const exactConflict = {
  detail: {
    code: 'duplicate_conflict',
    conflict_type: 'exact_content',
    message: '服务端内部文案不应直接展示',
    conflicts: [{ file_id: 'file_1', filename: 'demo.txt' }],
    allowed_strategies: ['skip']
  }
}

const sameNameConflict = {
  detail: {
    code: 'duplicate_conflict',
    conflict_type: 'same_name',
    message: '同一知识库中已存在同名但内容不同的文件',
    conflicts: [{ file_id: 'file_2', filename: 'demo.txt' }],
    allowed_strategies: ['skip', 'replace', 'keep_both']
  }
}

assert.equal(
  buildKnowledgeUploadUrl('kb-1'),
  '/api/knowledge/files/upload?kb_id=kb-1&duplicate_strategy=prompt'
)

assert.equal(
  buildKnowledgeUploadUrl('kb/with space', DUPLICATE_STRATEGIES.REPLACE, 'file/1'),
  '/api/knowledge/files/upload?kb_id=kb%2Fwith+space&duplicate_strategy=replace&replace_file_id=file%2F1'
)

const detail = getDuplicateConflictDetail(exactConflict)
assert.equal(detail.conflict_type, 'exact_content')
assert.equal(getDuplicateConflictMessage(detail), '检测到重复文档，不能重复上传')
assert.equal(isDuplicateStrategyAllowed(detail, DUPLICATE_STRATEGIES.SKIP), true)
assert.equal(isDuplicateStrategyAllowed(detail, DUPLICATE_STRATEGIES.REPLACE), false)
assert.equal(isDuplicateStrategyAllowed(detail, DUPLICATE_STRATEGIES.KEEP_BOTH), false)

assert.equal(
  getDuplicateConflictMessage(getDuplicateConflictDetail(sameNameConflict)),
  '已存在同名文件，当前暂不支持更新或替换'
)
assert.equal(getSafeUploadErrorMessage(exactConflict), '检测到重复文档，不能重复上传')
assert.equal(
  getSafeUploadErrorMessage({ detail: 'postgresql://user:password@database/internal' }),
  '文件上传失败，请稍后重试'
)

assert.equal(getDuplicateConflictDetail({ detail: 'plain error' }), null)
assert.equal(getDuplicateConflictDetail({ detail: { code: 'duplicate_conflict' } }), null)

console.log('documentDuplicatePolicy: all assertions passed')
