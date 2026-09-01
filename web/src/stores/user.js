import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { i18n } from '@/i18n'
import { useAgentStore } from './agent'

export const useUserStore = defineStore('user', () => {
  // 状态
  const token = ref(localStorage.getItem('user_token') || '')
  const userId = ref(null)
  const username = ref('')
  const uid = ref('')
  const phoneNumber = ref('')
  const avatar = ref('')
  const userRole = ref('')
  const departmentId = ref(null)
  const departmentName = ref('')

  // 计算属性
  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => userRole.value === 'admin' || userRole.value === 'superadmin')
  const isSuperAdmin = computed(() => userRole.value === 'superadmin')
  const isRegularUser = computed(() => userRole.value === 'user')

  // 动作
  async function login(credentials) {
    try {
      const formData = new FormData()
      // 支持uid或phone_number登录
      formData.append('username', credentials.loginId) // 使用loginId作为通用登录标识 // i18n-ignore
      formData.append('password', credentials.password)

      const response = await fetch('/api/auth/token', {
        method: 'POST',
        body: formData
      })

      if (!response.ok) {
        const error = await response.json()

        // 如果是423锁定状态码，抛出包含状态码的错误
        if (response.status === 423) {
          const lockError = new Error(error.detail || i18n.global.t('errors.accountLocked'))
          lockError.status = 423
          lockError.headers = response.headers
          throw lockError
        }

        throw new Error(error.detail || i18n.global.t('auth.loginFailed'))
      }

      const data = await response.json()

      // 更新状态
      token.value = data.access_token
      userId.value = data.user_id
      username.value = data.username
      uid.value = data.uid
      phoneNumber.value = data.phone_number || ''
      avatar.value = data.avatar || ''
      userRole.value = data.role
      departmentId.value = data.department_id || null
      departmentName.value = data.department_name || ''

      // 只保存 token 到本地存储
      localStorage.setItem('user_token', data.access_token)

      return true
    } catch (error) {
      console.error('登录错误:', error) // i18n-ignore
      throw error
    }
  }

  function logout() {
    // 清除状态
    token.value = ''
    userId.value = null
    username.value = ''
    uid.value = ''
    phoneNumber.value = ''
    avatar.value = ''
    userRole.value = ''
    departmentId.value = null
    departmentName.value = ''

    // 清除 agentStore 状态，确保重新登录时能正确加载数据
    const agentStore = useAgentStore()
    agentStore.reset()

    // 只清除 token
    localStorage.removeItem('user_token')
  }

  async function initialize(admin) {
    try {
      const response = await fetch('/api/auth/initialize', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(admin)
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || i18n.global.t('errors.initAdminFailed'))
      }

      const data = await response.json()

      // 更新状态
      token.value = data.access_token
      userId.value = data.user_id
      username.value = data.username
      uid.value = data.uid
      phoneNumber.value = data.phone_number || ''
      avatar.value = data.avatar || ''
      userRole.value = data.role
      departmentId.value = data.department_id || null
      departmentName.value = data.department_name || ''

      // 只保存 token 到本地存储
      localStorage.setItem('user_token', data.access_token)

      return true
    } catch (error) {
      console.error('初始化管理员错误:', error) // i18n-ignore
      throw error
    }
  }

  async function checkFirstRun() {
    try {
      const response = await fetch('/api/auth/check-first-run')
      const data = await response.json()
      return data.first_run
    } catch (error) {
      console.error('检查首次运行状态错误:', error) // i18n-ignore
      return false
    }
  }

  // 用于API请求的授权头
  function getAuthHeaders() {
    return {
      Authorization: `Bearer ${token.value}`
    }
  }

  // 用户管理功能
  async function getUsers({ pageSize = 100 } = {}) {
    try {
      const users = []
      let skip = 0

      while (true) {
        const params = new URLSearchParams({
          skip: String(skip),
          limit: String(pageSize)
        })
        const response = await fetch(`/api/auth/users?${params.toString()}`, {
          headers: {
            ...getAuthHeaders()
          }
        })

        if (!response.ok) {
          throw new Error(i18n.global.t('errors.getUsersFailed'))
        }

        const batch = await response.json()
        users.push(...batch)

        if (batch.length < pageSize) {
          break
        }

        skip += pageSize
      }

      return users
    } catch (error) {
      console.error('获取用户列表错误:', error) // i18n-ignore
      throw error
    }
  }

  async function createUser(userData) {
    try {
      const response = await fetch('/api/auth/users', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(userData)
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || i18n.global.t('errors.createUserFailed'))
      }

      return await response.json()
    } catch (error) {
      console.error('创建用户错误:', error) // i18n-ignore
      throw error
    }
  }

  async function updateUser(userId, userData) {
    try {
      const response = await fetch(`/api/auth/users/${userId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(userData)
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || i18n.global.t('errors.updateUserFailed'))
      }

      return await response.json()
    } catch (error) {
      console.error('更新用户错误:', error) // i18n-ignore
      throw error
    }
  }

  async function deleteUser(userId) {
    try {
      const response = await fetch(`/api/auth/users/${userId}`, {
        method: 'DELETE',
        headers: {
          ...getAuthHeaders()
        }
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || i18n.global.t('errors.deleteUserFailed'))
      }

      return await response.json()
    } catch (error) {
      console.error('删除用户错误:', error) // i18n-ignore
      throw error
    }
  }

  // 验证用户名并生成uid
  async function validateUsernameAndGenerateUid(username) {
    try {
      const response = await fetch('/api/auth/validate-username', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify({ username })
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || i18n.global.t('errors.validateUsernameFailed'))
      }

      return await response.json()
    } catch (error) {
      console.error('用户名验证错误:', error) // i18n-ignore
      throw error
    }
  }

  // 上传头像
  async function uploadAvatar(file) {
    try {
      const formData = new FormData()
      formData.append('file', file)

      const response = await fetch('/api/auth/upload-avatar', {
        method: 'POST',
        headers: {
          ...getAuthHeaders()
        },
        body: formData
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || i18n.global.t('errors.uploadAvatarFailed'))
      }

      const data = await response.json()

      // 更新本地头像状态
      avatar.value = data.avatar_url

      return data
    } catch (error) {
      console.error('头像上传错误:', error) // i18n-ignore
      throw error
    }
  }

  // 获取当前用户信息
  async function getCurrentUser() {
    try {
      const response = await fetch('/api/auth/me', {
        headers: {
          ...getAuthHeaders()
        }
      })

      if (!response.ok) {
        throw new Error(i18n.global.t('errors.getUserInfoFailed'))
      }

      const userData = await response.json()

      // 更新本地状态
      userId.value = userData.id
      username.value = userData.username
      uid.value = userData.uid
      phoneNumber.value = userData.phone_number || ''
      avatar.value = userData.avatar || ''
      userRole.value = userData.role
      departmentId.value = userData.department_id || null
      departmentName.value = userData.department_name || ''

      return userData
    } catch (error) {
      console.error('获取用户信息错误:', error) // i18n-ignore
      throw error
    }
  }

  // 更新个人资料
  async function updateProfile(profileData) {
    try {
      const response = await fetch('/api/auth/profile', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(profileData)
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || i18n.global.t('errors.updateProfileFailed'))
      }

      const userData = await response.json()

      // 更新本地状态
      if (typeof userData.username === 'string') {
        username.value = userData.username
      }
      if (typeof userData.phone_number !== 'undefined') {
        phoneNumber.value = userData.phone_number || ''
      }

      return userData
    } catch (error) {
      console.error('更新个人资料错误:', error) // i18n-ignore
      throw error
    }
  }

  return {
    // 状态
    token,
    userId,
    username,
    uid,
    phoneNumber,
    avatar,
    userRole,
    departmentId,
    departmentName,

    // 计算属性
    isLoggedIn,
    isAdmin,
    isSuperAdmin,
    isRegularUser,

    // 方法
    login,
    logout,
    initialize,
    checkFirstRun,
    getAuthHeaders,
    getUsers,
    createUser,
    updateUser,
    deleteUser,
    validateUsernameAndGenerateUid,
    uploadAvatar,
    getCurrentUser,
    updateProfile
  }
})

// 检查当前用户是否有管理员权限
export const checkAdminPermission = () => {
  const userStore = useUserStore()
  if (!userStore.isAdmin) {
    throw new Error(i18n.global.t('errors.adminPermissionRequired'))
  }
  return true
}

// 检查当前用户是否有超级管理员权限
export const checkSuperAdminPermission = () => {
  const userStore = useUserStore()
  if (!userStore.isSuperAdmin) {
    throw new Error(i18n.global.t('errors.superAdminPermissionRequired'))
  }
  return true
}
