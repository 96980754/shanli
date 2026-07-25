import assert from 'node:assert/strict'

import {
  FILE_ACTIONS,
  canDeleteFile,
  canPreviewChunks,
  getFilePrimaryAction,
  getFileStatusView,
  getProcessingStageLabel
} from '../knowledge_file_policy.js'

const cleanupFailure = {
  file_id: 'new_file',
  status: 'error_replacement_cleanup',
  processing_stage: 'replacement_cleanup',
  processing_progress: 96,
  is_folder: false
}

assert.deepEqual(getFilePrimaryAction(cleanupFailure), {
  type: FILE_ACTIONS.REPLACEMENT_CLEANUP,
  label: '重试版本清理'
})
assert.equal(getFileStatusView(cleanupFailure.status).label, '重试版本清理')
assert.equal(getProcessingStageLabel('verifying'), '验证新向量')
assert.equal(getProcessingStageLabel('replacement_cleanup'), '清理旧版本')
assert.equal(canPreviewChunks(cleanupFailure), true)
assert.equal(canDeleteFile(cleanupFailure), false)

console.log('knowledgeFilePolicyReplacement: all assertions passed')
