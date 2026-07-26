import assert from 'node:assert/strict'

import { canOpenEnrichment } from '../knowledge_file_policy.js'

assert.equal(canOpenEnrichment({ status: 'indexed', is_folder: false }), true)
assert.equal(canOpenEnrichment({ status: 'error_replacement_cleanup', is_folder: false }), true)
assert.equal(canOpenEnrichment({ status: 'waiting_confirmation', is_folder: false }), false)
assert.equal(canOpenEnrichment({ status: 'indexed', is_folder: true }), false)

console.log('documentEnrichmentPolicy: all assertions passed')
