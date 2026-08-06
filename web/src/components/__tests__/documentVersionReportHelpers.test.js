import assert from 'node:assert/strict'

import {
  canReviewValidationReport,
  getChangeTypeView,
  getEvidenceQuote,
  getFactValue,
  getMissingFactText,
  getSideValue
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
  assert.equal(
    canReviewValidationReport({ status: 'published', decision: 'accepted' }, true),
    false
  )
  assert.equal(
    canReviewValidationReport({ status: 'review_required', decision: 'pending' }, false),
    false
  )
  assert.equal(getEvidenceQuote([{ quote: '原文证据' }]), '原文证据')
  assert.equal(getEvidenceQuote([]), '无可用证据原文')

  // 事实值展示：keyed 槽位优先取 target.text（自带槽位名），退回归一化值
  assert.equal(
    getFactValue({ target: { text: '电池容量: 3800mAh' }, normalized_value: '3800mAh' }),
    '电池容量: 3800mAh'
  )
  assert.equal(getFactValue({ normalized_value: 'IP68' }), 'IP68')
  assert.equal(getFactValue(null), '')

  // 空文案按变更场景区分：新增项旧侧 / 删除项新侧
  assert.equal(getMissingFactText('new', 'old'), '旧版无此事实')
  assert.equal(getMissingFactText('removed', 'new'), '新版已删除此事实')
  assert.equal(getMissingFactText('changed', 'old'), '无可用证据原文')
  assert.equal(getMissingFactText('new', 'new'), '无可用证据原文')
  // 报告项单侧值：变更项展示 旧值→新值 对照
  const changed = {
    change_type: 'changed',
    old_fact: { target: { text: '电池容量: 3800mAh' } },
    new_fact: { target: { text: '电池容量: 1200mAh' } }
  }
  assert.equal(getSideValue(changed, 'old'), '电池容量: 3800mAh')
  assert.equal(getSideValue(changed, 'new'), '电池容量: 1200mAh')

  // 新增项旧侧显示"旧版无此事实"而非误导性的"无可用证据原文"
  const added = { change_type: 'new', old_fact: null, new_fact: { normalized_value: '1200mAh' } }
  assert.equal(getSideValue(added, 'old'), '旧版无此事实')
  assert.equal(getSideValue(added, 'new'), '1200mAh')

  // 删除项新侧显示"新版已删除此事实"
  const removed = { change_type: 'removed', old_fact: { normalized_value: 'IP68' }, new_fact: null }
  assert.equal(getSideValue(removed, 'old'), 'IP68')
  assert.equal(getSideValue(removed, 'new'), '新版已删除此事实')

  // 否定不存在事实的冲突项：旧版无此事实，而非误导性的"无可用证据原文"
  const negateMissing = {
    change_type: 'conflict',
    old_fact: null,
    new_fact: { normalized_value: 'x' }
  }
  assert.equal(getSideValue(negateMissing, 'old'), '旧版无此事实')
  assert.equal(getMissingFactText('conflict', 'old'), '旧版无此事实')

  // 证据空文案同样按场景区分
  assert.equal(getEvidenceQuote([], 'new', 'old'), '旧版无此事实')
  assert.equal(getEvidenceQuote([], 'removed', 'new'), '新版已删除此事实')

  console.log('documentVersionReportHelpers: all assertions passed')
}

run()
