<template>
  <div class="source-section">
    <div class="section-title">{{ $t('sources.kbTitle') }} ({{ docCount }})</div>
    <KbResultGroupedList :chunks="chunks" :show-summary="false" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { MessageProcessor } from '@/utils/messageProcessor'
import KbResultGroupedList from '@/components/sources/KbResultGroupedList.vue'

const props = defineProps({
  chunks: {
    type: Array,
    default: () => []
  }
})

// 标题数字与面板卡片数一致：按文档名去重（跨库同名文档只算 1）
const docCount = computed(
  () => MessageProcessor.groupKnowledgeChunksByDocument(props.chunks).length
)
</script>

<style scoped lang="less">
.source-section {
  .section-title {
    font-size: 12px;
    color: var(--gray-700);
    margin-bottom: 8px;
    font-weight: 600;
  }
}
</style>
