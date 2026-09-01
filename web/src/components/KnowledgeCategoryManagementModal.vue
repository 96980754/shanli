<template>
  <a-modal
    :open="open"
    :title="$t('kbCategory.manageTitle')"
    width="680px"
    :footer="null"
    @cancel="$emit('update:open', false)"
  >
    <div class="category-create-row">
      <a-input v-model:value="newCategory.name" :placeholder="$t('kbCategory.namePlaceholder')" :maxlength="64" />
      <a-input-number v-model:value="newCategory.sort_order" :placeholder="$t('kbCategory.sortPlaceholder')" />
      <a-button type="primary" :loading="saving" @click="createCategory">{{ $t('kbCategory.add') }}</a-button>
    </div>

    <a-table :data-source="items" :columns="columns" :pagination="false" row-key="id" size="small">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">
          <a-input
            v-if="editingId === record.id"
            v-model:value="editing.name"
            :disabled="record.is_protected"
            :maxlength="64"
          />
          <span v-else>{{ record.name }}</span>
          <a-tag v-if="record.is_default" class="default-tag">{{ $t('kbCategory.defaultTag') }}</a-tag>
        </template>
        <template v-else-if="column.key === 'sort_order'">
          <a-input-number v-if="editingId === record.id" v-model:value="editing.sort_order" />
          <span v-else>{{ record.sort_order }}</span>
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a-button v-if="editingId !== record.id" type="link" @click="startEditing(record)">
              {{ $t('common.edit') }}
            </a-button>
            <a-button v-else type="link" :loading="saving" @click="saveCategory(record.id)">
              {{ $t('common.save') }}
            </a-button>
            <a-button
              type="link"
              danger
              :disabled="record.is_protected"
              @click="deleteCategory(record)"
            >
              {{ $t('common.delete') }}
            </a-button>
          </a-space>
        </template>
      </template>
    </a-table>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { message, Modal } from 'ant-design-vue'
import { categoryApi } from '@/apis/knowledge_api'

defineProps({
  open: { type: Boolean, default: false },
  items: { type: Array, default: () => [] }
})
const emit = defineEmits(['update:open', 'changed'])

const { t } = useI18n()

const columns = computed(() => [
  { title: t('kbCategory.nameColumn'), key: 'name' },
  { title: t('kbCategory.sortColumn'), key: 'sort_order', width: 120 },
  { title: t('kbCategory.usageColumn'), dataIndex: 'usage_count', width: 100 },
  { title: t('kbCategory.actionsColumn'), key: 'actions', width: 160 }
])
const saving = ref(false)
const editingId = ref(null)
const editing = reactive({ name: '', sort_order: 0 })
const newCategory = reactive({ name: '', sort_order: 0 })

const createCategory = async () => {
  if (!newCategory.name.trim()) {
    message.warning(t('kbCategory.nameRequired'))
    return
  }
  saving.value = true
  try {
    await categoryApi.createCategory({ name: newCategory.name, sort_order: newCategory.sort_order })
    newCategory.name = ''
    newCategory.sort_order = 0
    emit('changed')
    message.success(t('kbCategory.added'))
  } catch (error) {
    message.error(error.message || t('kbCategory.addFailed'))
  } finally {
    saving.value = false
  }
}

const startEditing = (record) => {
  editingId.value = record.id
  editing.name = record.name
  editing.sort_order = record.sort_order
}

const saveCategory = async (categoryId) => {
  saving.value = true
  try {
    await categoryApi.updateCategory(categoryId, {
      name: editing.name,
      sort_order: editing.sort_order
    })
    editingId.value = null
    emit('changed')
    message.success(t('kbCategory.updated'))
  } catch (error) {
    message.error(error.message || t('kbCategory.updateFailed'))
  } finally {
    saving.value = false
  }
}

const deleteCategory = (record) => {
  Modal.confirm({
    title: t('kbCategory.deleteTitle'),
    content: t('kbCategory.deleteContent', { name: record.name }),
    okText: t('common.delete'),
    okType: 'danger',
    cancelText: t('common.cancel'),
    onOk: async () => {
      try {
        await categoryApi.deleteCategory(record.id)
        emit('changed')
        message.success(t('kbCategory.deleted'))
      } catch (error) {
        message.error(error.message || t('kbCategory.deleteFailed'))
        throw error
      }
    }
  })
}
</script>

<style lang="less" scoped>
.category-create-row {
  display: grid;
  grid-template-columns: 1fr 120px auto;
  gap: 8px;
  margin-bottom: 16px;
}

.default-tag {
  margin-left: 8px;
}
</style>
