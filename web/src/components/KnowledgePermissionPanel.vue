<template>
  <section class="knowledge-permission-panel">
    <div class="permission-panel-header">
      <div>
        <h3>权限设置</h3>
        <p>按用户、部门或角色配置当前知识库的操作级权限。</p>
      </div>
      <a-button type="primary" :loading="loading" @click="loadPermissions">刷新</a-button>
    </div>

    <a-form class="permission-form" layout="inline" :model="form">
      <a-form-item label="授权类型">
        <a-select v-model:value="form.subject_type" style="width: 120px">
          <a-select-option value="user">用户</a-select-option>
          <a-select-option value="department">部门</a-select-option>
          <a-select-option value="role">角色</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item label="授权对象">
        <a-select
          v-model:value="form.subject_id"
          show-search
          allow-clear
          :loading="optionLoading"
          :options="subjectOptions"
          style="width: 260px"
          placeholder="请选择授权对象"
          option-filter-prop="label"
        />
      </a-form-item>
      <a-form-item label="权限预设">
        <a-space>
          <a-button
            v-for="preset in Object.entries(permissionPresets)"
            :key="preset[0]"
            size="small"
            @click="applyPreset(preset[0])"
          >
            {{ preset[1].label }}
          </a-button>
        </a-space>
      </a-form-item>
      <a-form-item>
        <a-space wrap>
          <a-checkbox v-for="item in permissionOptions" :key="item.key" v-model:checked="form[item.key]">
            {{ item.label }}
          </a-checkbox>
        </a-space>
      </a-form-item>
      <a-form-item>
        <a-button type="primary" :loading="saving" @click="savePermission">保存授权</a-button>
      </a-form-item>
    </a-form>

    <a-table
      row-key="id"
      :columns="columns"
      :data-source="permissions"
      :loading="loading"
      :pagination="false"
      size="middle"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="permissionKeys.includes(column.key)">
          <a-tag :color="record[column.key] ? 'green' : 'default'">
            {{ record[column.key] ? '允许' : '—' }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'subject_type'">
          <a-tag>{{ subjectTypeLabel(record.subject_type) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'subject_id'">
          {{ subjectLabel(record) }}
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a-button type="link" size="small" @click="editPermission(record)">编辑</a-button>
            <a-popconfirm title="确认删除这条授权？" @confirm="deletePermission(record)">
              <a-button type="link" danger size="small">删除</a-button>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { databaseApi } from '@/apis/knowledge_api'
import { authApi } from '@/apis/auth_api'
import { departmentApi } from '@/apis/department_api'
import {
  buildPermissionPayload,
  formatSubjectLabel,
  permissionKeys,
  permissionOptions,
  permissionPresets,
  roleOptions
} from './knowledgePermissionPanelHelpers'

const props = defineProps({
  kbId: {
    type: String,
    required: true
  }
})

const columns = [
  { title: '授权类型', dataIndex: 'subject_type', key: 'subject_type', width: 100 },
  { title: '授权对象', dataIndex: 'subject_id', key: 'subject_id', width: 180 },
  ...permissionOptions.map((item) => ({ title: item.label, dataIndex: item.key, key: item.key, width: 82 })),
  { title: '操作', key: 'actions', fixed: 'right', width: 130 }
]

const emptyForm = () => ({
  subject_type: 'user',
  subject_id: '',
  can_view: true,
  can_search: true,
  can_upload: false,
  can_download: false,
  can_delete: false,
  can_manage: false,
  can_grant: false,
  can_export: false
})

const form = reactive(emptyForm())
const permissions = ref([])
const loading = ref(false)
const saving = ref(false)
const optionLoading = ref(false)
const users = ref([])
const departments = ref([])

const userOptions = computed(() =>
  users.value.map((item) => ({
    value: item.uid,
    label: item.username ? `${item.username}（${item.uid}）` : item.uid
  }))
)

const departmentOptions = computed(() =>
  departments.value.map((item) => ({
    value: String(item.id),
    label: item.name ? `${item.name}（${item.id}）` : String(item.id)
  }))
)

const subjectOptions = computed(() => {
  if (form.subject_type === 'department') return departmentOptions.value
  if (form.subject_type === 'role') return roleOptions
  return userOptions.value
})

const resetForm = () => {
  Object.assign(form, emptyForm())
}

const subjectTypeLabel = (type) => {
  if (type === 'department') return '部门'
  if (type === 'role') return '角色'
  return '用户'
}

const subjectLabel = (record) => formatSubjectLabel(record, { users: users.value, departments: departments.value })

const loadSubjectOptions = async () => {
  optionLoading.value = true
  try {
    const [userResult, departmentResult] = await Promise.all([
      authApi.getUserAccessOptions(),
      departmentApi.getDepartments()
    ])
    users.value = Array.isArray(userResult) ? userResult : []
    departments.value = departmentResult.departments || departmentResult || []
  } catch (error) {
    message.warning(error.message || '加载授权对象失败，请手动刷新后重试')
    users.value = []
    departments.value = []
  } finally {
    optionLoading.value = false
  }
}

const applyPreset = (presetKey) => {
  const preset = permissionPresets[presetKey]
  if (!preset) return
  Object.assign(form, preset.flags)
}

const loadPermissions = async () => {
  if (!props.kbId) return
  loading.value = true
  try {
    const result = await databaseApi.getPermissions(props.kbId)
    permissions.value = result.permissions || []
  } catch (error) {
    message.error(error.message || '加载权限失败')
  } finally {
    loading.value = false
  }
}

const savePermission = async () => {
  const payload = buildPermissionPayload(form)
  if (!payload.subject_id) {
    message.warning('请选择授权对象')
    return
  }
  saving.value = true
  try {
    await databaseApi.upsertPermission(props.kbId, payload)
    message.success('授权已保存')
    resetForm()
    await loadPermissions()
  } catch (error) {
    message.error(error.message || '保存授权失败')
  } finally {
    saving.value = false
  }
}

const editPermission = (record) => {
  Object.assign(form, {
    subject_type: record.subject_type,
    subject_id: String(record.subject_id ?? ''),
    ...Object.fromEntries(permissionKeys.map((key) => [key, Boolean(record[key])]))
  })
}

const deletePermission = async (record) => {
  try {
    await databaseApi.deletePermission(props.kbId, record.id)
    message.success('授权已删除')
    await loadPermissions()
  } catch (error) {
    message.error(error.message || '删除授权失败')
  }
}

watch(
  () => props.kbId,
  () => {
    resetForm()
    loadPermissions()
    loadSubjectOptions()
  }
)

watch(
  () => form.subject_type,
  () => {
    form.subject_id = ''
  },
  { flush: 'sync' }
)

onMounted(() => {
  loadPermissions()
  loadSubjectOptions()
})
</script>

<style scoped>
.knowledge-permission-panel {
  padding: 20px;
  background: var(--gray-0, #fff);
  border-radius: 12px;
}

.permission-panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.permission-panel-header h3 {
  margin: 0 0 4px;
  font-size: 18px;
  font-weight: 600;
}

.permission-panel-header p {
  margin: 0;
  color: var(--gray-500, #667085);
}

.permission-form {
  padding: 14px;
  margin-bottom: 16px;
  background: var(--gray-50, #f8fafc);
  border: 1px solid var(--gray-100, #eef2f6);
  border-radius: 10px;
}
</style>
