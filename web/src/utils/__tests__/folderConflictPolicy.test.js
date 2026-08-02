import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { getFolderCreateErrorMessage, normalizeFolderName } from '../knowledge_file_policy.js'

const testDirectory = path.dirname(fileURLToPath(import.meta.url))
const componentSource = fs.readFileSync(
  path.join(testDirectory, '..', '..', 'components', 'FileTable.vue'),
  'utf8'
)

assert.equal(normalizeFolderName('  Ｔｅｓｔ  '), 'Test')
assert.equal(normalizeFolderName('   '), '')
assert.equal(
  getFolderCreateErrorMessage({
    response: { data: { detail: { code: 'folder_name_conflict' } } }
  }),
  '同一目录下已存在同名文件夹'
)

assert.match(componentSource, /if \(createFolderLoading\.value\) return/)
assert.match(
  componentSource,
  /documentApi\.createFolder\(store\.kbId, folderName, currentParentId\.value\)/
)
assert.match(componentSource, /await handleRefresh\(\)/)
assert.match(componentSource, /getFolderCreateErrorMessage\(error\)/)
assert.doesNotMatch(componentSource, /创建失败:.*error\.message/)

console.log('folder conflict policy tests passed')
