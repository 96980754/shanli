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
    // 部门名唯一，无需展示内部 ID（数字易被误读为成员数）
    return department ? department.name : subjectId
  }

  if (record.subject_type === 'role') {
    const role = roleOptions.find((item) => item.value === subjectId)
    return role ? `${role.label}（${subjectId}）` : subjectId
  }

  return subjectId
}

export const FALLBACK_PERMISSION_FLAGS = resetPermissionFlags({
  can_view: true,
  can_search: true,
  can_download: true
})

const applyFlags = (row, flags = {}) => {
  for (const key of permissionKeys) {
    row[key] = row[key] || Boolean(flags[key])
  }
}

// 汇总创建者、共享设置兜底与显式授权为「有效访问全集」，同一对象按来源取并集去重。
export const buildAccessRows = ({
  createdBy,
  shareConfig,
  permissions = [],
  users = [],
  departments = []
}) => {
  const rows = new Map()

  const getOrCreate = (key, subjectType, subjectId) => {
    if (!rows.has(key)) {
      rows.set(key, {
        key,
        subject_type: subjectType,
        subject_id: subjectId,
        label: '',
        sources: [],
        editable: false,
        id: undefined,
        ...resetPermissionFlags()
      })
    }
    return rows.get(key)
  }

  if (createdBy) {
    const row = getOrCreate(`user:${createdBy}`, 'user', String(createdBy))
    applyFlags(row, Object.fromEntries(permissionKeys.map((key) => [key, true])))
    row.sources.push('创建者')
  }

  if (shareConfig && shareConfig.access_level) {
    if (shareConfig.access_level === 'global') {
      const row = getOrCreate('global', 'global', '')
      applyFlags(row, FALLBACK_PERMISSION_FLAGS)
      row.sources.push('共享设置')
    } else if (shareConfig.access_level === 'department') {
      for (const deptId of shareConfig.department_ids || []) {
        const row = getOrCreate(`department:${deptId}`, 'department', String(deptId))
        applyFlags(row, FALLBACK_PERMISSION_FLAGS)
        row.sources.push('共享设置')
      }
    } else if (shareConfig.access_level === 'user') {
      for (const uid of shareConfig.user_uids || []) {
        const row = getOrCreate(`user:${uid}`, 'user', String(uid))
        applyFlags(row, FALLBACK_PERMISSION_FLAGS)
        row.sources.push('共享设置')
      }
    }
  }

  for (const permission of permissions) {
    const key = `${permission.subject_type}:${permission.subject_id}`
    const row = getOrCreate(key, permission.subject_type, String(permission.subject_id))
    applyFlags(row, permission)
    row.sources.push('授权')
    row.editable = true
    if (row.id == null) row.id = permission.id
  }

  const order = { user: 0, department: 1, role: 2, global: 3 }
  return [...rows.values()]
    .map((row) => ({
      ...row,
      label:
        row.subject_type === 'global'
          ? '全体用户'
          : formatSubjectLabel(row, { users, departments }),
      sources: [...new Set(row.sources)]
    }))
    .sort((a, b) => {
      const aCreator = a.sources.includes('创建者') ? 0 : 1
      const bCreator = b.sources.includes('创建者') ? 0 : 1
      if (aCreator !== bCreator) return aCreator - bCreator
      if (order[a.subject_type] !== order[b.subject_type]) {
        return order[a.subject_type] - order[b.subject_type]
      }
      return a.label.localeCompare(b.label, 'zh-Hans-CN')
    })
}
