<template>
  <div class="user-management">
    <!-- 头部区域 -->
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">{{ $t('userMgmt.title') }}</div>
        <p class="section-description">{{ $t('userMgmt.description') }}</p>
      </div>
      <div class="header-actions">
        <a-button
          @click="handleRefresh"
          :loading="userManagement.refreshing"
          :title="$t('common.refresh')"
          class="refresh-btn lucide-icon-btn"
        >
          <template #icon>
            <RefreshCw :size="16" :class="{ spin: userManagement.refreshing }" />
          </template>
        </a-button>
        <a-button @click="openImportModal" class="lucide-icon-btn">
          <template #icon><FileUp :size="16" /></template>
          {{ $t('userMgmt.importUsers') }}
        </a-button>
        <a-button type="primary" @click="showAddUserModal" class="add-btn lucide-icon-btn">
          <template #icon><Plus :size="16" /></template>
          {{ $t('userMgmt.addUser') }}
        </a-button>
      </div>
    </div>

    <div class="filter-section">
      <a-input
        v-model:value="userManagement.searchKeyword"
        class="search-input"
        :placeholder="$t('userMgmt.searchPlaceholder')"
        allow-clear
      >
        <template #prefix><Search :size="16" /></template>
      </a-input>
      <div class="filter-actions">
        <a-select v-model:value="userManagement.departmentFilter" class="filter-select">
          <a-select-option value="">{{ $t('userMgmt.allDepartments') }}</a-select-option>
          <a-select-option
            v-for="dept in departmentFilterOptions"
            :key="dept.value"
            :value="dept.value"
          >
            {{ dept.label }}
          </a-select-option>
        </a-select>
        <a-select v-model:value="userManagement.roleFilter" class="filter-select">
          <a-select-option value="">{{ $t('userMgmt.allRoles') }}</a-select-option>
          <a-select-option value="superadmin">{{ $t('user.role.superadmin') }}</a-select-option>
          <a-select-option value="admin">{{ $t('user.role.admin') }}</a-select-option>
          <a-select-option value="user">{{ $t('user.role.user') }}</a-select-option>
        </a-select>
      </div>
    </div>

    <!-- 主内容区域 -->
    <div class="content-section">
      <a-spin :spinning="userManagement.loading">
        <div v-if="userManagement.error" class="error-message">
          <a-alert type="error" :message="userManagement.error" show-icon />
        </div>

        <div class="cards-container">
          <div v-if="filteredUsers.length === 0" class="empty-state">
            <a-empty
              :description="
                userManagement.users.length === 0
                  ? $t('userMgmt.emptyNoUsers')
                  : $t('userMgmt.emptyNoMatch')
              "
            />
          </div>
          <div v-else class="user-cards-grid">
            <InfoCard
              v-for="user in paginatedUsers"
              :key="user.id"
              :title="user.username"
              :subtitle="`ID: ${user.uid || '-'}`"
              class="user-card"
            >
              <template #icon>
                <FallbackAvatar
                  :src="user.avatar"
                  :default-src="getUserDefaultAvatarSrc(user)"
                  :name="user.username"
                  :seed="user.uid || user.username"
                  kind="user"
                  :size="40"
                  shape="circle"
                  :alt="user.username"
                  class="avatar-img"
                />
              </template>

              <template #status>
                <div
                  v-if="user.role === 'admin' || user.role === 'superadmin' || user.department_name"
                  class="role-dept-badge"
                >
                  <span class="role-icon-wrapper" :class="getRoleClass(user.role)">
                    <UserLock v-if="user.role === 'superadmin'" :size="14" />
                    <UserStar v-else-if="user.role === 'admin'" :size="14" />
                    <User v-else :size="14" />
                  </span>
                  <span v-if="user.department_name" class="dept-text">
                    {{ user.department_name
                    }}<template v-if="user.team_name"> › {{ user.team_name }}</template>
                  </span>
                </div>
              </template>

              <template #card-more-action-corner>
                <a-menu>
                  <a-menu-item key="edit" @click.stop="showEditUserModal(user)">
                    <span class="lucide-menu-item">
                      <SquarePen :size="14" />
                      <span>{{ $t('userMgmt.editUser') }}</span>
                    </span>
                  </a-menu-item>
                  <a-menu-item
                    key="delete"
                    :disabled="isUserDeleteDisabled(user)"
                    :danger="!isUserDeleteDisabled(user)"
                    @click.stop="confirmDeleteUser(user)"
                  >
                    <span class="lucide-menu-item">
                      <Trash2 :size="14" />
                      <span>{{ $t('userMgmt.deleteUser') }}</span>
                    </span>
                  </a-menu-item>
                </a-menu>
              </template>

              <template #info>
                <div class="card-content">
                  <div class="info-item">
                    <span class="info-label">{{ $t('userMgmt.phoneInfo') }}</span>
                    <span class="info-value phone-text">{{ user.phone_number || '-' }}</span>
                  </div>
                  <div class="info-item">
                    <span class="info-label">{{ $t('userMgmt.createdAtInfo') }}</span>
                    <span class="info-value time-text">{{ formatTime(user.created_at) }}</span>
                  </div>
                  <div class="info-item">
                    <span class="info-label">{{ $t('userMgmt.lastLoginInfo') }}</span>
                    <span class="info-value time-text">{{ formatTime(user.last_login) }}</span>
                  </div>
                </div>
              </template>
            </InfoCard>
          </div>
          <div v-if="filteredUsers.length > userManagement.pageSize" class="pagination-section">
            <a-pagination
              v-model:current="userManagement.currentPage"
              v-model:page-size="userManagement.pageSize"
              :total="filteredUsers.length"
              :page-size-options="['20', '50', '100']"
              show-size-changer
              size="small"
            />
          </div>
        </div>
      </a-spin>
    </div>

    <!-- 用户表单模态框 -->
    <a-modal
      v-model:open="userManagement.modalVisible"
      :title="userManagement.modalTitle"
      @ok="handleUserFormSubmit"
      :confirmLoading="userManagement.loading"
      @cancel="userManagement.modalVisible = false"
      :maskClosable="false"
      width="480px"
      class="user-modal"
    >
      <a-form layout="vertical" class="user-form">
        <a-form-item :label="$t('login.label.uid')" required class="form-item">
          <a-input
            v-model:value="userManagement.form.username"
            :placeholder="$t('userMgmt.usernamePlaceholder')"
            @blur="validateAndGenerateUid"
            :maxlength="20"
          />
          <div v-if="userManagement.form.usernameError" class="error-text">
            {{ userManagement.form.usernameError }}
          </div>
          <div
            v-if="userManagement.form.generatedUid && !userManagement.editMode"
            class="help-text"
          >
            {{ $t('userMgmt.uidGenerated', { uid: userManagement.form.generatedUid }) }}
          </div>
        </a-form-item>

        <!-- 手机号字段 -->
        <a-form-item :label="$t('login.label.phone')" class="form-item">
          <a-input
            v-model:value="userManagement.form.phoneNumber"
            :placeholder="$t('userMgmt.phonePlaceholder')"
            :maxlength="11"
          />
          <div v-if="userManagement.form.phoneError" class="error-text">
            {{ userManagement.form.phoneError }}
          </div>
        </a-form-item>

        <template v-if="userManagement.editMode">
          <div class="password-toggle">
            <a-checkbox v-model:checked="userManagement.displayPasswordFields">
              {{ $t('userMgmt.changePassword') }}
            </a-checkbox>
          </div>
        </template>

        <template v-if="!userManagement.editMode || userManagement.displayPasswordFields">
          <!-- 编辑模式勾选「修改密码」时是重置密码语义，标签/占位符用「新密码」提示，避免与创建时的初始密码混淆 -->
          <a-form-item
            :label="t(userManagement.editMode ? 'userMgmt.newPassword' : 'login.label.password')"
            required
            class="form-item"
          >
            <a-input-password
              v-model:value="userManagement.form.password"
              :placeholder="
                t(
                  userManagement.editMode
                    ? 'userMgmt.newPasswordPlaceholder'
                    : 'login.placeholder.password'
                )
              "
            />
          </a-form-item>

          <a-form-item
            :label="
              t(
                userManagement.editMode
                  ? 'userMgmt.confirmNewPassword'
                  : 'login.label.confirmPassword'
              )
            "
            required
            class="form-item"
          >
            <a-input-password
              v-model:value="userManagement.form.confirmPassword"
              :placeholder="
                t(
                  userManagement.editMode
                    ? 'userMgmt.repeatNewPasswordPlaceholder'
                    : 'login.validation.confirmRequired'
                )
              "
            />
          </a-form-item>
        </template>

        <a-form-item
          v-if="userManagement.editMode && userManagement.form.role === 'superadmin'"
          :label="$t('userMgmt.role')"
          class="form-item"
        >
          <a-input :value="$t('user.role.superadmin')" disabled />
          <div class="help-text">{{ $t('userMgmt.superadminRoleFixed') }}</div>
        </a-form-item>
        <a-form-item v-else :label="$t('userMgmt.role')" class="form-item">
          <a-select v-model:value="userManagement.form.role">
            <a-select-option value="user">{{ $t('user.role.user') }}</a-select-option>
            <a-select-option value="admin" v-if="userStore.isSuperAdmin">
              {{ $t('user.role.admin') }}
            </a-select-option>
          </a-select>
        </a-form-item>

        <!-- 部门选择器（仅超级管理员可见） -->
        <a-form-item v-if="userStore.isSuperAdmin" :label="$t('userMgmt.department')" class="form-item">
          <a-select
            v-model:value="userManagement.form.departmentId"
            :placeholder="$t('userMgmt.selectDepartment')"
          >
            <a-select-option
              v-for="dept in departmentManagement.departments"
              :key="dept.id"
              :value="dept.id"
            >
              {{ dept.name }}
            </a-select-option>
          </a-select>
        </a-form-item>

        <!-- 团队选择器（仅超级管理员可见，随部门级联） -->
        <a-form-item v-if="userStore.isSuperAdmin" :label="$t('userMgmt.team')" class="form-item">
          <a-select
            v-model:value="userManagement.form.teamId"
            :placeholder="$t('userMgmt.selectTeam')"
            allow-clear
            :disabled="!userManagement.form.departmentId"
          >
            <a-select-option v-for="team in filteredTeams" :key="team.id" :value="team.id">
              {{ team.name }}
            </a-select-option>
          </a-select>
          <div
            v-if="userManagement.form.departmentId && !userManagement.form.teamId"
            class="help-text"
          >
            {{ $t('userMgmt.teamDefaultHint') }}
          </div>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="userImport.visible"
      :title="$t('userMgmt.importEnterpriseUsers')"
      width="760px"
      :confirm-loading="userImport.importing"
      :ok-button-props="{ disabled: !userImport.preview?.valid || userImport.validating }"
      :ok-text="$t('userMgmt.confirmImport')"
      :cancel-text="$t('common.cancel')"
      :mask-closable="false"
      @ok="confirmUserImport"
      @cancel="closeImportModal"
    >
      <a-alert
        type="warning"
        show-icon
        :message="$t('userMgmt.excelWarning')"
        class="import-alert"
      />
      <div class="import-toolbar">
        <a-button @click="downloadImportTemplate">{{ $t('userMgmt.downloadTemplate') }}</a-button>
        <a-upload
          :file-list="userImport.file ? [userImport.file] : []"
          :before-upload="selectImportFile"
          :show-upload-list="false"
          accept=".xlsx"
          :disabled="userImport.validating || userImport.importing"
        >
          <a-button :loading="userImport.validating">{{ $t('userMgmt.selectExcel') }}</a-button>
        </a-upload>
        <span v-if="userImport.file" class="import-filename">{{ userImport.file.name }}</span>
      </div>

      <template v-if="userImport.preview">
        <a-alert
          v-if="!userImport.preview.valid"
          type="error"
          show-icon
          :message="
            $t('userMgmt.importErrors', {
              rows: userImport.preview.row_count,
              problems: userImport.preview.errors.length
            })
          "
          class="import-alert"
        />
        <a-alert
          v-else
          type="success"
          show-icon
          :message="
            $t('userMgmt.importValid', { count: userImport.preview.row_count })
          "
          class="import-alert"
        />

        <a-table
          v-if="userImport.preview.errors.length"
          :data-source="userImport.preview.errors"
          :columns="importErrorColumns"
          :pagination="{ pageSize: 8 }"
          row-key="excel_row"
          size="small"
        />
        <a-table
          v-else
          :data-source="userImport.preview.rows"
          :columns="importPreviewColumns"
          :pagination="{ pageSize: 8 }"
          row-key="excel_row"
          size="small"
        />
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { reactive, onMounted, watch, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { message, Modal } from 'ant-design-vue'
import { useUserStore } from '@/stores/user'
import { departmentApi, authApi } from '@/apis'
import {
  Plus,
  SquarePen,
  Trash2,
  User,
  UserLock,
  UserStar,
  RefreshCw,
  Search,
  FileUp
} from 'lucide-vue-next'
import { formatDateTime } from '@/utils/time'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import InfoCard from '@/components/shared/InfoCard.vue'

const { t } = useI18n()
const userStore = useUserStore()

// 用户管理相关状态
const userManagement = reactive({
  loading: false,
  refreshing: false,
  users: [],
  searchKeyword: '',
  departmentFilter: '',
  roleFilter: '',
  currentPage: 1,
  pageSize: 50,
  error: null,
  modalVisible: false,
  modalTitle: t('userMgmt.addUser'),
  editMode: false,
  editUserId: null,
  form: {
    username: '',
    generatedUid: '', // 自动生成的uid
    phoneNumber: '', // 手机号
    password: '',
    confirmPassword: '',
    role: 'user', // 默认角色
    departmentId: null, // 部门ID
    teamId: null, // 团队ID（随部门级联）
    usernameError: '', // 用户名错误信息
    phoneError: '' // 手机号错误信息
  },
  displayPasswordFields: true // 编辑时是否显示密码字段
})

// 部门列表（仅超级管理员使用）
const departmentManagement = reactive({
  departments: []
})

// 全部团队（仅超级管理员使用，随部门级联筛选）
const teamManagement = reactive({
  teams: []
})

const filteredTeams = computed(() => {
  const departmentId = userManagement.form.departmentId
  if (departmentId == null) return []
  return teamManagement.teams.filter((team) => String(team.department_id) === String(departmentId))
})

const userImport = reactive({
  visible: false,
  file: null,
  preview: null,
  validating: false,
  importing: false
})

const importErrorColumns = computed(() => [
  { title: t('userMgmt.excelRow'), dataIndex: 'excel_row', width: 90 },
  { title: t('userMgmt.column'), dataIndex: 'column', width: 150 },
  { title: t('userMgmt.problem'), dataIndex: 'message' }
])

const importPreviewColumns = computed(() => [
  { title: t('userMgmt.row'), dataIndex: 'excel_row', width: 70 },
  { title: t('login.label.uid'), dataIndex: 'username' },
  { title: 'UID', dataIndex: 'uid' },
  { title: t('login.label.phone'), dataIndex: 'phone_number' },
  { title: t('userMgmt.role'), dataIndex: 'role', width: 90 },
  { title: t('userMgmt.department'), dataIndex: 'department_name' }
])

const departmentFilterOptions = computed(() => {
  const options = new Map()

  departmentManagement.departments.forEach((dept) => {
    options.set(String(dept.id), {
      value: String(dept.id),
      label: dept.name
    })
  })

  userManagement.users.forEach((user) => {
    const departmentId = user.department_id
    const departmentName = user.department_name

    if (departmentId == null && !departmentName) return

    const value = String(departmentId ?? departmentName)

    if (!options.has(value)) {
      options.set(value, {
        value,
        label: departmentName || t('userMgmt.departmentId', { id: departmentId })
      })
    }
  })

  return [...options.values()]
})

const filteredUsers = computed(() => {
  const keyword = userManagement.searchKeyword.trim().toLowerCase()

  return userManagement.users.filter((user) => {
    const matchesKeyword =
      !keyword ||
      [user.username, user.uid, user.phone_number].some((value) =>
        String(value || '')
          .toLowerCase()
          .includes(keyword)
      )
    const matchesDepartment =
      !userManagement.departmentFilter ||
      String(user.department_id ?? user.department_name ?? '') === userManagement.departmentFilter
    const matchesRole = !userManagement.roleFilter || user.role === userManagement.roleFilter

    return matchesKeyword && matchesDepartment && matchesRole
  })
})

const paginatedUsers = computed(() => {
  const pageSize = Number(userManagement.pageSize)
  const start = (userManagement.currentPage - 1) * pageSize
  return filteredUsers.value.slice(start, start + pageSize)
})

// 获取部门列表
const fetchDepartments = async () => {
  if (!userStore.isSuperAdmin) return // 普通管理员不需要获取所有部门列表
  try {
    const [departments, teams] = await Promise.all([
      departmentApi.getDepartments(),
      departmentApi.getAllTeams()
    ])
    departmentManagement.departments = departments
    teamManagement.teams = teams
  } catch (error) {
    console.error(t('userMgmt.fetchDeptTeamFailed'), error)
  }
}

// 添加验证用户名并生成uid的函数
const validateAndGenerateUid = async () => {
  const username = userManagement.form.username.trim()

  // 清空之前的错误和生成的ID
  userManagement.form.usernameError = ''
  userManagement.form.generatedUid = ''

  if (!username) {
    return
  }

  // 在编辑模式下，不需要重新生成uid
  if (userManagement.editMode) {
    return
  }

  try {
    const result = await userStore.validateUsernameAndGenerateUid(username)
    userManagement.form.generatedUid = result.uid
  } catch (error) {
    userManagement.form.usernameError = error.message || t('userMgmt.usernameValidateFailed')
  }
}

// 验证手机号格式
const validatePhoneNumber = (phone) => {
  if (!phone) {
    return true // 手机号可选
  }

  // 中国大陆手机号格式验证
  const phoneRegex = /^1[3-9]\d{9}$/
  return phoneRegex.test(phone)
}

// 监听密码字段显示状态变化
watch(
  () => userManagement.displayPasswordFields,
  (newVal) => {
    // 当取消显示密码字段时，清空密码输入
    if (!newVal) {
      userManagement.form.password = ''
      userManagement.form.confirmPassword = ''
    }
  }
)

// 监听手机号输入变化
watch(
  () => userManagement.form.phoneNumber,
  (newPhone) => {
    userManagement.form.phoneError = ''

    if (newPhone && !validatePhoneNumber(newPhone)) {
      userManagement.form.phoneError = t('userMgmt.phoneFormatInvalid')
    }
  }
)

watch(
  () => [userManagement.searchKeyword, userManagement.departmentFilter, userManagement.roleFilter],
  () => {
    userManagement.currentPage = 1
  }
)

// 部门变化时重置团队（防止遗留跨部门的团队选择）
watch(
  () => userManagement.form.departmentId,
  () => {
    userManagement.form.teamId = null
  }
)

watch(
  () => filteredUsers.value.length,
  (total) => {
    const maxPage = Math.max(1, Math.ceil(total / Number(userManagement.pageSize)))
    if (userManagement.currentPage > maxPage) {
      userManagement.currentPage = maxPage
    }
  }
)

// 格式化时间显示
const formatTime = (timeStr) => formatDateTime(timeStr)

const getUserDefaultAvatarSrc = (user) => (user.uid ? generatePixelAvatar(user.uid) : '')

const isUserDeleteDisabled = (user) =>
  user.id === userStore.userId ||
  (user.role === 'superadmin' && userStore.userRole !== 'superadmin')

// 获取用户列表
const fetchUsers = async () => {
  try {
    userManagement.loading = true
    const users = await userStore.getUsers()
    userManagement.users = users
    userManagement.error = null
  } catch (error) {
    console.error(t('userMgmt.fetchUsersFailedLog'), error)
    userManagement.error = t('userMgmt.fetchUsersFailed')
  } finally {
    userManagement.loading = false
  }
}

// 刷新用户和部门信息
const handleRefresh = async () => {
  if (userManagement.refreshing) return
  userManagement.refreshing = true
  try {
    await Promise.all([fetchUsers(), fetchDepartments()])
    message.success(t('settings.refreshSuccess'))
  } catch (error) {
    console.error(t('settings.refreshFail'), error)
    message.error(t('settings.refreshFail'))
  } finally {
    userManagement.refreshing = false
  }
}

const openImportModal = () => {
  userImport.visible = true
  userImport.file = null
  userImport.preview = null
}

const closeImportModal = () => {
  if (userImport.validating || userImport.importing) return
  userImport.visible = false
  userImport.file = null
  userImport.preview = null
}

const downloadImportTemplate = async () => {
  try {
    await authApi.downloadUserImportTemplate()
  } catch (error) {
    message.error(error.message || t('userMgmt.downloadTemplateFailed'))
  }
}

const selectImportFile = async (file) => {
  if (!file.name.toLowerCase().endsWith('.xlsx')) {
    message.error(t('userMgmt.excelFormatOnly'))
    return false
  }
  userImport.file = file
  userImport.preview = null
  userImport.validating = true
  try {
    userImport.preview = await authApi.previewUserImport(file)
  } catch (error) {
    message.error(error.message || t('userMgmt.importValidateFailed'))
  } finally {
    userImport.validating = false
  }
  return false
}

const confirmUserImport = async () => {
  if (!userImport.file || !userImport.preview?.valid) return
  userImport.importing = true
  try {
    const result = await authApi.importUsers(userImport.file)
    message.success(t('userMgmt.importedCount', { count: result.imported_count }))
    userImport.visible = false
    userImport.file = null
    userImport.preview = null
    await fetchUsers()
  } catch (error) {
    const detail = error.response?.data?.detail
    if (detail && typeof detail === 'object' && Array.isArray(detail.errors)) {
      userImport.preview = {
        valid: false,
        row_count: detail.row_count || 0,
        rows: [],
        errors: detail.errors,
        errors_truncated: detail.errors_truncated || false
      }
    }
    message.error(error.message || t('userMgmt.importFailed'))
  } finally {
    userImport.importing = false
  }
}

// 打开添加用户模态框
const showAddUserModal = () => {
  userManagement.modalTitle = t('userMgmt.addUser')
  userManagement.editMode = false
  userManagement.editUserId = null
  userManagement.form = {
    username: '',
    generatedUid: '',
    phoneNumber: '',
    password: '',
    confirmPassword: '',
    role: 'user', // 默认角色为普通用户
    departmentId: null,
    teamId: null,
    usernameError: '',
    phoneError: ''
  }
  userManagement.displayPasswordFields = true
  userManagement.modalVisible = true
}

// 打开编辑用户模态框
const showEditUserModal = (user) => {
  userManagement.modalTitle = t('userMgmt.editUser')
  userManagement.editMode = true
  userManagement.editUserId = user.id
  userManagement.form = {
    username: user.username,
    generatedUid: user.uid || '', // 编辑模式显示现有的uid
    phoneNumber: user.phone_number || '',
    password: '',
    confirmPassword: '',
    role: user.role,
    departmentId: user.department_id || null,
    teamId: user.team_id || null,
    usernameError: '',
    phoneError: ''
  }
  userManagement.displayPasswordFields = false // 默认不显示密码字段
  userManagement.modalVisible = true
}

// 处理用户表单提交
const handleUserFormSubmit = async () => {
  try {
    // 简单验证
    if (!userManagement.form.username.trim()) {
      message.error(t('userMgmt.usernameRequired'))
      return
    }

    // 验证用户名长度
    if (
      userManagement.form.username.trim().length < 2 ||
      userManagement.form.username.trim().length > 20
    ) {
      message.error(t('userMgmt.usernameLengthInvalid'))
      return
    }

    // 验证手机号
    if (userManagement.form.phoneNumber && !validatePhoneNumber(userManagement.form.phoneNumber)) {
      message.error(t('userMgmt.phoneFormatInvalid'))
      return
    }

    if (userManagement.displayPasswordFields) {
      if (!userManagement.form.password) {
        message.error(t('userMgmt.passwordRequired'))
        return
      }

      if (userManagement.form.password !== userManagement.form.confirmPassword) {
        message.error(t('login.validation.passwordMismatch'))
        return
      }
    }

    userManagement.loading = true

    // 根据模式决定创建还是更新用户
    if (userManagement.editMode) {
      // 创建更新数据对象
      const updateData = {
        username: userManagement.form.username.trim(),
        role: userManagement.form.role
      }

      // 添加手机号字段
      if (userManagement.form.phoneNumber) {
        updateData.phone_number = userManagement.form.phoneNumber
      }

      // 超级管理员可以修改部门
      if (userStore.isSuperAdmin && userManagement.form.departmentId) {
        updateData.department_id = userManagement.form.departmentId
      }

      // 超级管理员可以指定团队（未选时保持现状，后端在改部门时自动落到新部门默认团队）
      if (userStore.isSuperAdmin && userManagement.form.teamId) {
        updateData.team_id = userManagement.form.teamId
      }

      // 如果显示了密码字段并且填写了密码，才更新密码
      if (userManagement.displayPasswordFields && userManagement.form.password) {
        updateData.password = userManagement.form.password
      }

      await userStore.updateUser(userManagement.editUserId, updateData)
      message.success(t('userMgmt.userUpdated'))
    } else {
      // 创建新用户
      const createData = {
        username: userManagement.form.username.trim(),
        password: userManagement.form.password,
        role: userManagement.form.role
      }

      // 超级管理员可以指定部门
      if (userStore.isSuperAdmin && userManagement.form.departmentId) {
        createData.department_id = userManagement.form.departmentId
      }

      // 超级管理员可以指定团队（未选时后端自动落到该部门默认团队）
      if (userStore.isSuperAdmin && userManagement.form.teamId) {
        createData.team_id = userManagement.form.teamId
      }

      // 添加手机号字段（如果填写了）
      if (userManagement.form.phoneNumber) {
        createData.phone_number = userManagement.form.phoneNumber
      }

      await userStore.createUser(createData)
      message.success(t('userMgmt.userCreated'))
    }

    // 重新获取用户列表
    await fetchUsers()
    userManagement.modalVisible = false
  } catch (error) {
    console.error(t('userMgmt.userOperationFailed'), error)
    message.error(error.message || t('settings.operationFailedRetry'))
  } finally {
    userManagement.loading = false
  }
}

// 删除用户
const confirmDeleteUser = (user) => {
  // 自己不能删除自己
  if (user.id === userStore.userId) {
    message.error(t('userMgmt.cannotDeleteSelf'))
    return
  }

  // 确认对话框
  Modal.confirm({
    title: t('userMgmt.confirmDeleteTitle'),
    content: t('userMgmt.confirmDeleteContent', { name: user.username }),
    okText: t('common.delete'),
    okType: 'danger',
    cancelText: t('common.cancel'),
    async onOk() {
      try {
        userManagement.loading = true
        await userStore.deleteUser(user.id)
        message.success(t('userMgmt.userDeleted'))
        // 重新获取用户列表
        await fetchUsers()
      } catch (error) {
        console.error(t('userMgmt.deleteFailedLog'), error)
        message.error(error.message || t('settings.deleteFailedRetry'))
      } finally {
        userManagement.loading = false
      }
    }
  })
}

const getRoleClass = (role) => {
  switch (role) {
    case 'superadmin':
      return 'role-superadmin'
    case 'admin':
      return 'role-admin'
    case 'user':
      return 'role-user'
    default:
      return 'role-default'
  }
}

// 在组件挂载时获取用户列表
onMounted(async () => {
  await fetchUsers()
  await fetchDepartments()
})
</script>

<style lang="less" scoped>
.user-management {
  .header-section {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 16px;

    .header-content {
      flex: 1;
      min-width: 0;

      .section-title {
        font-size: 16px;
        font-weight: 500;
        color: var(--gray-900);
        line-height: 1.4;
        margin: 12px 0 12px;
      }

      .section-description {
        font-size: 14px;
        color: var(--gray-600);
        line-height: 1.4;
        margin: 0;
      }
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 8px;

      .refresh-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        border-radius: 6px;
        transition: all 0.2s ease;

        &:hover {
          background: var(--gray-25);
        }

        .spin {
          animation: spin 1s linear infinite;
        }

        :deep(.ant-btn-loading-icon) {
          color: var(--gray-600);
        }
      }
    }
  }

  .filter-section {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 16px;
    flex-wrap: wrap;

    .search-input {
      width: 300px;
      max-width: 100%;

      :deep(.ant-input-prefix) {
        color: var(--gray-500);
        margin-right: 6px;
      }
    }

    .filter-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      margin-left: auto;
    }

    .filter-select {
      width: 150px;
    }
  }

  @media (max-width: 640px) {
    .filter-section {
      align-items: stretch;

      .search-input,
      .filter-actions {
        width: 100%;
      }

      .filter-actions {
        margin-left: 0;
      }

      .filter-select {
        flex: 1;
        min-width: 0;
      }
    }
  }

  .content-section {
    overflow: hidden;

    .error-message {
      padding: 16px 24px;
    }

    .cards-container {
      .empty-state {
        padding: 60px 20px;
        text-align: center;
      }

      .user-cards-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
        gap: 16px;
        // padding: 16px;

        .user-card {
          cursor: default;

          :deep(.info-card-icon) {
            border-radius: 50%;
          }

          :deep(.info-card-body) {
            display: flex;
            flex-direction: column;
            gap: 8px;
          }

          .avatar-img {
            width: 100%;
            height: 100%;
            object-fit: cover;
          }

          .role-dept-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 2px 8px 2px 4px;
            background: var(--gray-50);
            border-radius: 4px;

            .role-icon-wrapper {
              display: flex;
              align-items: center;
              justify-content: center;
              width: 16px;
              height: 16px;

              &.role-superadmin {
                color: var(--color-error-700);
              }
              &.role-admin {
                color: var(--color-info-700);
              }
              &.role-user {
                color: var(--color-success-700);
              }
            }

            .dept-text {
              font-size: 12px;
              color: var(--gray-700);
              font-weight: 500;
            }
          }

          .card-content {
            .info-item {
              display: flex;
              justify-content: space-between;
              align-items: center;
              padding: 2px 0;
              border-bottom: 1px solid var(--gray-25);

              &:last-child {
                border-bottom: none;
              }

              .info-label {
                font-size: 12px;
                color: var(--gray-600);
                font-weight: 500;
                min-width: 70px;
              }

              .info-value {
                font-size: 12px;
                color: var(--gray-900);
                text-align: right;
                flex: 1;

                &.time-text {
                  color: var(--gray-700);
                }

                &.phone-text {
                  font-family: 'Monaco', 'Consolas', monospace;
                }
              }
            }
          }
        }
      }

      .pagination-section {
        display: flex;
        justify-content: flex-end;
        margin-top: 16px;
      }
    }
  }

  .time-text {
    font-size: 13px;
    color: var(--gray-700);
  }

  .phone-text,
  .user-id-text {
    font-size: 13px;
    color: var(--gray-900);
    font-family: 'Monaco', 'Consolas', monospace;
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.import-alert {
  margin-bottom: 16px;
}

.import-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.import-filename {
  min-width: 0;
  color: var(--gray-600);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-modal {
  :deep(.ant-modal-header) {
    padding: 20px 24px 16px;
    border-bottom: 1px solid var(--gray-150);

    .ant-modal-title {
      font-size: 17px;
      font-weight: 600;
      color: var(--gray-900);
    }
  }

  :deep(.ant-modal-body) {
    padding: 20px 24px 24px;
  }

  .user-form {
    .form-item {
      margin-bottom: 16px;

      :deep(.ant-form-item-label) {
        padding-bottom: 6px;

        label {
          font-weight: 600;
          font-size: 13px;
          color: var(--gray-800);
        }
      }
    }

    .error-text {
      color: var(--color-error-500);
      font-size: 12px;
      margin-top: 4px;
      line-height: 1.3;
    }

    .help-text {
      color: var(--gray-600);
      font-size: 12px;
      margin-top: 4px;
      line-height: 1.3;
    }

    .password-toggle {
      margin-bottom: 16px;
      padding: 12px 16px;
      background: var(--gray-25);
      border-radius: 8px;
      border: 1px solid var(--gray-100);

      :deep(.ant-checkbox-wrapper) {
        font-weight: 500;
        color: var(--gray-700);
        font-size: 13px;
      }
    }
  }
}
</style>
