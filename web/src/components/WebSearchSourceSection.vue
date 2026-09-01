<template>
  <div class="source-section">
    <div class="section-title">{{ $t('sources.webSearchTitle') }} ({{ sources.length }})</div>
    <WebSearchResultList :results="sources" :empty-text="t('sources.webSearchEmpty')" />
  </div>
</template>

<script setup>
import WebSearchResultList from '@/components/sources/WebSearchResultList.vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps({
  sources: {
    type: Array,
    default: () => []
  }
})
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
