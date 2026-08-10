<script setup>
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { queryApi } from '@/apis/knowledge_api'

const open = defineModel('open', { type: Boolean, default: false })
const query = ref('')
const results = ref([])
const loading = ref(false)
const handoffAvailable = ref(false)
const searchComplete = ref(true)

const search = async () => {
  if (!query.value.trim()) return
  loading.value = true
  try {
    const response = await queryApi.globalSearch(query.value)
    results.value = response.result || []
    handoffAvailable.value = response.handoff_available === true
    searchComplete.value = response.search_complete !== false
  } finally {
    loading.value = false
  }
}

const createHandoff = async () => {
  const handoff = await queryApi.createHandoff(query.value)
  if (handoff.status === 'notified') message.success('已通知人工处理。')
  else message.warning('已登记转人工请求，但企业微信通知尚未完成。')
  handoffAvailable.value = false
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
    <a-empty v-else-if="!loading && query" class="results" :description="searchComplete ? '暂无可确认的知识库答案' : '部分知识库检索失败，请稍后重试'" />
    <a-button v-if="handoffAvailable" class="results" type="primary" @click="createHandoff">转人工处理</a-button>
  </a-modal>
</template>

<style scoped lang="less">
.results { margin-top: 16px; max-height: 420px; overflow-y: auto; }
.content { white-space: pre-wrap; color: var(--text-color-secondary); }
</style>
