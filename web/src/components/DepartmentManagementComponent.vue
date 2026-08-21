<template>
  <div class="department-management">
    <!-- 头部区域 -->
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">部门管理</div>
        <p class="section-description">管理系统部门，部门下的用户会被隔离管理。</p>
      </div>
      <div class="header-actions">
        <a-button
          @click="handleRefresh"
          :loading="departmentManagement.refreshing"
          title="刷新"
          class="refresh-btn lucide-icon-btn"
        >
          <template #icon
            ><RefreshCw :size="16" :class="{ spin: departmentManagement.refreshing }"
          /></template>
        </a-button>
        <a-button type="primary" @click="showAddDepartmentModal" class="add-btn lucide-icon-btn">
          <template #icon><Plus :size="16" /></template>
          添加部门
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
                  <span class="team-section-title">团队</span>
                  <a-button
                    type="dashed"
                    size="small"
                    class="add-team-btn lucide-icon-btn"
                    @click="showAddTeamModal(record)"
                  >
                    <template #icon><Plus :size="14" /></template>
                    新建团队
                  </a-button>
                </div>
                <div v-if="(teamsByDepartment[record.id] || []).length === 0" class="team-empty">
                  暂无团队
                </div>
                <div v-else class="team-list">
                  <div v-for="team in teamsByDepartment[record.id]" :key="team.id" class="team-row">
                    <Users :size="14" class="team-row-icon" />
                    <span class="team-name">{{ team.name }}</span>
                    <a-tag v-if="team.is_default" color="blue">默认</a-tag>
                    <span class="team-count">{{ team.user_count ?? 0 }} 人</span>
                    <span class="team-actions">
                      <a-tooltip title="管理成员">
                        <a-button
                          type="text"
                          size="small"
                          class="action-btn lucide-icon-btn"
                          @click="showManageMembers(record, team)"
                        >
                          <UserPlus :size="14" />
                        </a-button>
                      </a-tooltip>
                      <a-tooltip title="编辑团队">
                        <a-button
                          type="text"
                          size="small"
                          class="action-btn lucide-icon-btn"
                          @click="showEditTeamModal(record, team)"
                        >
                          <SquarePen :size="14" />
                        </a-button>
                      </a-tooltip>
                      <a-tooltip title="删除团队">
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
                <span>{{ record.user_count ?? 0 }} 人</span>
              </template>
              <template v-if="column.key === 'action'">
                <a-space>
                  <a-tooltip title="编辑部门">
                    <a-button
                      type="text"
                      size="small"
                      @click="showEditDepartmentModal(record)"
                      class="action-btn lucide-icon-btn"
                    >
                      <SquarePen :size="14" />
                    </a-button>
                  </a-tooltip>
                  <a-tooltip title="删除部门">
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
          <a-empty description="暂无部门数据" />
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
        <a-form-item label="部门名称" required class="form-item">
          <a-input
            v-model:value="departmentManagement.form.name"
            placeholder="请输入部门名称"
            size="large"
            :maxlength="50"
          />
        </a-form-item>

        <a-form-item label="部门描述" class="form-item">
          <a-textarea
            v-model:value="departmentManagement.form.description"
            placeholder="请输入部门描述（可选）"
            :rows="3"
            :maxlength="255"
            show-count
          />
        </a-form-item>

        <a-divider v-if="!departmentManagement.editMode" />

        <template v-if="!departmentManagement.editMode">
          <p class="admin-section-hint">
            创建部门时必须同时创建管理员，该管理员将负责管理本部门用户
          </p>

          <a-form-item label="管理员UID" required class="form-item">
            <a-input
              v-model:value="departmentManagement.form.adminUid"
              placeholder="请输入管理员UID（3-20位字母/数字/下划线）"
              size="large"
              :maxlength="20"
              @blur="checkAdminUid"
            />
            <div v-if="departmentManagement.form.uidError" class="error-text">
              {{ departmentManagement.form.uidError }}
            </div>
            <div v-else class="help-text">此 UID 将用于登录</div>
          </a-form-item>

          <a-form-item label="密码" required class="form-item">
            <a-input-password
              v-model:value="departmentManagement.form.adminPassword"
              placeholder="请输入管理员密码"
              size="large"
              :maxlength="50"
            />
          </a-form-item>

          <a-form-item label="确认密码" required class="form-item">
            <a-input-password
              v-model:value="departmentManagement.form.adminConfirmPassword"
              placeholder="请再次输入密码"
              size="large"
              :maxlength="50"
            />
          </a-form-item>

          <a-form-item label="手机号（可选）" class="form-item">
            <a-input
              v-model:value="departmentManagement.form.adminPhone"
              placeholder="请输入手机号（可用于登录）"
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
        <a-form-item label="团队名称" required class="form-item">
          <a-input
            v-model:value="teamManagement.form.name"
            placeholder="请输入团队名称"
            size="large"
            :maxlength="50"
          />
        </a-form-item>

        <a-form-item label="团队描述" class="form-item">
          <a-textarea
            v-model:value="teamManagement.form.description"
            placeholder="请输入团队描述（可选）"
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
      :title="memberManagement.team ? `团队「${memberManagement.team.name}」成员` : '管理团队成员'"
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
            placeholder="勾选该团队的用户"
            class="member-select"
            :maxTagCount="10"
          />
          <p v-if="memberManagement.team && memberManagement.team.is_default" class="help-text">
            默认团队是未分配用户的兜底桶：勾选用户将移回默认团队（取消分配），取消勾选不改变归属。
          </p>
          <p v-else class="help-text">勾选用户加入该团队；取消勾选的原成员将回到本部门默认团队。</p>
        </div>
      </a-spin>
    </a-modal>
  </div>
</template>

<script setup>
import { reactive, computed, onMounted, watch } from 'vue'
import { notification, message, Modal } from 'ant-design-vue'
import { departmentApi, apiSuperAdminGet, getUsersByDepartment } from '@/apis'
import { Plus, RefreshCw, SquarePen, Trash2, UserPlus, Users } from 'lucide-vue-next'

// 表格列定义
const columns = [
  {
    title: '部门名称',
    dataIndex: 'name',
    key: 'name',
    width: 200
  },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true
  },
  {
    title: '用户数量',
    dataIndex: 'user_count',
    key: 'userCount',
    width: 100,
    align: 'center'
  },
  {
    title: '操作',
    key: 'action',
    width: 120,
    align: 'center'
  }
]

// 部门管理状态
const departmentManagement = reactive({
  loading: false,
  refreshing: false,
  departments: [],
  error: null,
  modalVisible: false,
  modalTitle: '添加部门',
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
  modalTitle: '新建团队',
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
    label: user.team_name ? `${user.username}（${user.team_name}）` : user.username
  }))
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
    console.error('获取部门用户失败:', error)
    notification.error({
      message: '获取成员列表失败',
      description: error.message || '请稍后重试'
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
    notification.success({ message: '团队成员已更新' })
    memberManagement.visible = false
    await fetchDepartments()
  } catch (error) {
    console.error('更新团队成员失败:', error)
    notification.error({
      message: '更新失败',
      description: error.message || '请稍后重试'
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
    console.error('获取部门/团队列表失败:', error)
    departmentManagement.error = '获取部门列表失败'
  } finally {
    departmentManagement.loading = false
  }
}

// 打开新建团队模态框
const showAddTeamModal = (department) => {
  teamManagement.modalTitle = '新建团队'
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
  teamManagement.modalTitle = '编辑团队'
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
      notification.error({ message: '团队名称不能为空' })
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
      notification.success({ message: '团队更新成功' })
    } else {
      await departmentApi.createTeam(teamManagement.departmentId, payload)
      notification.success({ message: '团队创建成功' })
    }
    await fetchDepartments()
    teamManagement.modalVisible = false
  } catch (error) {
    console.error('团队操作失败:', error)
    notification.error({
      message: '操作失败',
      description: error.message || '请稍后重试'
    })
  } finally {
    teamManagement.loading = false
  }
}

// 删除团队（默认团队不可删）
const confirmDeleteTeam = (department, team) => {
  Modal.confirm({
    title: '确认删除团队',
    content: `确定要删除团队 "${team.name}" 吗？该团队下的用户会迁移到本部门默认团队。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        teamManagement.loading = true
        await departmentApi.deleteTeam(department.id, team.id)
        notification.success({ message: '团队删除成功' })
        await fetchDepartments()
      } catch (error) {
        console.error('删除团队失败:', error)
        notification.error({
          message: '删除失败',
          description: error.message || '请稍后重试'
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
    message.success('刷新成功')
  } catch (error) {
    console.error('刷新失败:', error)
    message.error('刷新失败')
  } finally {
    departmentManagement.refreshing = false
  }
}

// 打开添加部门模态框
const showAddDepartmentModal = () => {
  departmentManagement.modalTitle = '添加部门'
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
  departmentManagement.modalTitle = '编辑部门'
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
      departmentManagement.form.phoneError = '请输入正确的手机号格式'
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
    departmentManagement.form.uidError = 'UID只能包含字母、数字和下划线'
    return
  }

  if (uid.length < 3 || uid.length > 20) {
    departmentManagement.form.uidError = 'UID长度必须在3-20个字符之间'
    return
  }

  // 检查是否已存在
  try {
    const result = await apiSuperAdminGet(`/api/auth/check-uid/${uid}`)
    if (!result.is_available) {
      departmentManagement.form.uidError = '该UID已被使用'
    }
  } catch (error) {
    console.error('检查UID失败:', error)
  }
}

// 处理部门表单提交
const handleDepartmentFormSubmit = async () => {
  try {
    // 验证部门名称
    if (!departmentManagement.form.name.trim()) {
      notification.error({ message: '部门名称不能为空' })
      return
    }

    if (departmentManagement.form.name.trim().length < 2) {
      notification.error({ message: '部门名称至少2个字符' })
      return
    }

    // 验证管理员UID
    const adminUid = departmentManagement.form.adminUid.trim()
    if (!adminUid) {
      notification.error({ message: '请输入管理员UID' })
      return
    }

    if (!/^[a-zA-Z0-9_]+$/.test(adminUid)) {
      notification.error({ message: 'UID只能包含字母、数字和下划线' })
      return
    }

    if (adminUid.length < 3 || adminUid.length > 20) {
      notification.error({ message: 'UID长度必须在3-20个字符之间' })
      return
    }

    if (departmentManagement.form.uidError) {
      notification.error({ message: '管理员UID已存在或格式错误' })
      return
    }

    // 验证密码
    if (!departmentManagement.form.adminPassword) {
      notification.error({ message: '请输入管理员密码' })
      return
    }

    if (
      departmentManagement.form.adminPassword !== departmentManagement.form.adminConfirmPassword
    ) {
      notification.error({ message: '两次输入的密码不一致' })
      return
    }

    // 验证手机号
    if (
      departmentManagement.form.adminPhone &&
      !validatePhoneNumber(departmentManagement.form.adminPhone)
    ) {
      notification.error({ message: '请输入正确的手机号格式' })
      return
    }

    departmentManagement.loading = true

    if (departmentManagement.editMode) {
      // 更新部门
      await departmentApi.updateDepartment(departmentManagement.editDepartmentId, {
        name: departmentManagement.form.name.trim(),
        description: departmentManagement.form.description.trim() || undefined
      })
      notification.success({ message: '部门更新成功' })
    } else {
      // 创建部门，同时创建管理员
      await departmentApi.createDepartment({
        name: departmentManagement.form.name.trim(),
        description: departmentManagement.form.description.trim() || undefined,
        admin_uid: adminUid,
        admin_password: departmentManagement.form.adminPassword,
        admin_phone: departmentManagement.form.adminPhone || undefined
      })

      message.success(`部门创建成功，管理员 "${adminUid}" 已创建`)
    }

    // 重新获取部门列表
    await fetchDepartments()
    departmentManagement.modalVisible = false
  } catch (error) {
    console.error('部门操作失败:', error)
    notification.error({
      message: '操作失败',
      description: error.message || '请稍后重试'
    })
  } finally {
    departmentManagement.loading = false
  }
}

// 删除部门
const confirmDeleteDepartment = (department) => {
  Modal.confirm({
    title: '确认删除部门',
    content: `确定要删除部门 "${department.name}" 吗？此操作不可撤销。该部门下的用户会被迁移到默认部门，部门级配置和部门 API Key 会一并清理。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        departmentManagement.loading = true
        await departmentApi.deleteDepartment(department.id)
        notification.success({ message: '部门删除成功' })
        // 重新获取部门列表
        await fetchDepartments()
      } catch (error) {
        console.error('删除部门失败:', error)
        notification.error({
          message: '删除失败',
          description: error.message || '请稍后重试'
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
