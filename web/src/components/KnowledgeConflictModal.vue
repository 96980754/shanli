<template>
  <a-modal
    v-model:open="visible"
    title="知识冲突审核"
    width="1080px"
    :footer="null"
    @open-change="handleOpenChange"
  >
    <div class="conflict-toolbar">
      <a-select v-model:value="statusFilter" style="width: 160px" @change="load">
        <a-select-option value="">全部状态</a-select-option>
        <a-select-option value="pending">待处理</a-select-option>
        <a-select-option value="resolved">已处理</a-select-option>
        <a-select-option value="deferred">已暂缓</a-select-option>
      </a-select>
      <a-button :loading="loading" @click="load">刷新</a-button>
      <span v-if="payload?.readonly" class="readonly-hint">当前权限为只读</span>
    </div>

    <a-alert v-if="errorMessage" type="error" show-icon :message="errorMessage" />
    <a-spin :spinning="loading">
      <a-empty v-if="!loading && conflicts.length === 0" description="暂无知识冲突记录" />
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
            <h4>当前知识</h4>
            <div v-if="item.existing_assertions?.length">
              <div v-for="existing in item.existing_assertions" :key="existing.assertion_id">
                <p class="value">
                  {{ formatKnowledgeValue(existing.raw_value, existing.unit) }}
                </p>
                <small>文档版本：{{ existing.cleaning_version }} · {{ existing.file_id }}</small>
                <blockquote>{{ existing.evidence }}</blockquote>
              </div>
            </div>
            <p v-else class="empty-value">当前没有正式值</p>
          </article>
          <article>
            <h4>新候选知识</h4>
            <p class="value">
              {{
                formatKnowledgeValue(
                  item.incoming_assertion?.raw_value,
                  item.incoming_assertion?.unit
                )
              }}
            </p>
            <small>
              文档版本：{{ item.incoming_assertion?.cleaning_version }} ·
              {{ item.incoming_assertion?.file_id }} ·
              {{ item.incoming_assertion?.extraction_method }}
            </small>
            <blockquote>{{ item.incoming_assertion?.evidence }}</blockquote>
          </article>
          <article>
            <h4>系统判断</h4>
            <p>标准化旧值：{{ formatKnowledgeValue(item.normalized_existing_value) }}</p>
            <p>标准化新值：{{ formatKnowledgeValue(item.normalized_incoming_value) }}</p>
            <ul>
              <li v-for="reason in item.detection_rules?.reasons || []" :key="reason">
                {{ reason }}
              </li>
            </ul>
            <small>实体链接：{{ item.detection_rules?.entity_link_status || '-' }}</small>
          </article>
        </div>

        <div v-if="item.status === 'pending' && !payload?.readonly" class="review-actions">
          <a-select
            v-model:value="drafts[item.conflict_id].resolution"
            placeholder="请选择处理结果"
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
            placeholder="已有实体 ID"
            style="width: 220px"
          />
          <a-input
            v-model:value="drafts[item.conflict_id].reason"
            placeholder="处理理由（可选）"
            style="min-width: 280px; flex: 1"
          />
          <a-button
            type="primary"
            :disabled="!drafts[item.conflict_id].resolution"
            :loading="resolvingId === item.conflict_id"
            @click="resolve(item)"
          >
            确认处理
          </a-button>
        </div>
        <a-alert
          v-if="item.publish_status === 'pending'"
          type="info"
          show-icon
          message="审核结果已保存；正式图谱投影适配器尚未接入，当前等待同步。"
        />
      </section>
    </a-spin>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { knowledgeConflictApi } from '@/apis/knowledge_api'
import {
  KNOWLEDGE_CONFLICT_RESOLUTIONS,
  formatKnowledgeValue,
  knowledgeConflictClassificationColor,
  knowledgeConflictClassificationLabel,
  knowledgeConflictStatusLabel
} from '@/utils/knowledge_conflict_policy'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: [String, Number], default: '' }
})
const emit = defineEmits(['update:open', 'changed'])
const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})

const payload = ref(null)
const loading = ref(false)
const resolvingId = ref('')
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
    errorMessage.value = error.message || '加载知识冲突失败'
  } finally {
    loading.value = false
  }
}

const handleOpenChange = (open) => {
  if (open) load()
}

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
    message.success('处理结果已保存')
    emit('changed')
    await load()
  } catch (error) {
    message.error(error.message || '处理失败，请刷新后重试')
  } finally {
    resolvingId.value = ''
  }
}

const classificationLabel = knowledgeConflictClassificationLabel
const classificationColor = knowledgeConflictClassificationColor
const statusLabel = knowledgeConflictStatusLabel
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
