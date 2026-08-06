import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const componentSource = readFileSync(join(__dirname, '../FileUploadModal.vue'), 'utf8')

// 重复冲突弹窗状态
assert.match(componentSource, /const duplicateConflictQueue = ref\(\[\]\)/)
assert.match(componentSource, /const duplicateConflictOpen = ref\(false\)/)
assert.match(componentSource, /duplicateConflictIsExact/)

// 上传 URL 使用策略构造
assert.match(componentSource, /buildKnowledgeUploadUrl\(/)
assert.match(componentSource, /duplicate_strategy/)

// 409 重复冲突进入弹窗
assert.match(componentSource, /getDuplicateConflictDetail\(/)
assert.match(componentSource, /enqueueDuplicateConflict\(/)

// 冲突弹窗模板
assert.match(componentSource, /:open="duplicateConflictOpen"/)
assert.match(componentSource, /保留两份/)
assert.match(componentSource, /替换现有文件/)

// 处理函数
assert.match(componentSource, /resolveDuplicateConflict\(/)
assert.match(componentSource, /confirmReplacement/)
assert.match(componentSource, /keepBothDuplicate/)

// 入库时提交策略映射
assert.match(componentSource, /duplicate_strategies\[file_path\]/)
assert.match(componentSource, /duplicate_strategies/)

// 冲突处理逻辑：跳过（exact）走取消路径
assert.match(componentSource, /skipDuplicate/)
assert.match(componentSource, /cancelDuplicateConflict/)

console.log('FileUploadModalDuplicateConflict: all assertions passed')
