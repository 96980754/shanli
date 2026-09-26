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

    <template v-else>
      <!-- 新建模式：文件名行与编辑器并列渲染（不能进 v-else-if 链，否则编辑器永不出现） -->
      <div v-if="createType" class="office-filename-row">
        <FileSpreadsheet v-if="editingType === 'xlsx'" class="office-filename-icon" :size="15" />
        <FileText v-else class="office-filename-icon" :size="15" />
        <span>{{ $t('office.filenameLabel') }}</span>
        <a-input v-model:value="documentFilename" size="small" />
      </div>

      <!-- Word：平台轻量富文本块 -->
      <div v-if="editingType === 'docx'" class="office-docx">
        <div class="office-toolbar">
          <a-button type="text" size="small" @click="addBlock('heading')">
            <template #icon><Heading :size="14" /></template>
            {{ $t('office.addHeading') }}
          </a-button>
          <a-button type="text" size="small" @click="addBlock('para')">
            <template #icon><Pilcrow :size="14" /></template>
            {{ $t('office.addParagraph') }}
          </a-button>
          <a-button type="text" size="small" @click="addTable">
            <template #icon><Table :size="14" /></template>
            {{ $t('office.addTable') }}
          </a-button>
          <span class="office-tool-sep" />
          <a-button type="text" size="small" class="office-format-btn" @click="formatSelection('bold')">
            <template #icon><Bold :size="14" /></template>
          </a-button>
          <a-button type="text" size="small" class="office-format-btn" @click="formatSelection('italic')">
            <template #icon><Italic :size="14" /></template>
          </a-button>
        </div>
        <div v-for="(block, idx) in blocks" :key="idx" class="office-block">
          <div class="office-block-actions">
            <a-select v-model:value="block.kind" size="small" class="office-kind-select">
              <a-select-option value="heading">{{ $t('office.headingPlaceholder') }}</a-select-option>
              <a-select-option value="para">{{ $t('office.paragraphLabel') }}</a-select-option>
              <a-select-option value="list_item">{{ $t('office.listLabel') }}</a-select-option>
              <a-select-option value="table">{{ $t('office.tableLabel') }}</a-select-option>
            </a-select>
            <a-button type="text" danger size="small" :title="$t('common.delete')" @click="blocks.splice(idx, 1)">
              <template #icon><Trash2 :size="14" /></template>
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
            :data-placeholder="blockPlaceholder(block.kind)"
            @input="syncBlock(block, $event)"
            v-office-html="blockHtml(block)"
          />
        </div>
      </div>

      <!-- Excel：轻量工作表网格 -->
      <div v-else-if="editingType === 'xlsx'" class="office-xlsx">
        <div class="office-toolbar">
          <a-button type="text" size="small" @click="addSheet">
            <template #icon><Sheet :size="14" /></template>
            {{ $t('office.addSheet') }}
          </a-button>
          <a-button type="text" size="small" @click="addRow(activeSheet)">
            <template #icon><Rows3 :size="14" /></template>
            {{ $t('office.addRow') }}
          </a-button>
          <a-button type="text" size="small" @click="addColumn(activeSheet)">
            <template #icon><Columns3 :size="14" /></template>
            {{ $t('office.addColumn') }}
          </a-button>
        </div>
        <a-tabs v-model:active-key="activeSheetIndex" size="small">
          <a-tab-pane v-for="(sheet, si) in sheets" :key="si" :tab="sheet.name">
            <div class="office-sheet-title">
              <span>{{ $t('office.sheetNameLabel') }}</span>
              <a-input v-model:value="sheet.name" size="small" />
              <a-button v-if="sheets.length > 1" type="text" danger size="small" :title="$t('common.delete')" @click="removeSheet(si)">
                <template #icon><Trash2 :size="14" /></template>
              </a-button>
            </div>
            <div class="office-table-wrap">
              <table class="office-table">
                <thead>
                  <tr>
                    <th class="office-grid-corner"></th>
                    <th v-for="(_, ci) in sheetColCount(sheet)" :key="ci">{{ colLabel(ci) }}</th>
                    <th class="office-grid-corner"></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, ri) in sheet.rows" :key="ri">
                    <td class="office-grid-row">{{ ri + 1 }}</td>
                    <td v-for="(_, ci) in row" :key="ci">
                      <input v-model="sheet.rows[ri][ci]" class="office-cell" />
                    </td>
                    <td class="office-row-del">
                      <button type="button" class="office-row-del-btn" :title="$t('common.delete')" @click="sheet.rows.splice(ri, 1)">
                        <X :size="13" />
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </a-tab-pane>
        </a-tabs>
      </div>
    </template>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { message } from 'ant-design-vue'
import {
  Bold, Columns3, FileSpreadsheet, FileText, Heading, Italic,
  Pilcrow, Rows3, Sheet, Table, Trash2, X
} from 'lucide-vue-next'
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

// contenteditable 不能用 v-html 受控重渲：每次 input 更新 runs 都会重设 innerHTML，
// 把光标重置到开头导致输入倒序。挂载时写入一次；更新时仅当元素未聚焦（编辑中 DOM 即事实来源）
// 且内容确实变化（如 execCommand 产生的 <b> 归一化为 <strong>）才覆盖。
const vOfficeHtml = {
  mounted: (el, binding) => {
    el.innerHTML = binding.value
  },
  updated: (el, binding) => {
    if (document.activeElement !== el && el.innerHTML !== binding.value) {
      el.innerHTML = binding.value
    }
  }
}

const BLOCK_PLACEHOLDER_KEYS = { heading: 'office.headingPh', para: 'office.paraPh', list_item: 'office.listPh' }
const blockPlaceholder = (kind) => t(BLOCK_PLACEHOLDER_KEYS[kind] || 'office.paraPh')

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

// 表格网格装饰：行号 + A/B/C 列标（仅视觉，不入数据）
const sheetColCount = (sheet) => sheet.rows.reduce((max, row) => Math.max(max, row.length), 0)
const colLabel = (index) => {
  let label = ''
  let n = index
  while (n >= 0) {
    label = String.fromCharCode(65 + (n % 26)) + label
    n = Math.floor(n / 26) - 1
  }
  return label
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
// 画布：灰底圆角面板，Word 块卡片 / Excel 网格以白底浮在其上，模拟文档页/表格页的层次
.office-docx,
.office-xlsx {
  max-height: 70vh;
  padding: 12px;
  overflow: auto;
  background: var(--gray-50);
  border: 1px solid var(--gray-100);
  border-radius: 8px;
}

.office-filename-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--gray-700);

  :deep(.ant-input) {
    max-width: 360px;
  }
}
.office-filename-icon {
  flex-shrink: 0;
  color: var(--main-600);
}

.office-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  padding: 6px 8px;
  margin-bottom: 12px;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.office-tool-sep {
  width: 1px;
  height: 16px;
  margin: 0 4px;
  background: var(--gray-200);
}
.office-format-btn {
  min-width: 32px;
  padding-inline: 6px;
}

.office-block {
  margin-bottom: 10px;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  transition: border-color 0.15s ease;

  &:hover {
    border-color: var(--gray-200);
  }
  &:last-child {
    margin-bottom: 0;
  }
}
.office-block-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 4px 4px 8px;
  background: var(--gray-10);
  border-bottom: 1px solid var(--gray-100);
  border-radius: 8px 8px 0 0;
}
.office-kind-select {
  width: 110px;
}

.office-rich-text {
  min-height: 46px;
  padding: 9px 12px;
  border-radius: 0 0 8px 8px;
  font-size: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  outline: none;

  &:focus {
    box-shadow: inset 0 0 0 2px var(--color-primary-100);
  }
  &:empty::before {
    content: attr(data-placeholder);
    color: var(--gray-400);
    pointer-events: none;
  }
}
.office-rich-text.office-heading {
  font-size: 18px;
  font-weight: 600;
}

.office-table-wrap {
  overflow-x: auto;
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 8px;
}
.office-block .office-table-wrap {
  border: none;
  border-radius: 0 0 8px 8px;
}

.office-table {
  width: 100%;
  border-collapse: collapse;

  td {
    border: 1px solid var(--gray-150);
    padding: 0;
  }
  th {
    border: 1px solid var(--gray-150);
    background: var(--gray-25);
    padding: 3px 8px;
    font-size: 12px;
    font-weight: 500;
    color: var(--gray-500);
    text-align: center;
    user-select: none;
  }
  .office-grid-row {
    min-width: 36px;
    padding: 3px 8px;
    background: var(--gray-25);
    color: var(--gray-500);
    font-size: 12px;
    text-align: center;
    user-select: none;
  }
  .office-row-del {
    width: 34px;
    padding: 2px;
    background: var(--gray-25);
    text-align: center;
  }
}
.office-row-del-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--gray-400);
  cursor: pointer;

  &:hover {
    color: var(--color-error-500);
    background: var(--color-error-50);
  }
}
.office-cell {
  width: 100%;
  min-width: 96px;
  height: 30px;
  padding: 0 10px;
  border: none;
  background: transparent;
  font-size: 13px;
  outline: none;

  &:focus {
    background: var(--main-10);
    box-shadow: inset 0 0 0 2px var(--main-500);
  }
}

.office-sheet-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--gray-600);

  :deep(.ant-input) {
    max-width: 220px;
    background: var(--gray-0);
  }
}
.office-xlsx :deep(.ant-tabs-nav) {
  margin-bottom: 8px;
}

.office-loading,
.office-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 160px;
  font-size: 13px;
  color: var(--gray-600);
}
.office-error {
  color: var(--color-error-700);
}
</style>
