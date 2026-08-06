<template>
  <a-modal
    :open="visible"
    :title="editingType === 'xlsx' ? '编辑 Excel 单元格' : '编辑 Word 文字'"
    width="900px"
    @ok="handleSave"
    @cancel="handleCancel"
    :confirm-loading="saving"
    ok-text="确认并入库"
    cancel-text="取消"
  >
    <div v-if="loading" class="office-loading">
      <a-spin size="small" />
      <span>加载文档内容...</span>
    </div>

    <div v-else-if="error" class="office-error">{{ error }}</div>

    <!-- Word：段落/标题/表格 -->
    <div v-else-if="editingType === 'docx'" class="office-docx">
      <div v-for="(block, idx) in blocks" :key="idx" class="office-block">
        <template v-if="block.kind === 'heading'">
          <a-input
            v-model:value="block.text"
            class="office-heading"
            placeholder="标题"
          />
        </template>
        <template v-else-if="block.kind === 'table'">
          <div class="office-table-wrap">
            <table class="office-table">
              <tbody>
                <tr v-for="(row, ri) in block.rows" :key="ri">
                  <td v-for="(cell, ci) in row" :key="ci">
                    <input v-model="block.rows[ri][ci]" class="office-cell" />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <template v-else>
          <a-textarea
            v-model:value="block.text"
            class="office-para"
            :rows="2"
            :auto-size="{ minRows: 1, maxRows: 6 }"
          />
        </template>
      </div>
    </div>

    <!-- Excel：工作表单元格矩阵 -->
    <div v-else-if="editingType === 'xlsx'" class="office-xlsx">
      <div v-for="(sheet, si) in sheets" :key="si" class="office-sheet">
        <div class="office-sheet-title">{{ sheet.name }}</div>
        <div class="office-table-wrap">
          <table class="office-table">
            <tbody>
              <tr v-for="(row, ri) in sheet.rows" :key="ri">
                <td v-for="(cell, ci) in row" :key="ci">
                  <input v-model="sheet.rows[ri][ci]" class="office-cell" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { documentApi } from '@/apis/knowledge_api'

const props = defineProps({
  visible: { type: Boolean, default: false },
  kbId: { type: String, default: null },
  docId: { type: String, default: null },   // 已入库文档（可选）
  filePath: { type: String, default: '' }, // 已上传未入库的 MinIO URL（可选）
  filename: { type: String, default: '' }
})
const emit = defineEmits(['update:visible', 'success', 'writeback'])

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const editingType = ref('')
const blocks = ref([])
const sheets = ref([])

const loadContent = async () => {
  if (!props.kbId || (!props.docId && !props.filePath)) return
  loading.value = true
  error.value = ''
  try {
    const data = props.filePath
      ? await documentApi.getOfficeContentByPath(props.kbId, props.filePath, props.filename)
      : await documentApi.getOfficeContent(props.kbId, props.docId)
    editingType.value = data.type
    if (data.type === 'docx') {
      blocks.value = (data.blocks || []).map((b) => JSON.parse(JSON.stringify(b)))
    } else {
      sheets.value = (data.sheets || []).map((s) => JSON.parse(JSON.stringify(s)))
    }
  } catch (e) {
    console.error('加载 Office 内容失败:', e)
    error.value = e?.message || '加载文档内容失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => props.visible,
  (open) => {
    if (open) {
      editingType.value = ''
      blocks.value = []
      sheets.value = []
      loadContent()
    }
  }
)

const handleSave = async () => {
  saving.value = true
  try {
    const payload = {
      content_type: editingType.value,
      filename: props.filename || `edited.${editingType.value}`
    }
    if (editingType.value === 'docx') {
      payload.blocks = blocks.value
    } else {
      payload.sheets = sheets.value
    }
    // filePath 模式：上传时编辑，写回拿新 file_path 交给父组件入库
    if (props.filePath) {
      const res = await documentApi.officeWriteback(props.kbId, payload)
      emit('writeback', res)
      message.success('编辑内容已生成，确认入库即可')
    } else {
      // docId 模式：编辑已入库文档，写回并重新入库
      const res = await documentApi.saveEditedDocument(props.kbId, props.docId, payload)
      message.success(res?.message || '文档已更新并重新入库')
    }
    emit('update:visible', false)
    emit('success')
  } catch (e) {
    console.error('保存编辑后文档失败:', e)
    message.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

const handleCancel = () => {
  emit('update:visible', false)
}
</script>

<style scoped lang="less">
.office-loading,
.office-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 24px;
  color: var(--gray-600);
}
.office-error {
  color: var(--color-error-700);
}
.office-docx,
.office-xlsx {
  max-height: 70vh;
  overflow: auto;
}
.office-block {
  margin-bottom: 8px;
}
.office-heading {
  font-weight: 600;
  margin-bottom: 4px;
}
.office-para {
  font-size: 13px;
}
.office-sheet {
  margin-bottom: 16px;
}
.office-sheet-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--gray-800);
}
.office-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
}
.office-table {
  width: 100%;
  border-collapse: collapse;
  td {
    border: 1px solid var(--gray-150);
    padding: 2px;
  }
}
.office-cell {
  width: 100%;
  min-width: 60px;
  border: none;
  padding: 4px 6px;
  font-size: 13px;
  background: transparent;
  &:focus {
    outline: 1px solid var(--color-primary-500);
    background: var(--gray-25);
  }
}
</style>
