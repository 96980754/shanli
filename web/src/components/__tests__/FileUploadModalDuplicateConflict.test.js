import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  DUPLICATE_STRATEGIES,
  buildDuplicateResolution,
  getReplacementInProgressDetail,
  getSafeUploadErrorMessage
} from '../../utils/document_duplicate_policy.js'

const testDirectory = path.dirname(fileURLToPath(import.meta.url))
const componentSource = fs.readFileSync(
  path.join(testDirectory, '..', 'FileUploadModal.vue'),
  'utf8'
)
const databaseStoreSource = fs.readFileSync(
  path.join(testDirectory, '..', '..', 'stores', 'database.js'),
  'utf8'
)

const sameNameConflict = {
  conflict_type: 'same_name',
  incoming: { filename: 'demo.txt', size: 20, content_hash: 'new-hash' },
  conflicts: [
    {
      file_id: 'file_old',
      filename: 'demo.txt',
      size: 10,
      content_hash: 'old-hash',
      status: 'indexed'
    }
  ],
  allowed_strategies: ['skip', 'replace', 'keep_both']
}

assert.deepEqual(buildDuplicateResolution(sameNameConflict, DUPLICATE_STRATEGIES.KEEP_BOTH), {
  duplicateStrategy: 'keep_both',
  replaceFileId: null
})
assert.deepEqual(buildDuplicateResolution(sameNameConflict, DUPLICATE_STRATEGIES.REPLACE), {
  duplicateStrategy: 'replace',
  replaceFileId: 'file_old'
})
assert.equal(buildDuplicateResolution(sameNameConflict, DUPLICATE_STRATEGIES.PROMPT), null)

const replacementConflict = {
  detail: {
    code: 'replacement_in_progress',
    target_file_id: 'file_old',
    candidate_file_id: 'file_candidate'
  }
}
assert.equal(
  getReplacementInProgressDetail(replacementConflict)?.candidate_file_id,
  'file_candidate'
)
assert.equal(getSafeUploadErrorMessage(replacementConflict), '文件正在被其他用户更新，请稍后重试')

assert.match(componentSource, /:open="duplicateConflictOpen"/)
assert.match(componentSource, /duplicateConflictIsExact/)
assert.match(componentSource, /查看已有文件/)
assert.match(componentSource, /display_path/)
assert.match(componentSource, /emit\('view-existing-file', existing\.file_id, existing\)/)
assert.match(componentSource, /const duplicateConflictQueue = ref\(\[\]\)/)
assert.match(componentSource, /@click="cancelDuplicateConflict"/)
assert.match(componentSource, /resolveDuplicateConflict\(DUPLICATE_STRATEGIES\.KEEP_BOTH\)/)
assert.match(componentSource, /confirmReplacement/)
assert.match(componentSource, /:disabled="duplicateConflictPending"/)
assert.match(componentSource, /if \(duplicateConflictPending\.value\) return/)
assert.match(componentSource, /parentId: selectedFolderId\.value/)
assert.match(componentSource, /parent_ids\[file_path\] = file\.response\.parent_id/)
assert.match(componentSource, /emit\('success'\)/)
assert.match(componentSource, /kbId:\s*\{\s*type: String/)
assert.match(componentSource, /props\.kbId \|\| store\.kbId/)
assert.equal(componentSource.match(/databaseId: kbId\.value/g)?.length, 2)
assert.match(databaseStoreSource, /databaseId \|\| kbId\.value/)
assert.match(databaseStoreSource, /documentApi\.addDocuments\(targetKbId,/)

console.log('file upload duplicate conflict component contract tests passed')
