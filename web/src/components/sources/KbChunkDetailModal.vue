<template>
  <a-modal
    v-model:open="visible"
    :title="modalTitle"
    width="960px"
    :footer="null"
    :destroyOnClose="true"
    wrap-class-name="chunk-detail-modal"
    :bodyStyle="{ maxHeight: '72vh', overflowY: 'auto', padding: '0' }"
  >
    <div v-if="chunk" class="detail-meta">
      <span v-if="typeof chunk.score === 'number'" class="score"
        >{{ $t('sources.similarityScore', { score: (chunk.score * 100).toFixed(1) }) }}</span
      >
      <span v-if="chunk.metadata?.chunk_id" class="meta-item"
        >chunk_id: {{ chunk.metadata.chunk_id }}</span
      >
      <span v-if="lineRange" class="meta-item">{{ lineRange }}</span>
    </div>

    <MarkdownPreview
      v-if="chunk?.content"
      :content="chunk.content"
      class="chunk-markdown-content"
    />
    <div v-else class="empty-text">{{ $t('sources.emptyContent') }}</div>
  </a-modal>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'

const props = defineProps({
  open: {
    type: Boolean,
    default: false
  },
  chunk: {
    type: Object,
    default: null
  },
  titlePrefix: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['update:open'])
const { t } = useI18n()

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})

const modalTitle = computed(() => {
  const prefix = props.titlePrefix || t('sources.chunkDetailTitle')
  const source = props.chunk?.metadata?.source
  return source ? `${prefix} - ${source}` : prefix
})

const lineRange = computed(() => {
  const startLine = Number(props.chunk?.metadata?.start_line || 0)
  const endLine = Number(props.chunk?.metadata?.end_line || 0)
  if (!startLine || !endLine) return ''
  return startLine === endLine
    ? t('sources.lineRange', { line: startLine })
    : t('sources.lineRangeRange', { start: startLine, end: endLine })
})
</script>

<style scoped lang="less">
.detail-meta {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--gray-600);
  display: flex;
  flex-wrap: wrap;
  gap: 10px;

  .score {
    color: var(--gray-700);
    font-weight: 600;
  }

  .meta-item {
    color: var(--gray-600);
  }
}

.empty-text {
  color: var(--gray-500);
  font-size: 13px;
}
</style>
