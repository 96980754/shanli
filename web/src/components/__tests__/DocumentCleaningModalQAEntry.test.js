import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const testDirectory = path.dirname(fileURLToPath(import.meta.url))
const componentSource = fs.readFileSync(
  path.join(testDirectory, '..', 'DocumentCleaningModal.vue'),
  'utf8'
)

assert.match(componentSource, /@click="openQA"/)
assert.match(componentSource, /draft\.status === 'waiting_confirmation'/)
assert.match(componentSource, /emit\('open-qa', props\.fileId\)/)
assert.doesNotMatch(componentSource, /@after-open-change="handleOpenChange"/)
assert.match(componentSource, /watch\(\(\) => props\.open, handleOpenChange\)/)

console.log('document cleaning modal QA entry contract tests passed')
