<template>
  <a-modal
    :open="open"
    title="内容分类管理"
    width="680px"
    :footer="null"
    @cancel="$emit('update:open', false)"
  >
    <div class="category-create-row">
      <a-input v-model:value="newCategory.name" placeholder="分类名称" :maxlength="64" />
      <a-input-number v-model:value="newCategory.sort_order" placeholder="排序" />
      <a-button type="primary" :loading="saving" @click="createCategory">新增</a-button>
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
          <a-tag v-if="record.is_default" class="default-tag">默认</a-tag>
        </template>
        <template v-else-if="column.key === 'sort_order'">
          <a-input-number v-if="editingId === record.id" v-model:value="editing.sort_order" />
          <span v-else>{{ record.sort_order }}</span>
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a-button v-if="editingId !== record.id" type="link" @click="startEditing(record)">
              编辑
            </a-button>
            <a-button v-else type="link" :loading="saving" @click="saveCategory(record.id)">
              保存
            </a-button>
            <a-button
              type="link"
              danger
              :disabled="record.is_protected"
              @click="deleteCategory(record)"
            >
              删除
            </a-button>
          </a-space>
        </template>
      </template>
    </a-table>
  </a-modal>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { categoryApi } from '@/apis/knowledge_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  items: { type: Array, default: () => [] }
})
const emit = defineEmits(['update:open', 'changed'])

const columns = [
  { title: '分类名称', key: 'name' },
  { title: '排序', key: 'sort_order', width: 120 },
  { title: '知识库数', dataIndex: 'usage_count', width: 100 },
  { title: '操作', key: 'actions', width: 160 }
]
const saving = ref(false)
const editingId = ref(null)
const editing = reactive({ name: '', sort_order: 0 })
const newCategory = reactive({ name: '', sort_order: 0 })

const createCategory = async () => {
  if (!newCategory.name.trim()) {
    message.warning('请输入分类名称')
    return
  }
  saving.value = true
  try {
    await categoryApi.createCategory({ name: newCategory.name, sort_order: newCategory.sort_order })
    newCategory.name = ''
    newCategory.sort_order = 0
    emit('changed')
    message.success('分类已新增')
  } catch (error) {
    message.error(error.message || '新增分类失败')
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
    message.success('分类已更新')
  } catch (error) {
    message.error(error.message || '更新分类失败')
  } finally {
    saving.value = false
  }
}

const deleteCategory = (record) => {
  Modal.confirm({
    title: '删除内容分类',
    content: `确定删除“${record.name}”吗？使用中的分类不能删除。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await categoryApi.deleteCategory(record.id)
        emit('changed')
        message.success('分类已删除')
      } catch (error) {
        message.error(error.message || '删除分类失败')
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
