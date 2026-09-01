<template>
  <a-modal v-model:open="visible" :title="$t('conflict.auditTitle')" width="1080px" :footer="null">
    <div class="conflict-toolbar">
      <a-select v-model:value="statusFilter" style="width: 160px" @change="load">
        <a-select-option value="">{{ $t('conflict.statusAll') }}</a-select-option>
        <a-select-option value="pending">{{ $t('conflict.statusPending') }}</a-select-option>
        <a-select-option value="resolved">{{ $t('conflict.statusResolved') }}</a-select-option>
        <a-select-option value="deferred">{{ $t('conflict.statusDeferred') }}</a-select-option>
      </a-select>
      <a-button :loading="loading" @click="load">{{ $t('common.refresh') }}</a-button>
      <span v-if="payload?.readonly" class="readonly-hint">{{ $t('conflict.readonlyHint') }}</span>
    </div>
    <a-alert v-if="errorMessage" type="error" show-icon :message="errorMessage" />
    <a-spin :spinning="loading">
      <a-empty v-if="!loading && conflicts.length === 0" :description="$t('conflict.empty')" />
      <section v-for="item in conflicts" :key="item.conflict_id" class="conflict-card">
        <header>
          <div>
            <strong>{{ item.entity_name }}</strong>
            <span class="predicate">{{ item.predicate }}</span>
          </div>
          <div>
            <a-tag :color="classificationColor(item.classification)">
              {{ classificationLabel(item.classification) }}
            </a-tag>
            <a-tag>{{ statusLabel(item.status) }}</a-tag>
          </div>
        </header>
        <div class="comparison-grid">
          <article>
            <h4>{{ $t('conflict.existingKnowledge') }}</h4>
            <div v-if="item.existing_assertions?.length">
              <div v-for="existing in item.existing_assertions" :key="existing.assertion_id">
                <p class="value">
                  {{ formatKnowledgeValue(existing.raw_value, existing.unit) }}
                </p>
                <small>{{ $t('conflict.docVersion', { version: existing.cleaning_version, fileId: existing.file_id }) }}</small>
                <blockquote>{{ existing.evidence }}</blockquote>
              </div>
            </div>
            <p v-else class="empty-value">{{ $t('conflict.noExistingValue') }}</p>
          </article>
          <article>
            <h4>{{ $t('conflict.incomingKnowledge') }}</h4>
            <p class="value">
              {{
                formatKnowledgeValue(
                  item.incoming_assertion?.raw_value,
                  item.incoming_assertion?.unit
                )
              }}
            </p>
            <small>
              {{ $t('conflict.docVersionMethod', { version: item.incoming_assertion?.cleaning_version, fileId: item.incoming_assertion?.file_id, method: item.incoming_assertion?.extraction_method }) }}
            </small>
            <blockquote>{{ item.incoming_assertion?.evidence }}</blockquote>
          </article>
          <article>
            <h4>{{ $t('conflict.systemJudgment') }}</h4>
            <p>{{ $t('conflict.normalizedOldValue', { value: formatKnowledgeValue(item.normalized_existing_value) }) }}</p>
            <p>{{ $t('conflict.normalizedNewValue', { value: formatKnowledgeValue(item.normalized_incoming_value) }) }}</p>
            <ul>
              <li v-for="reason in item.detection_rules?.reasons || []" :key="reason">
                {{ reason }}
              </li>
            </ul>
            <small>{{ $t('conflict.entityLink', { status: item.detection_rules?.entity_link_status || '-' }) }}</small>
          </article>
        </div>
        <div v-if="item.status === 'pending' && !payload?.readonly" class="review-actions">
          <a-select
            v-model:value="drafts[item.conflict_id].resolution"
            :placeholder="$t('conflict.resolutionPlaceholder')"
            style="width: 220px"
          >
            <a-select-option
              v-for="option in resolutionOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </a-select-option>
          </a-select>
          <a-input
            v-if="drafts[item.conflict_id].resolution === 'link_existing_entity'"
            v-model:value="drafts[item.conflict_id].target_entity_id"
            :placeholder="$t('conflict.targetEntityPlaceholder')"
            style="width: 220px"
          />
          <a-input
            v-model:value="drafts[item.conflict_id].reason"
            :placeholder="$t('conflict.reasonPlaceholder')"
            style="min-width: 280px; flex: 1"
          />
          <a-button
            type="primary"
            :disabled="!drafts[item.conflict_id].resolution"
            :loading="resolvingId === item.conflict_id"
            @click="resolve(item)"
          >
            {{ $t('conflict.confirmResolution') }}
          </a-button>
        </div>
        <a-alert
          v-if="item.publish_status !== 'not_requested'"
          :type="publishAlertType(item.publish_status)"
          show-icon
          :message="$t('conflict.publishStatus', { status: publishStatusLabel(item.publish_status) })"
          :description="item.publish_error || undefined"
        >
          <template v-if="canRetry(item, payload?.readonly)" #action>
            <a-button
              size="small"
              :loading="retryingId === item.conflict_id"
              :disabled="retryingId === item.conflict_id"
              @click="retryPublish(item)"
            >
              {{ $t('conflict.retryPublish') }}
            </a-button>
          </template>
        </a-alert>
      </section>
    </a-spin>
  </a-modal>
</template>
<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { message } from 'ant-design-vue'
import { knowledgeConflictApi } from '@/apis/knowledge_api'
import {
  KNOWLEDGE_CONFLICT_RESOLUTIONS,
  formatKnowledgeValue,
  canRetryKnowledgePublish,
  knowledgeConflictClassificationColor,
  knowledgeConflictClassificationLabel,
  knowledgeConflictStatusLabel,
  knowledgePublishStatusLabel
} from '@/utils/knowledge_conflict_policy'
const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: [String, Number], default: '' }
})
const emit = defineEmits(['update:open', 'changed'])
const { t } = useI18n()
const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})
const payload = ref(null)
const loading = ref(false)
const resolvingId = ref('')
const retryingId = ref('')
const errorMessage = ref('')
const statusFilter = ref('pending')
const drafts = reactive({})
const conflicts = computed(() => payload.value?.items || [])
const resolutionOptions = KNOWLEDGE_CONFLICT_RESOLUTIONS
const load = async () => {
  if (!props.kbId) return
  loading.value = true
  errorMessage.value = ''
  try {
    payload.value = await knowledgeConflictApi.list(props.kbId, statusFilter.value)
    for (const item of payload.value.items || []) {
      drafts[item.conflict_id] ||= { resolution: undefined, reason: '', target_entity_id: '' }
    }
  } catch (error) {
    errorMessage.value = error.message || t('conflict.loadFailed')
  } finally {
    loading.value = false
  }
}
const handleOpenChange = (open) => {
  if (open) load()
}
// Ant Design Vue 4.2 does not emit afterOpenChange from Modal.
watch(() => props.open, handleOpenChange)
const resolve = async (item) => {
  const draft = drafts[item.conflict_id]
  resolvingId.value = item.conflict_id
  try {
    await knowledgeConflictApi.resolve(props.kbId, item.conflict_id, {
      resolution: draft.resolution,
      version: item.version,
      reason: draft.reason || null,
      target_entity_id: draft.target_entity_id || null
    })
    message.success(t('conflict.resolutionSaved'))
    emit('changed')
    await load()
  } catch (error) {
    message.error(error.message || t('conflict.resolveFailed'))
  } finally {
    resolvingId.value = ''
  }
}
const retryPublish = async (item) => {
  retryingId.value = item.conflict_id
  try {
    await knowledgeConflictApi.retryPublish(props.kbId, item.conflict_id)
    message.success(t('conflict.retrySubmitted'))
    await load()
  } catch (error) {
    message.error(error.message || t('conflict.retryFailed'))
  } finally {
    retryingId.value = ''
  }
}
const classificationLabel = knowledgeConflictClassificationLabel
const classificationColor = knowledgeConflictClassificationColor
const statusLabel = knowledgeConflictStatusLabel
const publishStatusLabel = knowledgePublishStatusLabel
const canRetry = canRetryKnowledgePublish
const publishAlertType = (status) =>
  ({ succeeded: 'success', failed: 'error', dead_letter: 'error' })[status] || 'info'
</script>
<style scoped lang="less">
.conflict-toolbar,
.review-actions,
.conflict-card header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.conflict-toolbar {
  margin-bottom: 16px;
}
.readonly-hint,
.predicate,
small {
  color: var(--gray-7);
}
.predicate {
  margin-left: 12px;
}
.conflict-card {
  padding: 16px;
  margin-bottom: 16px;
  border: 1px solid var(--gray-4);
  border-radius: 8px;
}
.conflict-card header {
  justify-content: space-between;
  margin-bottom: 12px;
}
.comparison-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.comparison-grid article {
  padding: 12px;
  background: var(--gray-1);
  border-radius: 6px;
}
.value {
  font-weight: 600;
  word-break: break-word;
}
.empty-value {
  color: var(--gray-7);
}
blockquote {
  padding-left: 10px;
  margin: 8px 0 0;
  color: var(--gray-8);
  border-left: 3px solid var(--gray-4);
}
.review-actions {
  margin-top: 14px;
}
@media (max-width: 900px) {
  .comparison-grid {
    grid-template-columns: 1fr;
  }
}
</style>
