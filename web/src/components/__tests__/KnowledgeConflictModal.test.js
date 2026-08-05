import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { knowledgeConflictClassificationLabel } from '../../utils/knowledge_conflict_policy.js'

const testDirectory = path.dirname(fileURLToPath(import.meta.url))
const componentSource = fs.readFileSync(
  path.join(testDirectory, '..', 'KnowledgeConflictModal.vue'),
  'utf8'
)

assert.doesNotMatch(componentSource, /@after-open-change="handleOpenChange"/)
assert.doesNotMatch(componentSource, /@open-change="handleOpenChange"/)
assert.match(componentSource, /const handleOpenChange = \(open\) => \{\s*if \(open\) load\(\)\s*\}/)
assert.match(componentSource, /watch\(\(\) => props\.open, handleOpenChange\)/)

let loadCalls = 0
const handleOpenChange = (open) => {
  if (open) loadCalls += 1
}

handleOpenChange(false)
assert.equal(loadCalls, 0)
handleOpenChange(true)
assert.equal(loadCalls, 1)
handleOpenChange(false)
assert.equal(loadCalls, 1)

assert.match(componentSource, /knowledgeConflictApi\.list\(props\.kbId, statusFilter\.value\)/)
assert.match(
  componentSource,
  /drafts\[item\.conflict_id\] \|\|= \{ resolution: undefined, reason: '', target_entity_id: '' \}/
)
assert.match(componentSource, /@click="resolve\(item\)"/)
assert.match(componentSource, /knowledgeConflictApi\.resolve\(props\.kbId, item\.conflict_id, \{/)
assert.match(
  componentSource,
  /knowledgeConflictApi\.retryPublish\(props\.kbId, item\.conflict_id\)/
)
assert.match(componentSource, /canRetry\(item, payload\?\.readonly\)/)
assert.match(componentSource, /:loading="retryingId === item\.conflict_id"/)
assert.match(componentSource, /publishStatusLabel\(item\.publish_status\)/)
assert.match(componentSource, /item\.publish_error \|\| undefined/)

assert.match(componentSource, /classificationLabel\(item\.classification\)/)
for (const classification of ['DUPLICATE', 'COMPLETION', 'UPDATE', 'CONFLICT'])
  assert.notEqual(knowledgeConflictClassificationLabel(classification), classification)

console.log('knowledge conflict modal open-event contract tests passed')
