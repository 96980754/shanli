import assert from 'node:assert/strict'

import {
  DUPLICATE_STRATEGIES,
  buildKnowledgeUploadUrl,
  getDuplicateConflictDetail,
  getDuplicateConflictMessage,
  getInvalidReplacementTargetDetail,
  getSafeUploadErrorMessage,
  isDuplicateStrategyAllowed
} from '../document_duplicate_policy.js'

const exactConflict = {
  detail: {
    code: 'duplicate_conflict',
    conflict_type: 'exact_content',
    message: '服务端内部文案不应直接展示',
    conflicts: [
      {
        file_id: 'file_1',
        filename: 'demo.txt',
        parent_id: 'folder_1',
        display_path: 'test/demo.txt',
        status: 'error_indexing'
      }
    ],
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
  buildKnowledgeUploadUrl('kb/with space', DUPLICATE_STRATEGIES.REPLACE, 'file/1', 'folder/1'),
  '/api/knowledge/files/upload?kb_id=kb%2Fwith+space&duplicate_strategy=replace&replace_file_id=file%2F1&parent_id=folder%2F1'
)

const detail = getDuplicateConflictDetail(exactConflict)
assert.equal(detail.conflict_type, 'exact_content')
assert.equal(
  getDuplicateConflictMessage(detail),
  '该文档已存在于「test/demo.txt」，为避免重复索引，不能再次入库。'
)
assert.equal(isDuplicateStrategyAllowed(detail, DUPLICATE_STRATEGIES.SKIP), true)
assert.equal(isDuplicateStrategyAllowed(detail, DUPLICATE_STRATEGIES.REPLACE), false)
assert.equal(isDuplicateStrategyAllowed(detail, DUPLICATE_STRATEGIES.KEEP_BOTH), false)

assert.equal(
  getDuplicateConflictMessage(getDuplicateConflictDetail(sameNameConflict)),
  '同一目录下已存在同名但内容不同的文件'
)
assert.equal(
  getSafeUploadErrorMessage(exactConflict),
  '该文档已存在于「test/demo.txt」，为避免重复索引，不能再次入库。'
)
const invalidReplacement = {
  detail: { code: 'invalid_replacement_target', message: 'internal detail' }
}
assert.equal(
  getInvalidReplacementTargetDetail(invalidReplacement)?.code,
  'invalid_replacement_target'
)
assert.equal(
  getSafeUploadErrorMessage(invalidReplacement),
  '替换目标不属于当前文件夹或文件名不匹配，请刷新文件列表后重试'
)
assert.equal(
  getSafeUploadErrorMessage({ detail: 'postgresql://user:password@database/internal' }),
  '文件上传失败，请稍后重试'
)
assert.equal(
  getSafeUploadErrorMessage({
    detail: {
      code: 'converter_unavailable',
      message: 'LibreOffice 旧 Office 转换服务未安装或不可用'
    }
  }),
  'LibreOffice 旧 Office 转换服务未安装或不可用'
)
assert.equal(
  getSafeUploadErrorMessage({
    detail: {
      code: 'file_too_large',
      message: 'DOC 文件超过转换大小限制'
    }
  }),
  'DOC 文件超过转换大小限制'
)
assert.equal(
  getSafeUploadErrorMessage({
    detail: {
      code: 'internal_storage_error',
      message: 'minio://private/internal/object'
    }
  }),
  '文件上传失败，请稍后重试'
)

assert.equal(getDuplicateConflictDetail({ detail: 'plain error' }), null)
assert.equal(getDuplicateConflictDetail({ detail: { code: 'duplicate_conflict' } }), null)

console.log('documentDuplicatePolicy: all assertions passed')
