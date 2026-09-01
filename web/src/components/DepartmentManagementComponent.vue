<template>
  <div class="department-management">
    <!-- 头部区域 -->
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">{{ $t('deptMgmt.title') }}</div>
        <p class="section-description">{{ $t('deptMgmt.description') }}</p>
      </div>
      <div class="header-actions">
        <a-button
          @click="handleRefresh"
          :loading="departmentManagement.refreshing"
          :title="$t('common.refresh')"
          class="refresh-btn lucide-icon-btn"
        >
          <template #icon
            ><RefreshCw :size="16" :class="{ spin: departmentManagement.refreshing }"
          /></template>
        </a-button>
        <a-button type="primary" @click="showAddDepartmentModal" class="add-btn lucide-icon-btn">
          <template #icon><Plus :size="16" /></template>
          {{ $t('deptMgmt.addDepartment') }}
        </a-button>
      </div>
    </div>

    <!-- 主内容区域 -->
    <div class="content-section">
      <a-spin :spinning="departmentManagement.loading">
        <div v-if="departmentManagement.error" class="error-message">
          <a-alert type="error" :message="departmentManagement.error" show-icon />
        </div>

        <template v-if="departmentManagement.departments.length > 0">
          <a-table
            :dataSource="departmentManagement.departments"
            :columns="columns"
            :rowKey="(record) => record.id"
            :pagination="false"
            class="department-table"
          >
            <template #expandedRowRender="{ record }">
              <div class="team-section">
                <div class="team-section-header">
                  <span class="team-section-title">{{ $t('deptMgmt.team') }}</span>
                  <a-button
                    type="dashed"
                    size="small"
                    class="add-team-btn lucide-icon-btn"
                    @click="showAddTeamModal(record)"
                  >
                    <template #icon><Plus :size="14" /></template>
                    {{ $t('deptMgmt.createTeam') }}
                  </a-button>
                </div>
                <div v-if="(teamsByDepartment[record.id] || []).length === 0" class="team-empty">
                  {{ $t('deptMgmt.noTeams') }}
                </div>
                <div v-else class="team-list">
                  <div v-for="team in teamsByDepartment[record.id]" :key="team.id" class="team-row">
                    <Users :size="14" class="team-row-icon" />
                    <span class="team-name">{{ team.name }}</span>
                    <a-tag v-if="team.is_default" color="blue">{{ $t('deptMgmt.default') }}</a-tag>
                    <span class="team-count">
                      {{ $t('deptMgmt.userCount', { count: team.user_count ?? 0 }) }}
                    </span>
                    <span class="team-actions">
                      <a-tooltip :title="$t('deptMgmt.manageMembers')">
                        <a-button
                          type="text"
                          size="small"
                          class="action-btn lucide-icon-btn"
                          @click="showManageMembers(record, team)"
                        >
                          <UserPlus :size="14" />
                        </a-button>
                      </a-tooltip>
                      <a-tooltip :title="$t('deptMgmt.editTeam')">
                        <a-button
                          type="text"
                          size="small"
                          class="action-btn lucide-icon-btn"
                          @click="showEditTeamModal(record, team)"
                        >
                          <SquarePen :size="14" />
                        </a-button>
                      </a-tooltip>
                      <a-tooltip :title="$t('deptMgmt.deleteTeam')">
                        <a-button
                          type="text"
                          size="small"
                          danger
                          :disabled="team.is_default"
                          class="action-btn lucide-icon-btn"
                          @click="confirmDeleteTeam(record, team)"
                        >
                          <Trash2 :size="14" />
                        </a-button>
                      </a-tooltip>
                    </span>
                  </div>
                </div>
              </div>
            </template>
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'name'">
                <div class="department-name">
                  <span class="name-text">{{ record.name }}</span>
                </div>
              </template>
              <template v-if="column.key === 'description'">
                <span class="description-text">{{ record.description || '-' }}</span>
              </template>
              <template v-if="column.key === 'userCount'">
                <span>{{ $t('deptMgmt.userCount', { count: record.user_count ?? 0 }) }}</span>
              </template>
              <template v-if="column.key === 'action'">
                <a-space>
                  <a-tooltip :title="$t('deptMgmt.editDepartment')">
                    <a-button
                      type="text"
                      size="small"
                      @click="showEditDepartmentModal(record)"
                      class="action-btn lucide-icon-btn"
                    >
                      <SquarePen :size="14" />
                    </a-button>
                  </a-tooltip>
                  <a-tooltip :title="$t('deptMgmt.deleteDepartment')">
                    <a-button
                      type="text"
                      size="small"
                      danger
                      @click="confirmDeleteDepartment(record)"
                      :disabled="record.id === 1"
                      class="action-btn lucide-icon-btn"
                    >
                      <Trash2 :size="14" />
                    </a-button>
                  </a-tooltip>
                </a-space>
              </template>
            </template>
          </a-table>
        </template>

        <div v-else class="empty-state">
          <a-empty :description="$t('deptMgmt.noDepartments')" />
        </div>
      </a-spin>
    </div>

    <!-- 部门表单模态框 -->
    <a-modal
      v-model:open="departmentManagement.modalVisible"
      :title="departmentManagement.modalTitle"
      @ok="handleDepartmentFormSubmit"
      :confirmLoading="departmentManagement.loading"
      @cancel="departmentManagement.modalVisible = false"
      :maskClosable="false"
      width="520px"
      class="department-modal"
    >
      <a-form layout="vertical" class="department-form">
        <a-form-item :label="$t('deptMgmt.departmentName')" required class="form-item">
          <a-input
            v-model:value="departmentManagement.form.name"
            :placeholder="$t('deptMgmt.departmentNamePlaceholder')"
            size="large"
            :maxlength="50"
          />
        </a-form-item>

        <a-form-item :label="$t('deptMgmt.departmentDescription')" class="form-item">
          <a-textarea
            v-model:value="departmentManagement.form.description"
            :placeholder="$t('deptMgmt.departmentDescriptionPlaceholder')"
            :rows="3"
            :maxlength="255"
            show-count
          />
        </a-form-item>

        <a-divider v-if="!departmentManagement.editMode" />

        <template v-if="!departmentManagement.editMode">
          <p class="admin-section-hint">{{ $t('deptMgmt.adminRequiredHint') }}</p>

          <a-form-item :label="$t('deptMgmt.adminUid')" required class="form-item">
            <a-input
              v-model:value="departmentManagement.form.adminUid"
              :placeholder="$t('deptMgmt.adminUidPlaceholder')"
              size="large"
              :maxlength="20"
              @blur="checkAdminUid"
            />
            <div v-if="departmentManagement.form.uidError" class="error-text">
              {{ departmentManagement.form.uidError }}
            </div>
            <div v-else class="help-text">{{ $t('deptMgmt.uidForLogin') }}</div>
          </a-form-item>

          <a-form-item :label="$t('login.label.password')" required class="form-item">
            <a-input-password
              v-model:value="departmentManagement.form.adminPassword"
              :placeholder="$t('deptMgmt.adminPasswordPlaceholder')"
              size="large"
              :maxlength="50"
            />
          </a-form-item>

          <a-form-item :label="$t('login.label.confirmPassword')" required class="form-item">
            <a-input-password
              v-model:value="departmentManagement.form.adminConfirmPassword"
              :placeholder="$t('login.validation.confirmRequired')"
              size="large"
              :maxlength="50"
            />
          </a-form-item>

          <a-form-item :label="$t('deptMgmt.adminPhoneOptional')" class="form-item">
            <a-input
              v-model:value="departmentManagement.form.adminPhone"
              :placeholder="$t('deptMgmt.adminPhonePlaceholder')"
              size="large"
              :maxlength="11"
            />
            <div v-if="departmentManagement.form.phoneError" class="error-text">
              {{ departmentManagement.form.phoneError }}
            </div>
          </a-form-item>
        </template>
      </a-form>
    </a-modal>

    <!-- 团队表单模态框 -->
    <a-modal
      v-model:open="teamManagement.modalVisible"
      :title="teamManagement.modalTitle"
      @ok="handleTeamFormSubmit"
      :confirmLoading="teamManagement.loading"
      @cancel="teamManagement.modalVisible = false"
      :maskClosable="false"
      width="480px"
      class="department-modal"
    >
      <a-form layout="vertical" class="department-form">
        <a-form-item :label="$t('deptMgmt.teamName')" required class="form-item">
          <a-input
            v-model:value="teamManagement.form.name"
            :placeholder="$t('deptMgmt.teamNamePlaceholder')"
            size="large"
            :maxlength="50"
          />
        </a-form-item>

        <a-form-item :label="$t('deptMgmt.teamDescription')" class="form-item">
          <a-textarea
            v-model:value="teamManagement.form.description"
            :placeholder="$t('deptMgmt.teamDescriptionPlaceholder')"
            :rows="3"
            :maxlength="255"
            show-count
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 团队成员管理模态框 -->
    <a-modal
      v-model:open="memberManagement.visible"
      :title="memberModalTitle"
      @ok="handleMemberSubmit"
      :confirmLoading="memberManagement.loading"
      @cancel="memberManagement.visible = false"
      :maskClosable="false"
      width="520px"
      class="department-modal"
    >
      <a-spin :spinning="memberManagement.loading">
        <div class="member-select-wrap">
          <a-select
            v-model:value="memberManagement.selectedIds"
            mode="multiple"
            :options="memberOptions"
            option-filter-prop="label"
            show-search
            :placeholder="$t('deptMgmt.selectTeamMembersPlaceholder')"
            class="member-select"
            :maxTagCount="10"
          />
          <p v-if="memberManagement.team && memberManagement.team.is_default" class="help-text">
            {{ $t('deptMgmt.defaultTeamHint') }}
          </p>
          <p v-else class="help-text">{{ $t('deptMgmt.teamMemberHint') }}</p>
        </div>
      </a-spin>
    </a-modal>
  </div>
</template>

<script setup>
import { reactive, computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { notification, message, Modal } from 'ant-design-vue'
import { departmentApi, apiSuperAdminGet, getUsersByDepartment } from '@/apis'
import { Plus, RefreshCw, SquarePen, Trash2, UserPlus, Users } from 'lucide-vue-next'

const { t } = useI18n()

// 表格列定义
const columns = computed(() => [
  {
    title: t('deptMgmt.departmentName'),
    dataIndex: 'name',
    key: 'name',
    width: 200
  },
  {
    title: t('deptMgmt.descriptionColumn'),
    dataIndex: 'description',
    key: 'description',
    ellipsis: true
  },
  {
    title: t('deptMgmt.userCountColumn'),
    dataIndex: 'user_count',
    key: 'userCount',
    width: 100,
    align: 'center'
  },
  {
    title: t('deptMgmt.actions'),
    key: 'action',
    width: 120,
    align: 'center'
  }
])

// 部门管理状态
const departmentManagement = reactive({
  loading: false,
  refreshing: false,
  departments: [],
  error: null,
  modalVisible: false,
  modalTitle: t('deptMgmt.addDepartment'),
  editMode: false,
  editDepartmentId: null,
  form: {
    name: '',
    description: '',
    adminUid: '',
    adminPassword: '',
    adminConfirmPassword: '',
    adminPhone: '',
    uidError: '',
    phoneError: ''
  }
})

// 团队管理状态
const teamManagement = reactive({
  loading: false,
  modalVisible: false,
  modalTitle: t('deptMgmt.createTeam'),
  editMode: false,
  editTeamId: null,
  departmentId: null,
  form: {
    name: '',
    description: ''
  }
})

// 按部门分组的团队缓存：{ [departmentId]: [team, ...] }
const teamsByDepartment = reactive({})

// 团队成员管理状态
const memberManagement = reactive({
  visible: false,
  loading: false,
  departmentId: null,
  team: null,
  users: [], // 部门用户（含 team_id/team_name）
  selectedIds: [] // 勾选的用户 id
})

// 成员下拉选项：用户名（当前团队名）便于识别归属
const memberOptions = computed(() =>
  memberManagement.users.map((user) => ({
    value: user.id,
    label: user.team_name
      ? t('deptMgmt.memberOptionLabel', { username: user.username, teamName: user.team_name })
      : user.username
  }))
)

const memberModalTitle = computed(() =>
  memberManagement.team
    ? t('deptMgmt.teamMemberTitle', { name: memberManagement.team.name })
    : t('deptMgmt.manageTeamMembers')
)

// 打开成员管理弹窗：拉取部门用户，预选当前团队已有成员
const showManageMembers = async (department, team) => {
  memberManagement.departmentId = department.id
  memberManagement.team = team
  memberManagement.users = []
  memberManagement.selectedIds = []
  memberManagement.visible = true
  memberManagement.loading = true
  try {
    const users = await getUsersByDepartment(department.id)
    memberManagement.users = users
    memberManagement.selectedIds = users
      .filter((user) => user.team_id === team.id)
      .map((user) => user.id)
  } catch (error) {
    console.error(t('deptMgmt.fetchDeptUsersFailedLog'), error)
    notification.error({
      message: t('deptMgmt.fetchMembersFailed'),
      description: error.message || t('settings.retryLater')
    })
  } finally {
    memberManagement.loading = false
  }
}

// 保存成员：勾选的进团队，取消勾选的原成员回默认团队
const handleMemberSubmit = async () => {
  try {
    memberManagement.loading = true
    await departmentApi.updateTeamMembers(
      memberManagement.departmentId,
      memberManagement.team.id,
      memberManagement.selectedIds
    )
    notification.success({ message: t('deptMgmt.teamMembersUpdated') })
    memberManagement.visible = false
    await fetchDepartments()
  } catch (error) {
    console.error(t('deptMgmt.updateTeamMembersFailedLog'), error)
    notification.error({
      message: t('deptMgmt.updateFailed'),
      description: error.message || t('settings.retryLater')
    })
  } finally {
    memberManagement.loading = false
  }
}

// 获取部门列表
const fetchDepartments = async () => {
  try {
    departmentManagement.loading = true
    departmentManagement.error = null
    const [departments, teams] = await Promise.all([
      departmentApi.getDepartments(),
      departmentApi.getAllTeams()
    ])
    departmentManagement.departments = departments
    // 按部门分组团队，供展开行渲染
    const grouped = {}
    for (const team of teams) {
      const deptId = String(team.department_id)
      if (!grouped[deptId]) grouped[deptId] = []
      grouped[deptId].push(team)
    }
    Object.keys(teamsByDepartment).forEach((key) => delete teamsByDepartment[key])
    Object.assign(teamsByDepartment, grouped)
  } catch (error) {
    console.error(t('userMgmt.fetchDeptTeamFailed'), error)
    departmentManagement.error = t('deptMgmt.fetchDepartmentsFailed')
  } finally {
    departmentManagement.loading = false
  }
}

// 打开新建团队模态框
const showAddTeamModal = (department) => {
  teamManagement.modalTitle = t('deptMgmt.createTeam')
  teamManagement.editMode = false
  teamManagement.editTeamId = null
  teamManagement.departmentId = department.id
  teamManagement.form = {
    name: '',
    description: ''
  }
  teamManagement.modalVisible = true
}

// 打开编辑团队模态框
const showEditTeamModal = (department, team) => {
  teamManagement.modalTitle = t('deptMgmt.editTeam')
  teamManagement.editMode = true
  teamManagement.editTeamId = team.id
  teamManagement.departmentId = department.id
  teamManagement.form = {
    name: team.name,
    description: team.description || ''
  }
  teamManagement.modalVisible = true
}

// 处理团队表单提交
const handleTeamFormSubmit = async () => {
  try {
    if (!teamManagement.form.name.trim()) {
      notification.error({ message: t('deptMgmt.teamNameRequired') })
      return
    }
    teamManagement.loading = true
    const payload = {
      name: teamManagement.form.name.trim(),
      description: teamManagement.form.description.trim() || undefined
    }
    if (teamManagement.editMode) {
      await departmentApi.updateTeam(
        teamManagement.departmentId,
        teamManagement.editTeamId,
        payload
      )
      notification.success({ message: t('deptMgmt.teamUpdated') })
    } else {
      await departmentApi.createTeam(teamManagement.departmentId, payload)
      notification.success({ message: t('deptMgmt.teamCreated') })
    }
    await fetchDepartments()
    teamManagement.modalVisible = false
  } catch (error) {
    console.error(t('deptMgmt.teamOperationFailedLog'), error)
    notification.error({
      message: t('common.operationFailed'),
      description: error.message || t('settings.retryLater')
    })
  } finally {
    teamManagement.loading = false
  }
}

// 删除团队（默认团队不可删）
const confirmDeleteTeam = (department, team) => {
  Modal.confirm({
    title: t('deptMgmt.confirmDeleteTeamTitle'),
    content: t('deptMgmt.confirmDeleteTeamContent', { name: team.name }),
    okText: t('common.delete'),
    okType: 'danger',
    cancelText: t('common.cancel'),
    async onOk() {
      try {
        teamManagement.loading = true
        await departmentApi.deleteTeam(department.id, team.id)
        notification.success({ message: t('deptMgmt.teamDeleted') })
        await fetchDepartments()
      } catch (error) {
        console.error(t('deptMgmt.deleteTeamFailedLog'), error)
        notification.error({
          message: t('deptMgmt.deleteFailed'),
          description: error.message || t('settings.retryLater')
        })
      } finally {
        teamManagement.loading = false
      }
    }
  })
}

// 刷新部门列表
const handleRefresh = async () => {
  if (departmentManagement.refreshing) return
  departmentManagement.refreshing = true
  try {
    await fetchDepartments()
    message.success(t('settings.refreshSuccess'))
  } catch (error) {
    console.error(t('settings.refreshFail'), error)
    message.error(t('settings.refreshFail'))
  } finally {
    departmentManagement.refreshing = false
  }
}

// 打开添加部门模态框
const showAddDepartmentModal = () => {
  departmentManagement.modalTitle = t('deptMgmt.addDepartment')
  departmentManagement.editMode = false
  departmentManagement.editDepartmentId = null
  departmentManagement.form = {
    name: '',
    description: '',
    adminUid: '',
    adminPassword: '',
    adminConfirmPassword: '',
    adminPhone: '',
    uidError: '',
    phoneError: ''
  }
  departmentManagement.modalVisible = true
}

// 打开编辑部门模态框
const showEditDepartmentModal = (department) => {
  departmentManagement.modalTitle = t('deptMgmt.editDepartment')
  departmentManagement.editMode = true
  departmentManagement.editDepartmentId = department.id
  departmentManagement.form = {
    name: department.name,
    description: department.description || '',
    adminUid: '',
    adminPassword: '',
    adminConfirmPassword: '',
    adminPhone: '',
    uidError: '',
    phoneError: ''
  }
  departmentManagement.modalVisible = true
}

// 验证手机号格式
const validatePhoneNumber = (phone) => {
  if (!phone) {
    return true // 手机号可选
  }
  const phoneRegex = /^1[3-9]\d{9}$/
  return phoneRegex.test(phone)
}

// 监听手机号输入变化
watch(
  () => departmentManagement.form.adminPhone,
  (newPhone) => {
    departmentManagement.form.phoneError = ''
    if (newPhone && !validatePhoneNumber(newPhone)) {
      departmentManagement.form.phoneError = t('userMgmt.phoneFormatInvalid')
    }
  }
)

// 检查管理员UID是否可用
const checkAdminUid = async () => {
  const uid = departmentManagement.form.adminUid.trim()
  departmentManagement.form.uidError = ''

  if (!uid) {
    return
  }

  // 验证格式
  if (!/^[a-zA-Z0-9_]+$/.test(uid)) {
    departmentManagement.form.uidError = t('deptMgmt.uidInvalid')
    return
  }

  if (uid.length < 3 || uid.length > 20) {
    departmentManagement.form.uidError = t('deptMgmt.uidLengthInvalid')
    return
  }

  // 检查是否已存在
  try {
    const result = await apiSuperAdminGet(`/api/auth/check-uid/${uid}`)
    if (!result.is_available) {
      departmentManagement.form.uidError = t('deptMgmt.uidInUse')
    }
  } catch (error) {
    console.error(t('deptMgmt.checkUidFailedLog'), error)
  }
}

// 处理部门表单提交
const handleDepartmentFormSubmit = async () => {
  try {
    // 验证部门名称
    if (!departmentManagement.form.name.trim()) {
      notification.error({ message: t('deptMgmt.departmentNameRequired') })
      return
    }

    if (departmentManagement.form.name.trim().length < 2) {
      notification.error({ message: t('deptMgmt.departmentNameMinLength') })
      return
    }

    // 验证管理员UID
    const adminUid = departmentManagement.form.adminUid.trim()
    if (!adminUid) {
      notification.error({ message: t('deptMgmt.adminUidRequired') })
      return
    }

    if (!/^[a-zA-Z0-9_]+$/.test(adminUid)) {
      notification.error({ message: t('deptMgmt.uidInvalid') })
      return
    }

    if (adminUid.length < 3 || adminUid.length > 20) {
      notification.error({ message: t('deptMgmt.uidLengthInvalid') })
      return
    }

    if (departmentManagement.form.uidError) {
      notification.error({ message: t('deptMgmt.adminUidUnavailable') })
      return
    }

    // 验证密码
    if (!departmentManagement.form.adminPassword) {
      notification.error({ message: t('deptMgmt.adminPasswordRequired') })
      return
    }

    if (
      departmentManagement.form.adminPassword !== departmentManagement.form.adminConfirmPassword
    ) {
      notification.error({ message: t('login.validation.passwordMismatch') })
      return
    }

    // 验证手机号
    if (
      departmentManagement.form.adminPhone &&
      !validatePhoneNumber(departmentManagement.form.adminPhone)
    ) {
      notification.error({ message: t('userMgmt.phoneFormatInvalid') })
      return
    }

    departmentManagement.loading = true

    if (departmentManagement.editMode) {
      // 更新部门
      await departmentApi.updateDepartment(departmentManagement.editDepartmentId, {
        name: departmentManagement.form.name.trim(),
        description: departmentManagement.form.description.trim() || undefined
      })
      notification.success({ message: t('deptMgmt.departmentUpdated') })
    } else {
      // 创建部门，同时创建管理员
      await departmentApi.createDepartment({
        name: departmentManagement.form.name.trim(),
        description: departmentManagement.form.description.trim() || undefined,
        admin_uid: adminUid,
        admin_password: departmentManagement.form.adminPassword,
        admin_phone: departmentManagement.form.adminPhone || undefined
      })

      message.success(t('deptMgmt.departmentCreated', { uid: adminUid }))
    }

    // 重新获取部门列表
    await fetchDepartments()
    departmentManagement.modalVisible = false
  } catch (error) {
    console.error(t('deptMgmt.departmentOperationFailedLog'), error)
    notification.error({
      message: t('common.operationFailed'),
      description: error.message || t('settings.retryLater')
    })
  } finally {
    departmentManagement.loading = false
  }
}

// 删除部门
const confirmDeleteDepartment = (department) => {
  Modal.confirm({
    title: t('deptMgmt.confirmDeleteDepartmentTitle'),
    content: t('deptMgmt.confirmDeleteDepartmentContent', { name: department.name }),
    okText: t('common.delete'),
    okType: 'danger',
    cancelText: t('common.cancel'),
    async onOk() {
      try {
        departmentManagement.loading = true
        await departmentApi.deleteDepartment(department.id)
        notification.success({ message: t('deptMgmt.departmentDeleted') })
        // 重新获取部门列表
        await fetchDepartments()
      } catch (error) {
        console.error(t('deptMgmt.deleteDepartmentFailedLog'), error)
        notification.error({
          message: t('deptMgmt.deleteFailed'),
          description: error.message || t('settings.retryLater')
        })
      } finally {
        departmentManagement.loading = false
      }
    }
  })
}

// 在组件挂载时获取部门列表
onMounted(() => {
  fetchDepartments()
})
</script>

<style lang="less" scoped>
.department-management {
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
      }
    }
  }

  .content-section {
    overflow: hidden;

    .error-message {
      padding: 16px 24px;
    }

    .empty-state {
      padding: 60px 20px;
      text-align: center;
    }

    .department-table {
      :deep(.ant-table-thead > tr > th) {
        background: var(--gray-50);
        font-weight: 500;
        padding: 8px 12px;
      }

      :deep(.ant-table-tbody > tr > td) {
        padding: 8px 12px;
      }

      .department-name {
        .name-text {
          font-weight: 500;
          color: var(--gray-900);
        }
      }

      .description-text {
        color: var(--gray-600);
      }

      .action-btn {
        padding: 4px 8px;
        border-radius: 6px;
        transition: all 0.2s ease;

        &:hover {
          background: var(--gray-25);
        }
      }
    }

    .team-section {
      padding: 8px 16px 12px 48px;

      .team-section-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 8px;

        .team-section-title {
          font-size: 13px;
          font-weight: 500;
          color: var(--gray-600);
        }
      }

      .team-empty {
        padding: 12px;
        text-align: center;
        color: var(--gray-500);
        font-size: 13px;
      }

      .team-list {
        display: flex;
        flex-direction: column;
        gap: 6px;

        .team-row {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 6px 10px;
          border: 1px solid var(--gray-100);
          border-radius: 8px;
          background: var(--gray-25, #fafbfc);

          .team-row-icon {
            color: var(--gray-500);
            flex-shrink: 0;
          }

          .team-name {
            font-weight: 500;
            color: var(--gray-900);
          }

          .team-count {
            margin-left: auto;
            color: var(--gray-500);
            font-size: 12px;
          }

          .team-actions {
            display: flex;
            align-items: center;
            gap: 2px;
          }
        }
      }
    }
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

.department-modal {
  :deep(.ant-modal-header) {
    padding: 20px 24px;
    border-bottom: 1px solid var(--gray-150);

    .ant-modal-title {
      font-size: 16px;
      font-weight: 600;
      color: var(--gray-900);
    }
  }

  :deep(.ant-modal-body) {
    padding: 24px;
  }

  .department-form {
    .form-item {
      margin-bottom: 20px;

      :deep(.ant-form-item-label) {
        padding-bottom: 4px;

        label {
          font-weight: 500;
          color: var(--gray-900);
        }
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

  .member-select-wrap {
    padding: 4px 0;

    .member-select {
      width: 100%;
    }
  }
}
</style>
