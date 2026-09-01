<template>
  <section class="knowledge-permission-panel">
    <div class="permission-panel-header">
      <div>
        <h3>{{ $t('perm.panelTitle') }}</h3>
        <p>{{ $t('perm.panelIntro') }}</p>
      </div>
      <a-button type="primary" :loading="loading" @click="loadPermissions">{{ $t('common.refresh') }}</a-button>
    </div>

    <a-form class="permission-form" layout="inline" :model="form">
      <a-form-item :label="$t('perm.subjectTypeLabel')">
        <a-select v-model:value="form.subject_type" style="width: 120px">
          <a-select-option value="user">{{ $t('perm.subjectUser') }}</a-select-option>
          <a-select-option value="department">{{ $t('perm.subjectDepartment') }}</a-select-option>
          <a-select-option value="team">{{ $t('perm.subjectTeam') }}</a-select-option>
          <a-select-option value="role">{{ $t('perm.subjectRole') }}</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item :label="$t('perm.subjectLabel')">
        <a-select
          v-model:value="form.subject_id"
          show-search
          allow-clear
          :loading="optionLoading"
          :options="subjectOptions"
          style="width: 260px"
          :placeholder="$t('perm.subjectPlaceholder')"
          option-filter-prop="label"
        />
      </a-form-item>
      <a-form-item :label="$t('perm.presetLabel')">
        <a-space>
          <a-button
            v-for="preset in Object.entries(permissionPresets)"
            :key="preset[0]"
            size="small"
            @click="applyPreset(preset[0])"
          >
            {{ presetLabel(preset[0]) }}
          </a-button>
        </a-space>
      </a-form-item>
      <a-form-item>
        <a-space wrap>
          <a-checkbox
            v-for="item in permissionOptions"
            :key="item.key"
            v-model:checked="form[item.key]"
          >
            {{ permissionOptionLabel(item) }}
          </a-checkbox>
        </a-space>
      </a-form-item>
      <a-form-item>
        <a-button type="primary" :loading="saving" @click="savePermission">{{ $t('perm.savePermission') }}</a-button>
      </a-form-item>
    </a-form>

    <a-alert
      type="info"
      show-icon
      class="permission-note"
      :message="$t('perm.adminNote')"
    />

    <a-table
      row-key="key"
      :columns="columns"
      :data-source="accessRows"
      :loading="loading"
      :pagination="false"
      size="middle"
      :scroll="{ x: 'max-content' }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="permissionKeys.includes(column.key)">
          <a-tag :color="record[column.key] ? 'green' : 'default'">
            {{ record[column.key] ? $t('perm.allowed') : '—' }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'subject_type'">
          <a-tag>{{ subjectTypeLabel(record.subject_type) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'subject_id'">
          {{ rowLabel(record) }}
        </template>
        <template v-else-if="column.key === 'sources'">
          <a-tag v-for="source in record.sources" :key="source" :color="sourceColor(source)">
            {{ sourceLabel(source) }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space v-if="record.editable">
            <a-button type="link" size="small" @click="editPermission(record)">{{ $t('common.edit') }}</a-button>
            <a-popconfirm :title="$t('perm.deleteConfirmTitle')" @confirm="deletePermission(record)">
              <a-button type="link" danger size="small">{{ $t('common.delete') }}</a-button>
            </a-popconfirm>
          </a-space>
          <span v-else>—</span>
        </template>
      </template>
    </a-table>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { message } from 'ant-design-vue'
import { databaseApi } from '@/apis/knowledge_api'
import { authApi } from '@/apis/auth_api'
import { departmentApi } from '@/apis/department_api'
import {
  buildAccessRows,
  buildPermissionPayload,
  permissionKeys,
  permissionOptions,
  permissionPresets,
  roleOptions
} from './knowledgePermissionPanelHelpers'

const props = defineProps({
  kbId: {
    type: String,
    required: true
  },
  database: {
    type: Object,
    default: () => ({})
  }
})

const { t } = useI18n()

const permissionOptionKeyMap = {
  can_view: 'common.view',
  can_search: 'perm.search',
  can_upload: 'common.upload',
  can_download: 'common.download',
  can_delete: 'common.delete',
  can_manage: 'perm.manage',
  can_grant: 'perm.grant',
  can_export: 'perm.export'
}
const permissionOptionLabel = (item) => t(permissionOptionKeyMap[item.key] || item.key)

const presetLabelKeyMap = {
  readonly: 'perm.presetReadonly',
  editor: 'perm.presetEditor',
  manager: 'perm.presetManager'
}
const presetLabel = (presetKey) => t(presetLabelKeyMap[presetKey] || presetKey)

const columns = computed(() => [
  { title: t('perm.subjectTypeColumn'), dataIndex: 'subject_type', key: 'subject_type', width: 90 },
  { title: t('perm.subjectColumn'), dataIndex: 'subject_id', key: 'subject_id', width: 200 },
  { title: t('perm.sourceColumn'), dataIndex: 'sources', key: 'sources', width: 160 },
  ...permissionOptions.map((item) => ({
    title: permissionOptionLabel(item),
    dataIndex: item.key,
    key: item.key,
    width: 82
  })),
  { title: t('perm.actionsColumn'), key: 'actions', fixed: 'right', width: 130 }
])

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
const teams = ref([])

const userOptions = computed(() =>
  users.value.map((item) => ({
    value: item.uid,
    label: item.username ? t('perm.userOptionLabel', { username: item.username, uid: item.uid }) : item.uid
  }))
)

const departmentOptions = computed(() =>
  departments.value.map((item) => ({
    value: String(item.id),
    // 部门名唯一，下拉选项不展示内部 ID（数字易被误读为成员数）
    label: item.name ? item.name : String(item.id)
  }))
)

// 团队选项按部门分组级联（部门名 › 团队名），与行标签渲染一致
const teamOptions = computed(() => {
  const groups = new Map()
  for (const team of teams.value) {
    const deptId = String(team.department_id)
    if (!groups.has(deptId)) {
      const department = departments.value.find((item) => String(item.id) === deptId)
      groups.set(deptId, {
        label: department ? department.name : t('perm.departmentFallbackLabel', { deptId }),
        options: []
      })
    }
    groups.get(deptId).options.push({ value: String(team.id), label: team.name })
  }
  return [...groups.values()]
})

const translatedRoleOptions = computed(() =>
  roleOptions.map((item) => ({ ...item, label: t(`user.role.${item.value}`) }))
)

const subjectOptions = computed(() => {
  if (form.subject_type === 'department') return departmentOptions.value
  if (form.subject_type === 'team') return teamOptions.value
  if (form.subject_type === 'role') return translatedRoleOptions.value
  return userOptions.value
})

const accessRows = computed(() =>
  buildAccessRows({
    createdBy: props.database?.created_by,
    shareConfig: props.database?.share_config,
    permissions: permissions.value,
    users: users.value,
    departments: departments.value,
    teams: teams.value
  })
)

const sourceColor = (source) => {
  if (source === '创建者') return 'gold' // i18n-ignore
  if (source === '共享设置') return 'blue' // i18n-ignore
  return 'green'
}

const sourceLabel = (source) => {
  if (source === '创建者') return t('perm.sourceCreator') // i18n-ignore
  if (source === '共享设置') return t('perm.sourceShare') // i18n-ignore
  return t('perm.sourceGrant')
}

const rowLabel = (record) => {
  if (record.subject_type === 'global') return t('perm.allUsers')
  if (record.subject_type === 'role') {
    const role = roleOptions.find((item) => item.value === String(record.subject_id))
    if (role) return t('perm.roleLabel', { label: t(`user.role.${role.value}`), id: record.subject_id })
  }
  return record.label
}

const resetForm = () => {
  Object.assign(form, emptyForm())
}

const subjectTypeLabel = (type) => {
  if (type === 'department') return t('perm.subjectDepartment')
  if (type === 'team') return t('perm.subjectTeam')
  if (type === 'role') return t('perm.subjectRole')
  if (type === 'global') return t('perm.subjectGlobal')
  return t('perm.subjectUser')
}

const loadSubjectOptions = async () => {
  optionLoading.value = true
  try {
    const [userResult, departmentResult, teamResult] = await Promise.all([
      authApi.getUserAccessOptions(),
      departmentApi.getDepartments(),
      departmentApi.getAllTeams()
    ])
    users.value = Array.isArray(userResult) ? userResult : []
    departments.value = departmentResult.departments || departmentResult || []
    teams.value = Array.isArray(teamResult) ? teamResult : []
  } catch (error) {
    message.warning(error.message || t('perm.loadSubjectsFailed'))
    users.value = []
    departments.value = []
    teams.value = []
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
    message.error(error.message || t('perm.loadFailed'))
  } finally {
    loading.value = false
  }
}

const savePermission = async () => {
  const payload = buildPermissionPayload(form)
  if (!payload.subject_id) {
    message.warning(t('perm.selectSubjectRequired'))
    return
  }
  saving.value = true
  try {
    await databaseApi.upsertPermission(props.kbId, payload)
    message.success(t('perm.saved'))
    resetForm()
    await loadPermissions()
  } catch (error) {
    message.error(error.message || t('perm.saveFailed'))
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
    message.success(t('perm.deleted'))
    await loadPermissions()
  } catch (error) {
    message.error(error.message || t('perm.deleteFailed'))
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

.permission-note {
  margin-bottom: 16px;
}

.permission-form {
  padding: 14px;
  margin-bottom: 16px;
  background: var(--gray-50, #f8fafc);
  border: 1px solid var(--gray-100, #eef2f6);
  border-radius: 10px;
}
</style>
