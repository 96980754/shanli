<template>
  <a-modal
    :open="visible"
    :title="createType ? (editingType === 'xlsx' ? $t('office.createExcelTitle') : $t('office.createWordTitle')) : (editingType === 'xlsx' ? $t('office.editExcelTitle') : $t('office.editWordTitle'))"
    width="900px"
    @ok="handleSave"
    @cancel="handleCancel"
    :confirm-loading="saving"
    :ok-text="$t('office.confirmAndStore')"
    :cancel-text="$t('common.cancel')"
  >
    <div v-if="loading" class="office-loading">
      <a-spin size="small" />
      <span>{{ $t('office.loadingContent') }}</span>
    </div>

    <div v-else-if="error" class="office-error">{{ error }}</div>

    <div v-if="createType && !loading && !error" class="office-filename-row">
      <span>{{ $t('office.filenameLabel') }}</span>
      <a-input v-model:value="documentFilename" size="small" />
    </div>

    <!-- Word：平台轻量富文本块 -->
    <div v-else-if="editingType === 'docx'" class="office-docx">
      <div class="office-toolbar">
        <a-button size="small" @click="addBlock('heading')">{{ $t('office.addHeading') }}</a-button>
        <a-button size="small" @click="addBlock('para')">{{ $t('office.addParagraph') }}</a-button>
        <a-button size="small" @click="addTable">{{ $t('office.addTable') }}</a-button>
        <a-button size="small" @click="formatSelection('bold')"><strong>B</strong></a-button>
        <a-button size="small" @click="formatSelection('italic')"><em>I</em></a-button>
      </div>
      <div v-for="(block, idx) in blocks" :key="idx" class="office-block">
        <div class="office-block-actions">
          <a-select v-model:value="block.kind" size="small" class="office-kind-select">
            <a-select-option value="heading">{{ $t('office.headingPlaceholder') }}</a-select-option>
            <a-select-option value="para">{{ $t('office.paragraphLabel') }}</a-select-option>
            <a-select-option value="list_item">{{ $t('office.listLabel') }}</a-select-option>
            <a-select-option value="table">{{ $t('office.tableLabel') }}</a-select-option>
          </a-select>
          <a-button type="text" danger size="small" @click="blocks.splice(idx, 1)">
            {{ $t('common.delete') }}
          </a-button>
        </div>
        <template v-if="block.kind === 'table'">
          <div class="office-table-wrap">
            <table class="office-table">
              <tbody>
                <tr v-for="(row, ri) in block.rows" :key="ri">
                  <td v-for="(_, ci) in row" :key="ci">
                    <input v-model="block.rows[ri][ci]" class="office-cell" />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <div
          v-else
          class="office-rich-text"
          :class="{ 'office-heading': block.kind === 'heading' }"
          contenteditable="true"
          spellcheck="false"
          @input="syncBlock(block, $event)"
          v-html="blockHtml(block)"
        />
      </div>
    </div>

    <!-- Excel：轻量工作表网格 -->
    <div v-else-if="editingType === 'xlsx'" class="office-xlsx">
      <div class="office-toolbar">
        <a-button size="small" @click="addSheet">{{ $t('office.addSheet') }}</a-button>
        <a-button size="small" @click="addRow(activeSheet)">{{ $t('office.addRow') }}</a-button>
        <a-button size="small" @click="addColumn(activeSheet)">{{ $t('office.addColumn') }}</a-button>
      </div>
      <a-tabs v-model:active-key="activeSheetIndex" size="small">
        <a-tab-pane v-for="(sheet, si) in sheets" :key="si" :tab="sheet.name">
          <div class="office-sheet-title">
            <a-input v-model:value="sheet.name" size="small" />
            <a-button v-if="sheets.length > 1" type="text" danger size="small" @click="removeSheet(si)">
              {{ $t('common.delete') }}
            </a-button>
          </div>
          <div class="office-table-wrap">
            <table class="office-table">
              <tbody>
                <tr v-for="(row, ri) in sheet.rows" :key="ri">
                  <td v-for="(_, ci) in row" :key="ci">
                    <input v-model="sheet.rows[ri][ci]" class="office-cell" />
                  </td>
                  <td><a-button type="text" danger size="small" @click="sheet.rows.splice(ri, 1)">×</a-button></td>
                </tr>
              </tbody>
            </table>
          </div>
        </a-tab-pane>
      </a-tabs>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { message } from 'ant-design-vue'
import { documentApi } from '@/apis/knowledge_api'

const { t } = useI18n()

const props = defineProps({
  visible: { type: Boolean, default: false },
  kbId: { type: String, default: null },
  docId: { type: String, default: null },   // 已入库文档（可选） // i18n-ignore
  filePath: { type: String, default: '' }, // 已上传未入库的 MinIO URL（可选） // i18n-ignore
  filename: { type: String, default: '' },
  createType: { type: String, default: '' },
  parentId: { type: String, default: null }
})
const emit = defineEmits(['update:visible', 'success', 'writeback'])

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const editingType = ref('')
const blocks = ref([])
const sheets = ref([])

const activeSheetIndex = ref(0)
const documentFilename = ref('')

const initializeCreateContent = () => {
  editingType.value = props.createType
  documentFilename.value = props.filename || `新建文档.${props.createType}`
  if (props.createType === 'xlsx') {
    sheets.value = [{ name: 'Sheet1', rows: [['']] }]
    activeSheetIndex.value = 0
  } else {
    blocks.value = [{ kind: 'heading', level: 1, text: '', runs: [] }, { kind: 'para', text: '', runs: [] }]
  }
}

const loadContent = async () => {
  if (props.createType) {
    initializeCreateContent()
    return
  }
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
    console.error(t('office.loadFailedLog'), e)
    error.value = e?.message || t('office.loadFailed')
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
      activeSheetIndex.value = 0
      loadContent()
    }
  }
)

const escapeHtml = (value) =>
  String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')

const blockHtml = (block) => {
  if (block.runs?.length) {
    return block.runs
      .map((run) => {
        const text = escapeHtml(run.text).replaceAll('\n', '<br>')
        if (run.bold && run.italic) return `<strong><em>${text}</em></strong>`
        if (run.bold) return `<strong>${text}</strong>`
        if (run.italic) return `<em>${text}</em>`
        return text
      })
      .join('')
  }
  return escapeHtml(block.text).replaceAll('\n', '<br>')
}

const syncBlock = (block, event) => {
  const root = event.currentTarget
  const runs = []
  const walk = (node, marks = {}) => {
    if (node.nodeType === Node.TEXT_NODE) {
      if (node.nodeValue) runs.push({ text: node.nodeValue, ...marks })
      return
    }
    const nextMarks = {
      bold: marks.bold || node.tagName === 'B' || node.tagName === 'STRONG',
      italic: marks.italic || node.tagName === 'I' || node.tagName === 'EM'
    }
    node.childNodes.forEach((child) => walk(child, nextMarks))
  }
  root.childNodes.forEach((child) => walk(child))
  block.runs = runs
  block.text = runs.map((run) => run.text).join('')
}

const formatSelection = (command) => {
  document.execCommand(command)
}

const addBlock = (kind) => {
  blocks.value.push({ kind, level: 1, text: '', runs: [] })
}

const addTable = () => {
  blocks.value.push({ kind: 'table', rows: [['', ''], ['', '']] })
}

const activeSheet = computed(() => sheets.value[activeSheetIndex.value] || null)
const addSheet = () => {
  sheets.value.push({ name: `Sheet${sheets.value.length + 1}`, rows: [['']] })
  activeSheetIndex.value = sheets.value.length - 1
}
const removeSheet = (index) => {
  if (sheets.value.length <= 1) return
  sheets.value.splice(index, 1)
  activeSheetIndex.value = Math.min(activeSheetIndex.value, sheets.value.length - 1)
}
const addRow = (sheet) => {
  if (!sheet) return
  const width = Math.max(1, ...sheet.rows.map((row) => row.length))
  sheet.rows.push(Array(width).fill(''))
}
const addColumn = (sheet) => {
  if (!sheet) return
  if (!sheet.rows.length) sheet.rows.push([''])
  sheet.rows.forEach((row) => row.push(''))
}

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
    if (props.createType) {
      const res = await documentApi.createOfficeDocument(props.kbId, {
        ...payload,
        filename: documentFilename.value || payload.filename,
        parent_id: props.parentId
      })
      message.success(res?.message || t('office.documentCreated'))
    } else if (props.filePath) {
      const res = await documentApi.officeWriteback(props.kbId, payload)
      emit('writeback', res)
      message.success(t('office.generatedReadyToStore'))
    } else {
      // docId 模式：编辑已入库文档，写回并重新入库
      const res = await documentApi.saveEditedDocument(props.kbId, props.docId, payload)
      message.success(res?.message || t('office.documentUpdated'))
    }
    emit('update:visible', false)
    emit('success')
  } catch (e) {
    console.error(t('office.saveFailedLog'), e)
    message.error(e?.message || t('common.saveFailed'))
  } finally {
    saving.value = false
  }
}

const handleCancel = () => {
  emit('update:visible', false)
}
</script>

<style scoped lang="less">
.office-filename-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--gray-700);
  .ant-input {
    max-width: 360px;
  }
}
.office-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.office-block-actions,
.office-sheet-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.office-kind-select {
  width: 110px;
}
.office-rich-text {
  min-height: 42px;
  padding: 8px 10px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  line-height: 1.6;
  white-space: pre-wrap;
  outline: none;
}
.office-rich-text:focus {
  border-color: var(--main-color);
  box-shadow: 0 0 0 2px var(--color-primary-50);
}
.office-rich-text.office-heading {
  font-size: 18px;
  font-weight: 600;
}
.office-sheet-title :deep(.ant-input) {
  max-width: 240px;
}
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
