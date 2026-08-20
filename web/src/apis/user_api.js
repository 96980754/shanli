import { apiAdminGet, apiPost } from './base'

/**
 * 获取指定部门下的用户（供团队管理选择成员）
 * @param {number} departmentId - 部门ID
 * @returns {Promise<Array>} 用户列表（含 team_id/team_name）
 */
export const getUsersByDepartment = (departmentId) => {
  return apiAdminGet(`/api/auth/users?department_id=${departmentId}&limit=1000`)
}

export const userApi = {
  uploadImage: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return apiPost('/api/user/upload-image', formData)
  },
  getUsersByDepartment
}
