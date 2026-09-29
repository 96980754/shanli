import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const dir = dirname(fileURLToPath(import.meta.url))
const source = (name) => readFileSync(join(dir, '..', name), 'utf8')
const page = readFileSync(join(dir, '../../views/DataBaseInfoView.vue'), 'utf8')
const api = readFileSync(join(dir, '../../apis/knowledge_api.js'), 'utf8')
const upload = source('FlowchartUploadModal.vue')
const review = source('FlowchartReviewModal.vue')
const table = source('FileTable.vue')

assert.match(page, /<FileUploadModal/)
assert.match(page, /<FlowchartUploadModal/)
assert.match(page, /v-if="kbPermissions\.can_upload"[\s\S]*?flowchartUploadVisible = true/)
assert.match(page, /@open-flowchart="openFlowchartReview"/)
assert.match(page, /onBeforeRouteLeave\(confirmReviewNavigation\)/)
assert.match(page, /onBeforeRouteUpdate\(confirmReviewNavigation\)/)
assert.match(table, /row\.ingestion_type === 'flowchart'/)
assert.match(table, /emit\('open-flowchart', record\.file_id\)/)

assert.match(upload, /accept="application\/pdf,\.pdf"/)
assert.match(upload, /isPdfFlowchartFile\(file\)/)
assert.match(api, /export const fileApi = \{[\s\S]*?uploadFile: async/)
assert.match(upload, /import \{ fileApi, flowchartApi \} from '@\/apis\/knowledge_api'/)
assert.match(upload, /fileApi\.uploadFile\(selectedFile\.value, props\.kbId\)/)
assert.match(upload, /flowchartApi\.createFlowchart\(props\.kbId, uploaded\.file_path\)/)
assert.match(upload, /phase\.value = 'parsing'/)

assert.match(review, /getWorkspaceKnowledgeFileContent\(props\.kbId, props\.fileId\)/)
assert.match(review, /<AgentFilePreview/)
assert.match(review, /<MarkdownPreview/)
assert.match(review, /isFlowchartDraftDirty\(savedMarkdown\.value, currentMarkdown\.value\)/)
assert.match(review, /preview\.value\.revision,[\s\S]*?currentMarkdown\.value/)
assert.match(review, /cause\.response\?\.status === 409/)
assert.match(review, /conflict\.value = true/)
assert.match(review, /@click="loadPreview"/)
assert.match(review, /dirty\.value \? t\('flowchart\.reparseDirtyConfirm'\)/)
assert.match(review, /flowchartApi\.reparseFlowchart/)
assert.match(review, /await loadPreview\(\)/)
assert.match(review, /savedMarkdown \? t\('flowchart\.failedWithDraft'\)/)
assert.match(review, /getFlowchartWarningKeys/)
assert.match(review, /canEditFlowchartDraft/)
assert.match(
  review,
  /getFileStatusView\(reparsing \? 'flowchart_parsing' : preview\.status\)\.label/
)
assert.match(review, /t\('flowchart\.reparsing'\)/)
assert.match(review, /if \(!editable\.value \|\| !savedMarkdown\.value \|\| dirty\.value/)
assert.match(
  review,
  /flowchartApi\.confirmFlowchart\(props\.kbId, props\.fileId, preview\.value\.revision\)/
)
assert.match(review, /flowchartApi\.retryFlowchartIndex\(props\.kbId, props\.fileId\)/)

for (const method of [
  'createFlowchart',
  'getFlowchartPreview',
  'updateFlowchartDraft',
  'reparseFlowchart',
  'confirmFlowchart',
  'retryFlowchartIndex'
]) {
  assert.match(api, new RegExp(`${method}:`))
}

console.log('FlowchartReviewWorkflow: component/API contract assertions passed')
