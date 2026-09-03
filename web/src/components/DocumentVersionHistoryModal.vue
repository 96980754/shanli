<template>
  <a-modal
    v-model:open="visible"
    :title="compareMode ? $t('docModal.compareVersions') : $t('docModal.versionHistoryTitle')"
    :width="compareMode ? 1040 : 840"
    :footer="null"
  >
    <a-spin :spinning="versionLoading || diffLoading">
      <template v-if="compareMode">
        <!-- ============ 版本对比视图 ============ -->
        <div class="diff-toolbar">
          <a-button size="small" @click="exitCompare">
            <template #icon><CornerUpLeft :size="14" /></template>
            {{ $t('docModal.backToVersionList') }}
          </a-button>
          <div class="diff-pickers">
            <a-select
              v-model:value="compareBaseId"
              :options="baseOptions"
              size="small"
              style="min-width: 220px; max-width: 320px"
              @change="runCompare"
            />
            <span class="diff-picker-arrow">→</span>
            <a-select
              v-model:value="compareTargetId"
              :options="targetOptions"
              size="small"
              style="min-width: 220px; max-width: 320px"
              @change="runCompare"
            />
            <a-button size="small" :disabled="!canSwap" @click="swapDiff">
              <template #icon><ArrowLeftRight :size="14" /></template>
              {{ $t('docModal.swapAB') }}
            </a-button>
          </div>
        </div>

        <a-alert
          v-if="diffError"
          type="error"
          show-icon
          :message="diffError"
          class="diff-error"
        />
        <a-alert
          v-else-if="diffResult && diffResult.identical"
          type="success"
          show-icon
          :message="$t('docModal.diffIdentical')"
          class="diff-error"
        />
        <template v-else-if="diffResult">
          <div class="diff-summary">
            <div class="diff-version-chip">
              <strong>{{ versionLabel(diffResult.base) }}</strong>
              <span class="diff-version-name">{{ basename(diffResult.base.filename) }}</span>
              <a-tag :color="diffResult.base.is_current ? 'green' : 'default'">
                {{ $t(diffResult.base.is_current ? 'docModal.currentVersion' : 'docModal.historyVersion') }}
              </a-tag>
              <span class="diff-version-time">{{ formatTime(diffResult.base.activated_at) }}</span>
              <a-button
                v-if="baseListVersion"
                type="link"
                size="small"
                :title="$t('common.download')"
                class="diff-download"
                @click="emit('download', baseListVersion)"
              >
                <Download :size="14" />
              </a-button>
            </div>
            <div class="diff-summary-arrow">
              <span class="diff-old-label">{{ $t('docModal.diffOldLabel') }}</span>
              <ArrowRight :size="16" />
              <span class="diff-new-label">{{ $t('docModal.diffNewLabel') }}</span>
            </div>
            <div class="diff-version-chip">
              <strong>{{ versionLabel(diffResult.target) }}</strong>
              <span class="diff-version-name">{{ basename(diffResult.target.filename) }}</span>
              <a-tag :color="diffResult.target.is_current ? 'green' : 'default'">
                {{ $t(diffResult.target.is_current ? 'docModal.currentVersion' : 'docModal.historyVersion') }}
              </a-tag>
              <span class="diff-version-time">{{ formatTime(diffResult.target.activated_at) }}</span>
            </div>
            <div class="diff-stats">
              <span class="diff-stat stat-added">
                {{ $t('docModal.diffStatAdded', { count: diffResult.stats.added_lines }) }}
              </span>
              <span class="diff-stat stat-removed">
                {{ $t('docModal.diffStatRemoved', { count: diffResult.stats.removed_lines }) }}
              </span>
              <span class="diff-stat stat-unchanged">
                {{ $t('docModal.diffStatUnchanged', { count: diffResult.stats.unchanged_lines }) }}
              </span>
            </div>
          </div>
          <div class="diff-scroll">
            <div v-for="(hunk, hunkIndex) in diffResult.hunks" :key="hunkIndex" class="diff-hunk">
              <template v-for="(line, lineIndex) in hunk.lines" :key="`${hunkIndex}-${lineIndex}`">
                <div class="diff-line" :class="`is-${line.type}`">
                  <span class="gutter gutter-old">{{ line.old_no ?? '' }}</span>
                  <span class="gutter gutter-new">{{ line.new_no ?? '' }}</span>
                  <span class="diff-text">{{ line.text || ' ' }}</span>
                </div>
              </template>
            </div>
          </div>
        </template>
      </template>

      <!-- ============ 版本列表 ============ -->
      <template v-else>
        <a-alert
          v-if="compareBaseId && versionItems.length >= 2"
          type="info"
          show-icon
          class="compare-hint"
          :message="$t('docModal.comparePickHint', { name: compareBaseItem?.filename || '' })"
        >
          <template #action>
            <a-button size="small" @click="compareBaseId = ''">{{ $t('common.cancel') }}</a-button>
          </template>
        </a-alert>
        <a-empty
          v-if="!versionLoading && versionItems.length === 0"
          :description="$t('docModal.noVersionRecords')"
        />
        <a-list v-else :data-source="versionItems" item-layout="horizontal">
          <template #renderItem="{ item }">
            <a-list-item>
              <template #actions>
                <a-button
                  type="link"
                  :class="{ 'compare-active': item.file_id === compareBaseId }"
                  @click="toggleCompareVersion(item)"
                >
                  {{ compareActionLabel(item) }}
                </a-button>
                <a-button type="link" @click="emit('download', item)">{{ $t('common.download') }}</a-button>
                <a-button v-if="item.validation_report" type="link" @click="openReport(item)">
                  {{ $t('docModal.viewChangeReport') }}
                </a-button>
              </template>
              <a-list-item-meta :title="`V${item.document_version} · ${item.filename}`">
                <template #description>
                  <a-space wrap>
                    <a-tag :color="item.is_current ? 'green' : 'default'">
                      {{ $t(item.is_current ? 'docModal.currentVersion' : 'docModal.historyVersion') }}
                    </a-tag>
                    <span>{{ getFileStatusView(item.status).label }}</span>
                    <template v-if="item.validation_report">
                      <a-tag color="blue">{{ $t('docModal.reportNew', { count: item.validation_report.new_count }) }}</a-tag>
                      <a-tag color="orange">{{ $t('docModal.reportChanged', { count: item.validation_report.changed_count }) }}</a-tag>
                      <a-tag color="red">{{ $t('docModal.reportRemoved', { count: item.validation_report.removed_count }) }}</a-tag>
                      <a-tag color="red">{{ $t('docModal.reportConflict', { count: item.validation_report.conflict_count }) }}</a-tag>
                    </template>
                    <span
                      v-if="item.error_message"
                      class="version-status-message"
                      :title="item.error_message"
                    >
                      {{ item.error_message }}
                    </span>
                    <span>{{ formatTime(item.activated_at || item.created_at) }}</span>
                  </a-space>
                </template>
              </a-list-item-meta>
            </a-list-item>
          </template>
        </a-list>
      </template>
    </a-spin>
  </a-modal>

  <a-modal
    v-model:open="reportVisible"
    :title="$t('docModal.knowledgeChangeReport')"
    width="980px"
    :footer="null"
  >
    <a-spin :spinning="reportLoading">
      <template v-if="report">
        <a-alert
          v-if="report.inconclusive"
          type="warning"
          show-icon
          :message="report.summary?.message || $t('docModal.inconclusiveReport')"
        />
        <div class="report-summary">
          <a-tag color="blue">{{ $t('docModal.reportNew', { count: report.new_count }) }}</a-tag>
          <a-tag color="orange">{{ $t('docModal.reportChanged', { count: report.changed_count }) }}</a-tag>
          <a-tag color="red">{{ $t('docModal.reportRemoved', { count: report.removed_count }) }}</a-tag>
          <a-tag color="red">{{ $t('docModal.reportConflict', { count: report.conflict_count }) }}</a-tag>
        </div>
        <a-empty
          v-if="reportItems.length === 0"
          :description="$t('docModal.noStructuredChanges')"
        />
        <div v-for="item in reportItems" :key="item.item_id" class="report-item">
          <div class="report-item-title">
            <a-tag :color="getChangeTypeView(item.change_type).color">
              {{ getChangeTypeView(item.change_type).label }}
            </a-tag>
            <strong>{{ item.relation || item.fact_key }}</strong>
          </div>
          <p class="report-reason">{{ item.reason }}</p>
          <div class="report-value-compare">
            <span class="value-label">{{ $t('docModal.oldValue') }}</span>
            <code>{{ getSideValue(item, 'old') }}</code>
            <span class="value-arrow">→</span>
            <span class="value-label">{{ $t('docModal.newValue') }}</span>
            <code>{{ getSideValue(item, 'new') }}</code>
          </div>
          <div class="report-evidence-columns">
            <section>
              <span>{{ $t('docModal.oldEvidence') }}</span>
              <pre>{{ getEvidenceQuote(item.old_evidence, item.change_type, 'old') }}</pre>
            </section>
            <section>
              <span>{{ $t('docModal.newEvidence') }}</span>
              <pre>{{ getEvidenceQuote(item.new_evidence, item.change_type, 'new') }}</pre>
            </section>
          </div>
        </div>
        <div v-if="canReviewValidationReport(report, canManage)" class="report-actions">
          <a-button danger :loading="decisionLoading" @click="rejectReport">{{
            $t('docModal.rejectNewVersion')
          }}</a-button>
          <a-button type="primary" :loading="decisionLoading" @click="acceptReport"
            >{{ $t('docModal.acceptAndEnable') }}</a-button
          >
        </div>
      </template>
    </a-spin>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ArrowLeftRight, ArrowRight, CornerUpLeft, Download } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import { message, Modal } from 'ant-design-vue'

import { documentApi } from '@/apis/knowledge_api'
import { getFileStatusView } from '@/utils/knowledge_file_policy'
import {
  canReviewValidationReport,
  getChangeTypeView,
  getEvidenceQuote,
  getSideValue
} from './documentVersionReportHelpers'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: [String, Number], default: '' },
  fileId: { type: [String, Number], default: '' },
  canManage: { type: Boolean, default: false }
})
const emit = defineEmits(['update:open', 'download', 'changed'])

const { t } = useI18n()

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})
const versionLoading = ref(false)
const versionItems = ref([])
const reportVisible = ref(false)
const reportLoading = ref(false)
const report = ref(null)
const reportItems = ref([])
const reviewedVersion = ref(null)
const decisionLoading = ref(false)

// ---- 版本对比状态 ----
const compareMode = ref(false)
const compareBaseId = ref('')
const compareTargetId = ref('')
const diffLoading = ref(false)
const diffError = ref('')
const diffResult = ref(null)

const loadVersions = async () => {
  if (!props.kbId || !props.fileId) return
  versionLoading.value = true
  try {
    const response = await documentApi.getDocumentVersions(props.kbId, props.fileId)
    versionItems.value = response?.versions || []
  } catch (error) {
    message.error(error.message || t('docModal.loadVersionHistoryFailed'))
    versionItems.value = []
  } finally {
    versionLoading.value = false
  }
}

const openReport = async (version) => {
  reviewedVersion.value = version
  reportLoading.value = true
  reportVisible.value = true
  try {
    const response = await documentApi.getDocumentValidationReport(props.kbId, version.file_id)
    report.value = response?.report || null
    reportItems.value = response?.items || []
  } catch (error) {
    message.error(error.message || t('docModal.loadChangeReportFailed'))
    reportVisible.value = false
  } finally {
    reportLoading.value = false
  }
}

const acceptReport = async () => {
  if (!reviewedVersion.value?.supersedes_file_id) return
  decisionLoading.value = true
  try {
    const response = await documentApi.activateDocumentVersion(
      props.kbId,
      reviewedVersion.value.file_id,
      {
        expected_current_file_id: reviewedVersion.value.supersedes_file_id,
        accept_conflicts: true
      }
    )
    if (response?.cleanup_warnings?.length) {
      message.warning(
        t('docModal.newVersionActiveWarnings', {
          warnings: response.cleanup_warnings.join('；')
        })
      )
    } else {
      message.success(t('docModal.newVersionActive'))
    }
    reportVisible.value = false
    await loadVersions()
    emit('changed')
  } catch (error) {
    message.error(error.message || t('docModal.activateVersionFailed'))
  } finally {
    decisionLoading.value = false
  }
}

const rejectReport = () => {
  Modal.confirm({
    title: t('docModal.confirmRejectNewTitle'),
    content: t('docModal.confirmRejectNewContent'),
    okText: t('docModal.rejectNewVersion'),
    okButtonProps: { danger: true },
    async onOk() {
      decisionLoading.value = true
      try {
        await documentApi.rejectDocumentValidationReport(props.kbId, report.value.report_id)
        message.success(t('docModal.rejectedNewVersion'))
        reportVisible.value = false
        await loadVersions()
        emit('changed')
      } catch (error) {
        message.error(error.message || t('docModal.rejectNewVersionFailed'))
      } finally {
        decisionLoading.value = false
      }
    }
  })
}

// ---- 版本对比逻辑 ----
const versionById = (fileId) => versionItems.value.find((item) => item.file_id === fileId)
const compareBaseItem = computed(() => versionById(compareBaseId.value))
const baseListVersion = computed(() =>
  diffResult.value ? versionById(diffResult.value.base?.file_id) : undefined
)
const canSwap = computed(
  () => Boolean(compareBaseId.value) && Boolean(compareTargetId.value) && compareBaseId.value !== compareTargetId.value
)
const baseOptions = computed(() =>
  versionItems.value
    .filter((item) => item.file_id !== compareTargetId.value)
    .map((item) => ({ value: item.file_id, label: compareOptionLabel(item) }))
)
const targetOptions = computed(() =>
  versionItems.value
    .filter((item) => item.file_id !== compareBaseId.value)
    .map((item) => ({ value: item.file_id, label: compareOptionLabel(item) }))
)

const resetCompareState = () => {
  compareMode.value = false
  compareBaseId.value = ''
  compareTargetId.value = ''
  diffResult.value = null
  diffError.value = ''
}

const toggleCompareVersion = (item) => {
  if (item.file_id === compareBaseId.value) {
    compareBaseId.value = ''
    return
  }
  if (!compareBaseId.value) {
    compareBaseId.value = item.file_id
    return
  }
  const baseItem = compareBaseItem.value
  if (baseItem) enterCompare(baseItem, item)
}

// 与 toggleCompareVersion 三分支一一对应：未选基准＝选为基准；已是基准＝取消；
// 已选基准且是其它版本＝发起对比。避免每行同名「对比」造成第一步点选困惑。
const compareActionLabel = (item) => {
  if (item.file_id === compareBaseId.value) return t('docModal.compareCancelBase')
  if (compareBaseId.value) return t('docModal.compareToBase')
  return t('docModal.comparePickBase')
}

const enterCompare = (baseItem, targetItem) => {
  const [older, newer] = orderByVersion(baseItem, targetItem)
  compareBaseId.value = older.file_id
  compareTargetId.value = newer.file_id
  compareMode.value = true
  runCompare()
}

const orderByVersion = (a, b) => {
  const versionA = Number(a.document_version)
  const versionB = Number(b.document_version)
  if (!Number.isNaN(versionA) && !Number.isNaN(versionB) && versionA !== versionB) {
    return versionA < versionB ? [a, b] : [b, a]
  }
  return [a, b]
}

const exitCompare = () => {
  compareMode.value = false
  diffResult.value = null
  diffError.value = ''
}

const swapDiff = () => {
  if (!canSwap.value) return
  const previousBase = compareBaseId.value
  compareBaseId.value = compareTargetId.value
  compareTargetId.value = previousBase
  runCompare()
}

const runCompare = async () => {
  const versionAFileId = compareBaseId.value
  const versionBFileId = compareTargetId.value
  if (!versionAFileId || !versionBFileId || versionAFileId === versionBFileId) return
  diffLoading.value = true
  diffError.value = ''
  diffResult.value = null
  try {
    diffResult.value = await documentApi.getDocumentDiff(props.kbId, versionAFileId, versionBFileId)
  } catch (error) {
    diffError.value = error.message || t('docModal.loadVersionDiffFailed')
  } finally {
    diffLoading.value = false
  }
}

// ---- 展示辅助 ----
const versionLabel = (item) => `V${item.document_version ?? '?'}`
const compareOptionLabel = (item) =>
  `${versionLabel(item)} · ${basename(item.filename)}${item.is_current ? ' · ' + t('docModal.currentVersion') : ''}`
const basename = (filename) => {
  const name = String(filename || '')
  const index = name.lastIndexOf('/')
  return index >= 0 ? name.slice(index + 1) : name
}
const formatTime = (value) => (value ? new Date(value).toLocaleString() : '-')

watch(
  () => [props.open, props.kbId, props.fileId],
  ([open]) => {
    resetCompareState()
    if (open) loadVersions()
  }
)
</script>

<style scoped lang="less">
.version-status-message {
  max-width: 280px;
  overflow: hidden;
  color: var(--color-warning-700);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.report-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 16px 0;
}

.report-item {
  padding: 14px;
  margin-bottom: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.report-item-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.report-reason {
  margin: 8px 0;
  color: var(--color-text-secondary);
}

.report-value-compare {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 10px 12px;
  margin-bottom: 12px;
  border: 1px dashed var(--gray-200);
  border-radius: 6px;

  .value-label {
    flex: none;
    color: var(--color-text-secondary);
  }

  code {
    flex: 1 1 auto;
    min-width: 0;
    padding: 4px 8px;
    overflow-wrap: anywhere;
    background: var(--gray-50);
    border-radius: 4px;
  }

  .value-arrow {
    flex: none;
    color: var(--color-text-secondary);
  }
}

.report-evidence-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;

  section {
    min-width: 0;
  }

  span {
    color: var(--color-text-secondary);
  }

  pre {
    padding: 10px;
    margin: 6px 0 0;
    overflow: auto;
    white-space: pre-wrap;
    background: var(--gray-50);
    border-radius: 6px;
  }
}

.report-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 20px;
}

// ============ 版本对比 ============
.compare-hint {
  margin-bottom: 12px;
}

.diff-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.diff-pickers {
  display: flex;
  flex: 1 1 auto;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
  min-width: 0;

  .diff-picker-arrow {
    color: var(--color-text-secondary);
  }
}

.diff-error {
  margin-bottom: 12px;
}

.diff-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  padding: 12px;
  margin-bottom: 12px;
  background: var(--gray-50);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.diff-version-chip {
  display: flex;
  flex: 1 1 240px;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  min-width: 0;

  strong {
    font-size: 15px;
  }

  .diff-version-name {
    overflow: hidden;
    color: var(--color-text-secondary);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .diff-version-time {
    color: var(--color-text-secondary);
    font-size: 12px;
  }

  .diff-download {
    padding: 0;
    margin-left: 2px;
    color: var(--color-text-secondary);
  }
}

.diff-summary-arrow {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 2px;
  align-items: center;
  color: var(--gray-400);
  font-size: 12px;

  .diff-old-label {
    color: var(--color-error-500);
  }

  .diff-new-label {
    color: var(--color-success-500);
  }
}

.diff-stats {
  display: flex;
  flex: none;
  gap: 14px;
  padding-left: 4px;
}

.diff-stat {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.stat-added {
  color: var(--color-success-700);
}

.stat-removed {
  color: var(--color-error-700);
}

.stat-unchanged {
  color: var(--color-text-secondary);
  font-weight: 400;
}

.diff-scroll {
  max-height: calc(100vh - 380px);
  overflow: auto;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.diff-hunk + .diff-hunk {
  margin-top: 18px;
  border-top: 1px dashed var(--gray-150);
  padding-top: 6px;
}

.diff-line {
  display: grid;
  grid-template-columns: 48px 48px minmax(0, 1fr);
  align-items: start;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 20px;
  white-space: pre-wrap;
  word-break: break-word;

  &.is-del {
    background: var(--color-error-10);

    .gutter-old {
      color: var(--color-error-700);
    }
  }

  &.is-add {
    background: var(--color-success-10);

    .gutter-new {
      color: var(--color-success-700);
    }
  }

  .gutter {
    padding-right: 8px;
    overflow: hidden;
    color: var(--gray-400);
    text-align: right;
    user-select: none;
    font-variant-numeric: tabular-nums;
  }

  .diff-text {
    padding: 0 12px 0 4px;
    min-width: 0;
  }
}

@media (max-width: 768px) {
  .report-evidence-columns {
    grid-template-columns: 1fr;
  }

  .diff-pickers {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
