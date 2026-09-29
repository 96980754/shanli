import assert from 'node:assert/strict'

import { canIndexFile, canReparseFile, getFileStatusView } from '../knowledge_file_policy.js'

const run = () => {
  assert.deepEqual(getFileStatusView('conflict_detecting'), {
    label: '冲突检测中',
    tone: 'status-info',
    icon: 'progress'
  })
  assert.deepEqual(getFileStatusView('conflict_clear'), {
    label: '冲突检测通过',
    tone: 'status-success',
    icon: 'success'
  })
  assert.deepEqual(getFileStatusView('conflict_review'), {
    label: '待审核冲突',
    tone: 'status-warning',
    icon: 'clock'
  })
  assert.equal(getFileStatusView('conflict_detection_failed').label, '冲突检测失败')
  assert.equal(getFileStatusView('conflict_inconclusive').label, '冲突证据不足')
  assert.equal(getFileStatusView('version_task_failed').label, '版本更新失败')
  assert.equal(getFileStatusView('validation_processing').label, '知识变更分析中')
  assert.equal(getFileStatusView('validation_review').label, '待审核知识变更')
  assert.equal(getFileStatusView('validation_accepted').label, '知识变更已接受')
  assert.equal(getFileStatusView('validation_failed').label, '知识变更分析失败')
  assert.equal(getFileStatusView('validation_rejected').label, '新版已拒绝')
  assert.equal(getFileStatusView('done').label, '已入库')
  assert.equal(getFileStatusView('flowchart_waiting_confirmation').label, '流程图待确认')
  assert.equal(getFileStatusView('error_flowchart_parsing').label, '流程图解析失败')
  assert.equal(
    canIndexFile({
      status: 'error_indexing',
      ingestion_type: 'flowchart',
      confirmed_at: null,
      is_current: true,
      is_folder: false
    }),
    false
  )
  assert.equal(
    canIndexFile({
      status: 'error_indexing',
      ingestion_type: 'flowchart',
      confirmed_at: '2026-09-23T00:00:00Z',
      is_current: true,
      is_folder: false
    }),
    false
  )
  assert.equal(
    canReparseFile({
      status: 'error_indexing',
      ingestion_type: 'flowchart',
      confirmed_at: '2026-09-23T00:00:00Z',
      is_current: true,
      is_folder: false
    }),
    false
  )
  assert.deepEqual(getFileStatusView('future_status'), {
    label: 'future_status',
    tone: '',
    icon: null
  })

  console.log('knowledge_file_policy: all assertions passed')
}

run()
