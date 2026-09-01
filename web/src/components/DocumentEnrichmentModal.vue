<template>
  <a-modal
    v-model:open="visible"
    :title="$t('docModal.enrichmentTitle')"
    width="820px"
    :footer="null"
    :destroy-on-close="true"
  >
    <div v-if="loading" class="loading-state">
      <a-spin :tip="$t('docModal.loadingEnrichment')" />
    </div>
    <a-alert
      v-else-if="errorMessage"
      type="error"
      show-icon
      :message="errorMessage"
      class="enrichment-alert"
    />
    <div v-else-if="payload" class="enrichment-shell">
      <div class="enrichment-meta">
        <a-tag :color="statusColor">{{ statusLabel }}</a-tag>
        <span>{{ $t('docModal.version', { version: payload.version }) }}</span>
        <span v-if="payload.readonly">{{ $t('docModal.readonly') }}</span>
      </div>
      <a-alert
        v-if="payload.possibly_outdated"
        type="warning"
        show-icon
        :message="$t('docModal.bodyChangedReviewManual')"
        class="enrichment-alert"
      />
      <a-alert
        v-if="payload.error"
        type="warning"
        show-icon
        :message="payload.error"
        class="enrichment-alert"
      />
      <section class="enrichment-section">
        <header>
          <div>
            <strong>{{ $t('docModal.summary') }}</strong>
            <a-tag v-if="payload.summary?.source" class="source-tag">
              {{ sourceLabel(payload.summary.source) }}
            </a-tag>
          </div>
          <div v-if="!payload.readonly" class="section-actions">
            <a-button
              v-if="payload.summary?.source !== 'manual'"
              size="small"
              :loading="actionLoading"
              @click="generate(['summary'])"
            >
              {{ $t('upload.regenerate') }}
            </a-button>
            <a-button
              v-if="payload.summary?.source === 'manual'"
              size="small"
              :loading="actionLoading"
              @click="replaceManual(['summary'])"
            >
              {{ $t('docModal.replaceWithGenerated') }}
            </a-button>
          </div>
        </header>
        <a-textarea
          v-model:value="summaryText"
          :readonly="payload.readonly"
          :maxlength="1000"
          :auto-size="{ minRows: 4, maxRows: 10 }"
          :placeholder="$t('docModal.noSummaryPlaceholder')"
        />
      </section>
      <section class="enrichment-section">
        <header>
          <div>
            <strong>{{ $t('docModal.keywords') }}</strong>
            <a-tag v-if="keywordSource" class="source-tag">{{ sourceLabel(keywordSource) }}</a-tag>
          </div>
          <div v-if="!payload.readonly" class="section-actions">
            <a-button
              v-if="keywordSource !== 'manual'"
              size="small"
              :loading="actionLoading"
              @click="generate(['keywords'])"
            >
              {{ $t('upload.regenerate') }}
            </a-button>
            <a-button
              v-if="keywordSource === 'manual'"
              size="small"
              :loading="actionLoading"
              @click="replaceManual(['keywords'])"
            >
              {{ $t('docModal.replaceWithGenerated') }}
            </a-button>
          </div>
        </header>
        <a-select
          v-model:value="keywordValues"
          mode="tags"
          :disabled="payload.readonly"
          :open="false"
          :max-tag-count="12"
          :placeholder="$t('docModal.keywordPlaceholder')"
        />
      </section>
      <section class="enrichment-section">
        <header>
          <div>
            <strong>{{ $t('docModal.tags') }}</strong>
            <a-tag v-if="tagSource" class="source-tag">{{ sourceLabel(tagSource) }}</a-tag>
          </div>
          <div v-if="!payload.readonly" class="section-actions">
            <a-button
              v-if="tagSource !== 'manual'"
              size="small"
              :loading="actionLoading"
              @click="generate(['tags'])"
            >
              {{ $t('upload.regenerate') }}
            </a-button>
            <a-button
              v-if="tagSource === 'manual'"
              size="small"
              :loading="actionLoading"
              @click="replaceManual(['tags'])"
            >
              {{ $t('docModal.replaceWithGenerated') }}
            </a-button>
          </div>
        </header>
        <a-select
          v-model:value="tagValues"
          mode="tags"
          :disabled="payload.readonly"
          :open="false"
          :max-tag-count="8"
          :placeholder="$t('docModal.tagPlaceholder')"
        />
      </section>
      <div class="modal-actions">
        <a-button @click="visible = false">{{ $t('common.close') }}</a-button>
        <template v-if="!payload.readonly">
          <a-button :loading="actionLoading" @click="generateAll">{{ $t('docModal.generateAll') }}</a-button>
          <a-button type="primary" :loading="actionLoading" @click="saveAll">{{ $t('docModal.saveManualContent') }}</a-button>
        </template>
      </div>
    </div>
  </a-modal>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { message, Modal } from 'ant-design-vue'
import { documentApi } from '@/apis/knowledge_api'
const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: [String, Number], default: '' },
  fileId: { type: [String, Number], default: '' }
})
const emit = defineEmits(['update:open', 'changed'])
const { t } = useI18n()
const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})
const loading = ref(false)
const actionLoading = ref(false)
const errorMessage = ref('')
const payload = ref(null)
const summaryText = ref('')
const keywordValues = ref([])
const tagValues = ref([])
const statusLabel = computed(() => {
  const labels = {
    not_generated: 'docModal.statusNotGenerated',
    generating: 'docModal.statusGenerating',
    ready: 'docModal.statusReady',
    skipped: 'docModal.statusSkipped',
    failed: 'docModal.statusGenerationFailed',
    possibly_outdated: 'docModal.statusPossiblyOutdated'
  }
  const key = labels[payload.value?.status]
  return key ? t(key) : payload.value?.status || ''
})
const statusColor = computed(() => {
  if (payload.value?.status === 'failed') return 'red'
  if (payload.value?.status === 'possibly_outdated') return 'orange'
  if (payload.value?.status === 'ready') return 'green'
  return 'blue'
})
const keywordSource = computed(
  () => payload.value?.keyword_source || payload.value?.keywords?.[0]?.source || ''
)
const tagSource = computed(
  () => payload.value?.tag_source || payload.value?.tags?.[0]?.source || ''
)
const sourceLabel = (source) =>
  source === 'manual' ? t('docModal.manualSource') : t('docModal.autoSource')
const applyPayload = (nextPayload) => {
  payload.value = nextPayload
  summaryText.value = nextPayload?.summary?.text || ''
  keywordValues.value = (nextPayload?.keywords || []).map((item) => item.value)
  tagValues.value = (nextPayload?.tags || []).map((item) => item.name)
  errorMessage.value = ''
}
const load = async () => {
  if (!props.kbId || !props.fileId) return
  loading.value = true
  errorMessage.value = ''
  try {
    applyPayload(await documentApi.getEnrichment(props.kbId, props.fileId))
  } catch (error) {
    errorMessage.value = error.message || t('docModal.loadEnrichmentFailed')
  } finally {
    loading.value = false
  }
}
const handleOpenChange = (open) => {
  if (open) load()
}
watch(() => props.open, handleOpenChange)
const waitForGeneration = async (previousVersion) => {
  for (let attempt = 0; attempt < 30 && visible.value; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1000))
    const nextPayload = await documentApi.getEnrichment(props.kbId, props.fileId)
    if (
      nextPayload.version > previousVersion &&
      !['generating', 'not_generated'].includes(nextPayload.status)
    ) {
      applyPayload(nextPayload)
      return
    }
  }
  await load()
}
const generate = async (components, overwriteManual = false) => {
  actionLoading.value = true
  const previousVersion = payload.value.version
  try {
    await documentApi.generateEnrichment(props.kbId, props.fileId, components, overwriteManual)
    message.success(t('docModal.generationSubmitted'))
    await waitForGeneration(previousVersion)
    emit('changed', payload.value)
  } catch (error) {
    message.error(error.message || t('docModal.generationFailedRetry'))
  } finally {
    actionLoading.value = false
  }
}
const generateAll = () => {
  if (
    payload.value.summary?.source === 'manual' &&
    keywordSource.value === 'manual' &&
    tagSource.value === 'manual'
  ) {
    message.info(t('docModal.allManualInfo'))
    return
  }
  generate(['summary', 'keywords', 'tags'])
}
const replaceManual = (components) => {
  Modal.confirm({
    title: t('docModal.replaceManualTitle'),
    content: t('docModal.replaceManualContent'),
    okText: t('docModal.confirmReplace'),
    cancelText: t('common.cancel'),
    onOk: () => generate(components, true)
  })
}
const saveAll = async () => {
  actionLoading.value = true
  try {
    let version = payload.value.version
    let nextPayload = payload.value
    const savedKeywords = (payload.value.keywords || []).map((item) => item.value)
    const savedTags = (payload.value.tags || []).map((item) => item.name)
    if (summaryText.value !== (payload.value.summary?.text || '')) {
      nextPayload = await documentApi.updateSummary(
        props.kbId,
        props.fileId,
        summaryText.value,
        version
      )
      version = nextPayload.version
    }
    if (JSON.stringify(keywordValues.value) !== JSON.stringify(savedKeywords)) {
      nextPayload = await documentApi.updateKeywords(
        props.kbId,
        props.fileId,
        keywordValues.value,
        version
      )
      version = nextPayload.version
    }
    if (JSON.stringify(tagValues.value) !== JSON.stringify(savedTags)) {
      nextPayload = await documentApi.updateTags(props.kbId, props.fileId, tagValues.value, version)
    }
    if (nextPayload === payload.value) {
      message.info(t('docModal.noChangesToSave'))
      return
    }
    applyPayload(nextPayload)
    message.success(t('docModal.documentInfoSaved'))
    emit('changed', nextPayload)
  } catch (error) {
    message.error(error.message || t('docModal.saveFailedRetry'))
  } finally {
    actionLoading.value = false
  }
}
</script>
<style scoped lang="less">
.loading-state {
  display: grid;
  min-height: 360px;
  place-items: center;
}
.enrichment-shell {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.enrichment-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.enrichment-alert {
  margin-bottom: 4px;
}
.enrichment-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}
.enrichment-section header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.source-tag {
  margin-left: 8px;
}
.section-actions,
.modal-actions {
  display: flex;
  gap: 8px;
}
.modal-actions {
  justify-content: flex-end;
}
</style>
