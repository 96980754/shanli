<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { message } from 'ant-design-vue'
import { queryApi } from '@/apis/knowledge_api'

const { t } = useI18n()

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
  if (handoff.status === 'notified') message.success(t('globalSearch.handoffNotified'))
  else message.warning(t('globalSearch.handoffRegistered'))
  handoffAvailable.value = false
}
</script>

<template>
  <a-modal v-model:open="open" :title="$t('globalSearch.title')" :footer="null" @ok="search">
    <a-input-search v-model:value="query" :placeholder="$t('globalSearch.placeholder')" :loading="loading" :enter-button="$t('common.search')" @search="search" />
    <a-list v-if="results.length" class="results" :data-source="results" item-layout="vertical">
      <template #renderItem="{ item }">
        <a-list-item>
          <a-list-item-meta :title="item.kb_name">
            <template #description>
              <div v-if="item.file_dir" class="file-dir">{{ item.file_dir }}</div>
              <div class="file-name">{{ item.file_name || item.filename || $t('globalSearch.kbSnippet') }}</div>
            </template>
          </a-list-item-meta>
          <div class="content">{{ item.content || item.text }}</div>
        </a-list-item>
      </template>
    </a-list>
    <a-empty v-else-if="!loading && query" class="results" :description="searchComplete ? $t('globalSearch.noConfirmedAnswer') : $t('globalSearch.partialFailed')" />
    <a-button v-if="handoffAvailable" class="results" type="primary" @click="createHandoff">{{ $t('globalSearch.handoff') }}</a-button>
  </a-modal>
</template>

<style scoped lang="less">
.results { margin-top: 16px; max-height: 420px; overflow-y: auto; }
.content { white-space: pre-wrap; color: var(--text-color-secondary); }
.file-dir { color: var(--text-color-secondary); font-size: 12px; }
.file-name { color: var(--text-color); font-weight: 500; }
</style>
