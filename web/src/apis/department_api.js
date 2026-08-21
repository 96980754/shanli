/**
 * 部门管理 API
 */

import {
  apiAdminGet,
  apiAdminPost,
  apiAdminPut,
  apiAdminDelete,
  apiSuperAdminGet,
  apiSuperAdminPost,
  apiSuperAdminPut,
  apiSuperAdminDelete
} from './base'

const BASE_URL = '/api/departments'

/**
 * 获取部门列表（普通管理员可访问）
 * @returns {Promise<Array>} 部门列表
 */
export const getDepartments = () => {
  return apiAdminGet(BASE_URL)
}

/**
 * 获取部门详情
 * @param {number} departmentId - 部门ID
 * @returns {Promise<Object>} 部门详情
 */
export const getDepartment = (departmentId) => {
  return apiSuperAdminGet(`${BASE_URL}/${departmentId}`)
}

/**
 * 创建部门
 * @param {Object} data - 部门数据
 * @param {string} data.name - 部门名称
 * @param {string} [data.description] - 部门描述
 * @returns {Promise<Object>} 创建的部门
 */
export const createDepartment = (data) => {
  return apiSuperAdminPost(BASE_URL, data)
}

/**
 * 更新部门
 * @param {number} departmentId - 部门ID
 * @param {Object} data - 部门数据
 * @param {string} [data.name] - 部门名称
 * @param {string} [data.description] - 部门描述
 * @returns {Promise<Object>} 更新后的部门
 */
export const updateDepartment = (departmentId, data) => {
  return apiSuperAdminPut(`${BASE_URL}/${departmentId}`, data)
}

/**
 * 删除部门
 * @param {number} departmentId - 部门ID
 * @returns {Promise<Object>} 删除结果
 */
export const deleteDepartment = (departmentId) => {
  return apiSuperAdminDelete(`${BASE_URL}/${departmentId}`)
}

/**
 * 获取部门下的团队列表
 * @param {number} departmentId - 部门ID
 * @returns {Promise<Array>} 团队列表
 */
export const getDepartmentTeams = (departmentId) => {
  return apiAdminGet(`${BASE_URL}/${departmentId}/teams`)
}

/**
 * 创建团队
 * @param {number} departmentId - 部门ID
 * @param {Object} data - 团队数据
 * @param {string} data.name - 团队名称
 * @param {string} [data.description] - 团队描述
 * @returns {Promise<Object>} 创建的团队
 */
export const createTeam = (departmentId, data) => {
  return apiAdminPost(`${BASE_URL}/${departmentId}/teams`, data)
}

/**
 * 更新团队
 * @param {number} departmentId - 部门ID
 * @param {number} teamId - 团队ID
 * @param {Object} data - 团队数据
 * @param {string} [data.name] - 团队名称
 * @param {string} [data.description] - 团队描述
 * @returns {Promise<Object>} 更新后的团队
 */
export const updateTeam = (departmentId, teamId, data) => {
  return apiAdminPut(`${BASE_URL}/${departmentId}/teams/${teamId}`, data)
}

/**
 * 删除团队（默认团队拒绝）
 * @param {number} departmentId - 部门ID
 * @param {number} teamId - 团队ID
 * @returns {Promise<Object>} 删除结果
 */
export const deleteTeam = (departmentId, teamId) => {
  return apiAdminDelete(`${BASE_URL}/${departmentId}/teams/${teamId}`)
}

/**
 * 设置团队成员（勾选的用户 id 全集：勾选进团队，取消勾选的原成员回到本部门默认团队）
 * @param {number} departmentId - 部门ID
 * @param {number} teamId - 团队ID
 * @param {Array<number>} userIds - 勾选的用户 id 列表
 * @returns {Promise<Object>} 更新结果
 */
export const updateTeamMembers = (departmentId, teamId, userIds) => {
  return apiAdminPut(`${BASE_URL}/${departmentId}/teams/${teamId}/members`, { user_ids: userIds })
}

/**
 * 获取全部团队（含部门名，供权限面板/级联使用）
 * @returns {Promise<Array>} 团队列表
 */
export const getAllTeams = () => {
  return apiAdminGet('/api/teams')
}

export const departmentApi = {
  getDepartments,
  getDepartment,
  createDepartment,
  updateDepartment,
  deleteDepartment,
  getDepartmentTeams,
  createTeam,
  updateTeam,
  deleteTeam,
  updateTeamMembers,
  getAllTeams
}
