import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const testDirectory = path.dirname(fileURLToPath(import.meta.url))
const componentSource = fs.readFileSync(
  path.join(testDirectory, '..', 'DocumentQAModal.vue'),
  'utf8'
)

assert.match(componentSource, /v-if="payload\?\.draft_mode"[\s\S]*文档待确认清洗/)
assert.match(componentSource, /v-if="payload\?\.confirmable"/)
assert.match(
  componentSource,
  /v-if="\s*payload\?\.confirmable\s*&&\s*\(item\.status !== 'confirmed' \|\| item\.sync_status !== 'synced'\)\s*"/
)
assert.match(componentSource, /@click="generate"/)
assert.match(componentSource, /@click="startManual"/)
assert.match(componentSource, /chunk-tags-editor/)
assert.doesNotMatch(componentSource, /@after-open-change="handleOpenChange"/)
assert.match(componentSource, /watch\(\(\) => props\.open, handleOpenChange\)/)

console.log('document QA modal draft-mode contract tests passed')
