import assert from 'node:assert/strict'

import {
  buildAccessRows,
  buildPermissionPayload,
  formatSubjectLabel,
  permissionKeys,
  permissionOptions,
  permissionPresets,
  resetPermissionFlags,
  roleOptions
} from '../knowledgePermissionPanelHelpers.js'

const run = () => {
  assert.deepEqual(permissionKeys, [
    'can_view',
    'can_search',
    'can_upload',
    'can_download',
    'can_delete',
    'can_manage',
    'can_grant',
    'can_export'
  ])

  assert.deepEqual(permissionOptions, [
    { key: 'can_view', label: '查看' },
    { key: 'can_search', label: '问答' },
    { key: 'can_upload', label: '上传' },
    { key: 'can_download', label: '下载' },
    { key: 'can_delete', label: '删除' },
    { key: 'can_manage', label: '管理' },
    { key: 'can_grant', label: '授权' },
    { key: 'can_export', label: '导出' }
  ])

  assert.deepEqual(roleOptions, [
    { value: 'admin', label: '管理员' },
    { value: 'user', label: '普通用户' },
    { value: 'superadmin', label: '超级管理员' }
  ])

  assert.deepEqual(permissionPresets.readonly.flags, {
    can_view: true,
    can_search: true,
    can_upload: false,
    can_download: false,
    can_delete: false,
    can_manage: false,
    can_grant: false,
    can_export: false
  })

  assert.deepEqual(permissionPresets.editor.flags, {
    can_view: true,
    can_search: true,
    can_upload: true,
    can_download: true,
    can_delete: false,
    can_manage: false,
    can_grant: false,
    can_export: false
  })

  assert.deepEqual(permissionPresets.manager.flags, {
    can_view: true,
    can_search: true,
    can_upload: true,
    can_download: true,
    can_delete: true,
    can_manage: true,
    can_grant: true,
    can_export: true
  })

  assert.deepEqual(resetPermissionFlags({ can_view: true, can_grant: true }), {
    can_view: true,
    can_search: false,
    can_upload: false,
    can_download: false,
    can_delete: false,
    can_manage: false,
    can_grant: true,
    can_export: false
  })

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'user', subject_id: 'zhangsan' },
      {
        users: [{ uid: 'zhangsan', username: '张三' }],
        departments: []
      }
    ),
    '张三（zhangsan）'
  )

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'department', subject_id: '10' },
      {
        users: [],
        departments: [{ id: 10, name: '研发部' }]
      }
    ),
    '研发部'
  )

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'department', subject_id: '001' },
      {
        users: [],
        departments: [{ id: 1, name: '研发部' }]
      }
    ),
    '001'
  )

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'role', subject_id: 'admin' },
      {
        users: [],
        departments: []
      }
    ),
    '管理员（admin）'
  )

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'user', subject_id: 'unknown-user' },
      {
        users: [{ uid: 'zhangsan', username: '张三' }],
        departments: []
      }
    ),
    'unknown-user'
  )

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'department', subject_id: '999' },
      {
        users: [],
        departments: [{ id: 10, name: '研发部' }]
      }
    ),
    '999'
  )

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'role', subject_id: 'unknown-role' },
      {
        users: [],
        departments: []
      }
    ),
    'unknown-role'
  )

  assert.deepEqual(
    buildPermissionPayload({
      subject_type: 'user',
      subject_id: ' zhangsan ',
      can_view: true,
      can_search: true,
      can_upload: undefined,
      can_download: false,
      can_delete: false,
      can_manage: false,
      can_grant: false,
      can_export: false
    }),
    {
      subject_type: 'user',
      subject_id: 'zhangsan',
      can_view: true,
      can_search: true,
      can_upload: false,
      can_download: false,
      can_delete: false,
      can_manage: false,
      can_grant: false,
      can_export: false
    }
  )

  assert.deepEqual(
    buildPermissionPayload({
      subject_type: 'department',
      subject_id: ' 001 ',
      can_view: true,
      can_search: false,
      can_upload: false,
      can_download: true,
      can_delete: false,
      can_manage: false,
      can_grant: false,
      can_export: false
    }),
    {
      subject_type: 'department',
      subject_id: '1',
      can_view: true,
      can_search: false,
      can_upload: false,
      can_download: true,
      can_delete: false,
      can_manage: false,
      can_grant: false,
      can_export: false
    }
  )

  assert.deepEqual(
    buildPermissionPayload({
      subject_type: 'department',
      subject_id: '',
      can_view: false,
      can_search: false,
      can_upload: false,
      can_download: false,
      can_delete: false,
      can_manage: false,
      can_grant: false,
      can_export: false
    }),
    {
      subject_type: 'department',
      subject_id: '',
      can_view: false,
      can_search: false,
      can_upload: false,
      can_download: false,
      can_delete: false,
      can_manage: false,
      can_grant: false,
      can_export: false
    }
  )

  // 创建者行：全权限、只读、来源「创建者」
  const creatorRows = buildAccessRows({
    createdBy: 'owner',
    shareConfig: {},
    permissions: []
  })
  assert.equal(creatorRows.length, 1)
  assert.equal(creatorRows[0].key, 'user:owner')
  assert.deepEqual(creatorRows[0].sources, ['创建者'])
  assert.equal(creatorRows[0].editable, false)
  assert.equal(creatorRows[0].can_view, true)
  assert.equal(creatorRows[0].can_upload, true)
  assert.equal(creatorRows[0].can_grant, true)
  assert.equal(creatorRows[0].can_export, true)

  // 空 shareConfig（database prop 未加载完）→ 不产生兜底行
  assert.deepEqual(buildAccessRows({ createdBy: null, shareConfig: {} }), [])

  // global 共享设置 → 全体用户伪行，仅 view/search/download
  const globalRows = buildAccessRows({
    createdBy: null,
    shareConfig: { access_level: 'global', department_ids: [], user_uids: [] }
  })
  assert.equal(globalRows.length, 1)
  assert.equal(globalRows[0].key, 'global')
  assert.equal(globalRows[0].label, '全体用户')
  assert.deepEqual(globalRows[0].sources, ['共享设置'])
  assert.equal(globalRows[0].editable, false)
  assert.equal(globalRows[0].can_view, true)
  assert.equal(globalRows[0].can_search, true)
  assert.equal(globalRows[0].can_download, true)
  assert.equal(globalRows[0].can_upload, false)

  // 部门共享设置 + 同部门显式授权 → 单行并集、双来源、可编辑且带 id
  const mergedRows = buildAccessRows({
    createdBy: null,
    shareConfig: { access_level: 'department', department_ids: [10], user_uids: [] },
    permissions: [
      { id: 5, subject_type: 'department', subject_id: '10', can_view: true, can_upload: true }
    ],
    departments: [{ id: 10, name: '研发部' }]
  })
  assert.equal(mergedRows.length, 1)
  assert.equal(mergedRows[0].key, 'department:10')
  assert.equal(mergedRows[0].label, '研发部')
  assert.deepEqual(mergedRows[0].sources, ['共享设置', '授权'])
  assert.equal(mergedRows[0].editable, true)
  assert.equal(mergedRows[0].id, 5)
  assert.equal(mergedRows[0].can_view, true)
  assert.equal(mergedRows[0].can_upload, true)
  assert.equal(mergedRows[0].can_download, true)
  assert.equal(mergedRows[0].can_delete, false)

  // 角色授权解析 label，且默认可编辑
  const roleRows = buildAccessRows({
    createdBy: null,
    shareConfig: {},
    permissions: [{ id: 7, subject_type: 'role', subject_id: 'admin', can_view: true }]
  })
  assert.equal(roleRows.length, 1)
  assert.equal(roleRows[0].label, '管理员（admin）')
  assert.equal(roleRows[0].editable, true)
  assert.equal(roleRows[0].sources.includes('授权'), true)
}

run()
