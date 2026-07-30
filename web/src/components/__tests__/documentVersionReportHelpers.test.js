import assert from 'node:assert/strict'

import {
  canReviewValidationReport,
  getChangeTypeView,
  getEvidenceQuote
} from '../documentVersionReportHelpers.js'

const run = () => {
  assert.equal(getChangeTypeView('new').label, '新增')
  assert.equal(getChangeTypeView('changed').label, '变更')
  assert.equal(getChangeTypeView('removed').label, '删除')
  assert.equal(getChangeTypeView('conflict').label, '冲突')
  assert.equal(getChangeTypeView('future').label, 'future')

  assert.equal(
    canReviewValidationReport({ status: 'review_required', decision: 'pending' }, true),
    true
  )
  assert.equal(canReviewValidationReport({ status: 'published', decision: 'accepted' }, true), false)
  assert.equal(canReviewValidationReport({ status: 'review_required', decision: 'pending' }, false), false)
  assert.equal(getEvidenceQuote([{ quote: '原文证据' }]), '原文证据')
  assert.equal(getEvidenceQuote([]), '无可用证据原文')

  console.log('documentVersionReportHelpers: all assertions passed')
}

run()
