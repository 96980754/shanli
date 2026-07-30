<template>
  <a-modal v-model:open="visible" title="版本历史" width="840px" :footer="null">
    <a-spin :spinning="versionLoading">
      <a-empty v-if="!versionLoading && versionItems.length === 0" description="暂无版本记录" />
      <a-list v-else :data-source="versionItems" item-layout="horizontal">
        <template #renderItem="{ item }">
          <a-list-item>
            <template #actions>
              <a-button type="link" @click="emit('download', item)">下载</a-button>
              <a-button v-if="item.validation_report" type="link" @click="openReport(item)">
                查看变更报告
              </a-button>
            </template>
            <a-list-item-meta :title="`V${item.document_version} · ${item.filename}`">
              <template #description>
                <a-space wrap>
                  <a-tag :color="item.is_current ? 'green' : 'default'">
                    {{ item.is_current ? '当前版本' : '历史/候选版本' }}
                  </a-tag>
                  <span>{{ getFileStatusView(item.status).label }}</span>
                  <template v-if="item.validation_report">
                    <a-tag color="blue">新增 {{ item.validation_report.new_count }}</a-tag>
                    <a-tag color="orange">变更 {{ item.validation_report.changed_count }}</a-tag>
                    <a-tag color="red">删除 {{ item.validation_report.removed_count }}</a-tag>
                    <a-tag color="red">冲突 {{ item.validation_report.conflict_count }}</a-tag>
                  </template>
                  <span v-if="item.error_message" class="version-status-message" :title="item.error_message">
                    {{ item.error_message }}
                  </span>
                  <span>{{ formatTime(item.activated_at || item.created_at) }}</span>
                </a-space>
              </template>
            </a-list-item-meta>
          </a-list-item>
        </template>
      </a-list>
    </a-spin>
  </a-modal>

  <a-modal v-model:open="reportVisible" title="知识变更报告" width="980px" :footer="null">
    <a-spin :spinning="reportLoading">
      <template v-if="report">
        <a-alert
          v-if="report.inconclusive"
          type="warning"
          show-icon
          :message="report.summary?.message || '知识变更证据不足，需要人工审核'"
        />
        <div class="report-summary">
          <a-tag color="blue">新增 {{ report.new_count }}</a-tag>
          <a-tag color="orange">变更 {{ report.changed_count }}</a-tag>
          <a-tag color="red">删除 {{ report.removed_count }}</a-tag>
          <a-tag color="red">冲突 {{ report.conflict_count }}</a-tag>
        </div>
        <a-empty v-if="reportItems.length === 0" description="没有结构化知识变化" />
        <div v-for="item in reportItems" :key="item.item_id" class="report-item">
          <div class="report-item-title">
            <a-tag :color="getChangeTypeView(item.change_type).color">
              {{ getChangeTypeView(item.change_type).label }}
            </a-tag>
            <strong>{{ item.relation || item.fact_key }}</strong>
          </div>
          <p class="report-reason">{{ item.reason }}</p>
          <div class="report-evidence-columns">
            <section>
              <span>旧版证据</span>
              <pre>{{ getEvidenceQuote(item.old_evidence) }}</pre>
            </section>
            <section>
              <span>新版证据</span>
              <pre>{{ getEvidenceQuote(item.new_evidence) }}</pre>
            </section>
          </div>
        </div>
        <div v-if="canReviewValidationReport(report, canManage)" class="report-actions">
          <a-button danger :loading="decisionLoading" @click="rejectReport">拒绝新版</a-button>
          <a-button type="primary" :loading="decisionLoading" @click="acceptReport">接受并启用新版</a-button>
        </div>
      </template>
    </a-spin>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'

import { documentApi } from '@/apis/knowledge_api'
import { getFileStatusView } from '@/utils/knowledge_file_policy'
import {
  canReviewValidationReport,
  getChangeTypeView,
  getEvidenceQuote
} from './documentVersionReportHelpers'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: [String, Number], default: '' },
  fileId: { type: [String, Number], default: '' },
  canManage: { type: Boolean, default: false }
})
const emit = defineEmits(['update:open', 'download', 'changed'])

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

const loadVersions = async () => {
  if (!props.kbId || !props.fileId) return
  versionLoading.value = true
  try {
    const response = await documentApi.getDocumentVersions(props.kbId, props.fileId)
    versionItems.value = response?.versions || []
  } catch (error) {
    message.error(error.message || '加载版本历史失败')
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
    message.error(error.message || '加载知识变更报告失败')
    reportVisible.value = false
  } finally {
    reportLoading.value = false
  }
}

const acceptReport = async () => {
  if (!reviewedVersion.value?.supersedes_file_id) return
  decisionLoading.value = true
  try {
    const response = await documentApi.activateDocumentVersion(props.kbId, reviewedVersion.value.file_id, {
      expected_current_file_id: reviewedVersion.value.supersedes_file_id,
      accept_conflicts: true
    })
    if (response?.cleanup_warnings?.length) {
      message.warning(`新版已生效，但旧版索引清理存在告警：${response.cleanup_warnings.join('；')}`)
    } else {
      message.success('新版已生效')
    }
    reportVisible.value = false
    await loadVersions()
    emit('changed')
  } catch (error) {
    message.error(error.message || '启用新版失败')
  } finally {
    decisionLoading.value = false
  }
}

const rejectReport = () => {
  Modal.confirm({
    title: '确认拒绝新版？',
    content: '拒绝后旧版继续生效，知识变更报告仍会保留。',
    okText: '拒绝新版',
    okButtonProps: { danger: true },
    async onOk() {
      decisionLoading.value = true
      try {
        await documentApi.rejectDocumentValidationReport(props.kbId, report.value.report_id)
        message.success('已拒绝新版，旧版继续生效')
        reportVisible.value = false
        await loadVersions()
        emit('changed')
      } catch (error) {
        message.error(error.message || '拒绝新版失败')
      } finally {
        decisionLoading.value = false
      }
    }
  })
}

const formatTime = (value) => (value ? new Date(value).toLocaleString() : '-')

watch(
  () => [props.open, props.kbId, props.fileId],
  ([open]) => {
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

@media (max-width: 768px) {
  .report-evidence-columns {
    grid-template-columns: 1fr;
  }
}
</style>
