<script setup>
import { ref } from 'vue'
import { queryApi } from '@/apis/knowledge_api'

const open = defineModel('open', { type: Boolean, default: false })
const query = ref('')
const results = ref([])
const loading = ref(false)

const search = async () => {
  if (!query.value.trim()) return
  loading.value = true
  try {
    const response = await queryApi.globalSearch(query.value)
    results.value = response.result || []
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <a-modal v-model:open="open" title="全知识库搜索" :footer="null" @ok="search">
    <a-input-search v-model:value="query" placeholder="输入问题或关键词" :loading="loading" enter-button="搜索" @search="search" />
    <a-list v-if="results.length" class="results" :data-source="results" item-layout="vertical">
      <template #renderItem="{ item }">
        <a-list-item>
          <a-list-item-meta :title="item.kb_name" :description="item.file_name || item.filename || '知识库片段'" />
          <div class="content">{{ item.content || item.text }}</div>
        </a-list-item>
      </template>
    </a-list>
    <a-empty v-else-if="!loading && query" class="results" description="暂无相关内容" />
  </a-modal>
</template>

<style scoped lang="less">
.results { margin-top: 16px; max-height: 420px; overflow-y: auto; }
.content { white-space: pre-wrap; color: var(--text-color-secondary); }
</style>
