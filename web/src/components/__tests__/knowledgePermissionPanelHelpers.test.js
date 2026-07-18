import assert from 'node:assert/strict'

import {
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
    '研发部（10）'
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
}

run()
