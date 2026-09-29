import assert from 'node:assert/strict'

import {
  canEditFlowchartDraft,
  getFlowchartWarningKeys,
  isFlowchartDraftDirty,
  isPdfFlowchartFile
} from '../flowchartReview.js'
import { canParseFile, getFilePrimaryAction, getFileStatusView } from '../knowledge_file_policy.js'

const draft = (status = 'flowchart_waiting_confirmation', extra = {}) => ({
  status,
  readonly: false,
  confirmed_at: null,
  ...extra
})

assert.equal(isPdfFlowchartFile({ name: '流程.pdf', type: 'application/pdf' }), true)
assert.equal(isPdfFlowchartFile({ name: '流程.PDF', type: '' }), true)
assert.equal(isPdfFlowchartFile({ name: '流程.png', type: 'image/png' }), false)
assert.equal(isPdfFlowchartFile({ name: '流程.pdf', type: 'text/plain' }), false)
assert.equal(isPdfFlowchartFile(null), false)

assert.equal(isFlowchartDraftDirty('saved', 'saved'), false)
assert.equal(isFlowchartDraftDirty('saved', 'edited'), true)
assert.equal(isFlowchartDraftDirty('saved', 'saved'), false)

assert.equal(canEditFlowchartDraft(draft(), true), true)
assert.equal(canEditFlowchartDraft(draft('error_flowchart_parsing'), true), true)
assert.equal(canEditFlowchartDraft(draft(), false), false)
assert.equal(canEditFlowchartDraft(draft('flowchart_parsing'), true), false)
assert.equal(canEditFlowchartDraft(draft('flowchart_confirming'), true), false)
assert.equal(canEditFlowchartDraft(draft('indexed', { confirmed_at: '2026-09-23' }), true), false)
assert.equal(
  canEditFlowchartDraft(draft('flowchart_waiting_confirmation', { readonly: true }), true),
  false
)

assert.deepEqual(
  getFlowchartWarningKeys({
    pages: [{ warnings: ['FLOWCHART_RENDER_DPI_REDUCED', 'FLOWCHART_COMPLEX_PAGE'] }]
  }),
  ['flowchart.warningReducedComplex']
)
assert.deepEqual(
  getFlowchartWarningKeys({
    warnings: ['FLOWCHART_RENDER_DPI_REDUCED', 'FLOWCHART_COMPLEX_PAGE'],
    pages: [{ warnings: ['FLOWCHART_EXTREME_ASPECT_RATIO'] }]
  }),
  ['flowchart.warningReducedComplex', 'flowchart.warningAspect']
)
assert.deepEqual(getFlowchartWarningKeys({ warnings: ['FUTURE_WARNING'] }), [])

for (const status of [
  'flowchart_parsing',
  'flowchart_waiting_confirmation',
  'flowchart_confirming',
  'error_flowchart_parsing'
]) {
  assert.notEqual(getFileStatusView(status).label, status)
}
assert.equal(getFilePrimaryAction({ status: 'uploaded', ingestion_type: 'flowchart' }), null)
assert.equal(
  canParseFile({ status: 'uploaded', ingestion_type: 'flowchart', is_current: true }),
  false
)
assert.equal(canParseFile({ status: 'uploaded', is_current: true }), true)

console.log('flowchart review policy tests passed')
