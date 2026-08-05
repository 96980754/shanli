import assert from 'node:assert/strict'

import { canOpenDocumentQA } from '../knowledge_file_policy.js'

assert.equal(canOpenDocumentQA({ status: 'indexed', is_folder: false, is_active: true }), true)
assert.equal(
  canOpenDocumentQA({
    status: 'error_replacement_cleanup',
    is_folder: false,
    is_active: true
  }),
  true
)
assert.equal(
  canOpenDocumentQA({ status: 'waiting_confirmation', is_folder: false, is_active: true }),
  true
)
assert.equal(canOpenDocumentQA({ status: 'confirmed', is_folder: false, is_active: true }), true)
assert.equal(
  canOpenDocumentQA({ status: 'error_indexing', is_folder: false, is_active: true }),
  true
)
assert.equal(canOpenDocumentQA({ status: 'parsed', is_folder: false, is_active: true }), false)
assert.equal(canOpenDocumentQA({ status: 'indexed', is_folder: false, is_active: false }), false)
assert.equal(canOpenDocumentQA({ status: 'indexed', is_folder: true, is_active: true }), false)

console.log('documentQAPolicy: all assertions passed')
