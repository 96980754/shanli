export const permissionOptions = [
  { key: 'can_view', label: '查看' },
  { key: 'can_search', label: '问答' },
  { key: 'can_upload', label: '上传' },
  { key: 'can_download', label: '下载' },
  { key: 'can_delete', label: '删除' },
  { key: 'can_manage', label: '管理' },
  { key: 'can_grant', label: '授权' },
  { key: 'can_export', label: '导出' }
]

export const permissionKeys = permissionOptions.map((option) => option.key)

export const resetPermissionFlags = (flags = {}) =>
  Object.fromEntries(permissionKeys.map((key) => [key, Boolean(flags[key])]))

export const permissionPresets = {
  readonly: {
    label: '只读',
    flags: resetPermissionFlags({ can_view: true, can_search: true })
  },
  editor: {
    label: '编辑者',
    flags: resetPermissionFlags({
      can_view: true,
      can_search: true,
      can_upload: true,
      can_download: true
    })
  },
  manager: {
    label: '管理者',
    flags: resetPermissionFlags(Object.fromEntries(permissionKeys.map((key) => [key, true])))
  }
}

export const roleOptions = [
  { value: 'admin', label: '管理员' },
  { value: 'user', label: '普通用户' },
  { value: 'superadmin', label: '超级管理员' }
]

export const buildPermissionPayload = (form) => {
  const trimmedSubjectId = String(form.subject_id ?? '').trim()
  const subjectId =
    form.subject_type === 'department' && /^\d+$/.test(trimmedSubjectId)
      ? String(Number(trimmedSubjectId))
      : trimmedSubjectId

  return {
    subject_type: form.subject_type,
    subject_id: subjectId,
    ...resetPermissionFlags(form)
  }
}

export const formatSubjectLabel = (record, { users = [], departments = [] } = {}) => {
  const subjectId = String(record.subject_id ?? '')

  if (record.subject_type === 'user') {
    const user = users.find((item) => String(item.uid) === subjectId)
    return user ? `${user.username}（${subjectId}）` : subjectId
  }

  if (record.subject_type === 'department') {
    const department = departments.find((item) => String(item.id) === subjectId)
    return department ? `${department.name}（${subjectId}）` : subjectId
  }

  if (record.subject_type === 'role') {
    const role = roleOptions.find((item) => item.value === subjectId)
    return role ? `${role.label}（${subjectId}）` : subjectId
  }

  return subjectId
}
