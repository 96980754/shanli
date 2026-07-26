import assert from 'node:assert/strict'

import {
  FILE_ACTIONS,
  canOpenCleaning,
  canPreviewChunks,
  canSelectFile,
  getFilePrimaryAction,
  getFileStatusView,
  getProcessingStageLabel
} from '../knowledge_file_policy.js'

const waiting = {
  file_id: 'file-1',
  status: 'waiting_confirmation',
  processing_stage: null,
  is_folder: false
}

assert.deepEqual(getFilePrimaryAction(waiting), {
  type: FILE_ACTIONS.CLEANING,
  label: '确认清洗'
})
assert.equal(getFileStatusView(waiting.status).label, '待确认清洗')
assert.equal(canOpenCleaning(waiting), true)
assert.equal(canPreviewChunks(waiting), false)
assert.equal(canSelectFile(waiting), true)

const cleaning = {
  ...waiting,
  status: 'cleaning',
  processing_stage: 'cleaning'
}
assert.equal(getFileStatusView(cleaning.status).label, '清洗中')
assert.equal(getProcessingStageLabel(cleaning.processing_stage), '清洗中')
assert.equal(canOpenCleaning(cleaning), true)
assert.equal(canSelectFile(cleaning), false)

const indexed = {
  ...waiting,
  status: 'indexed'
}
assert.equal(canOpenCleaning(indexed), true)
assert.equal(canPreviewChunks(indexed), true)

const folder = {
  file_id: 'folder-1',
  status: 'waiting_confirmation',
  is_folder: true
}
assert.equal(canOpenCleaning(folder), false)

console.log('documentCleaningPolicy: all assertions passed')
