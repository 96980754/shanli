<template>
  <a-modal
    :open="open"
    :title="$t('docVersion.selectTitle')"
    width="720px"
    :confirm-loading="loading"
    :ok-button-props="{ disabled: !selectedDocument }"
    :ok-text="$t('docVersion.selectButton')"
    :cancel-text="$t('common.cancel')"
    @ok="confirmSelection"
    @cancel="close"
  >
    <a-input-search
      v-model:value="keyword"
      :placeholder="$t('docVersion.searchPlaceholder')"
      allow-clear
      class="document-search-input"
    />

    <div class="document-search-results">
      <a-spin v-if="loading && items.length === 0" :tip="$t('docVersion.searchingTip')" />
      <a-alert v-else-if="error" type="error" :message="error" show-icon />
      <a-empty v-else-if="items.length === 0" :description="$t('docVersion.noTargetDoc')" />
      <button
        v-for="item in items"
        v-else
        :key="item.file_id"
        type="button"
        class="document-result"
        :class="{ selected: selectedDocument?.file_id === item.file_id }"
        @click="selectedDocument = item"
        @dblclick="selectAndConfirm(item)"
      >
        <FileTypeIcon :file-type="item.file_type" :size="18" />
        <span class="document-result-main">
          <span class="document-result-name" :title="item.filename">{{ item.filename }}</span>
          <span class="document-result-meta">{{ formatTime(item.updated_at || item.created_at) }}</span>
        </span>
      </button>
    </div>

    <a-pagination
      v-if="total > pageSize"
      v-model:current="page"
      :page-size="pageSize"
      :total="total"
      size="small"
      :show-size-changer="false"
      class="document-search-pagination"
      @change="loadDocuments"
    />
  </a-modal>
</template>

<script setup>
import { onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { documentApi } from '@/apis/knowledge_api'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: String, default: '' },
  selectedFileId: { type: String, default: undefined }
})

const emit = defineEmits(['update:open', 'select'])

const { t } = useI18n()

const keyword = ref('')
const items = ref([])
const selectedDocument = ref(null)
const loading = ref(false)
const error = ref('')
const page = ref(1)
const pageSize = 30
const total = ref(0)
let searchTimer = null
let requestId = 0

const close = () => emit('update:open', false)

const formatTime = (value) => {
  if (!value) return t('docVersion.updatedUnknown')
  return new Date(value).toLocaleString()
}

const loadDocuments = async () => {
  if (!props.kbId) return
  const currentRequestId = ++requestId
  loading.value = true
  error.value = ''
  try {
    const response = await documentApi.searchDocuments({
      kb_id: props.kbId,
      keyword: keyword.value.trim(),
      page: page.value,
      page_size: pageSize
    })
    if (currentRequestId !== requestId) return
    // 版本目标必须是文档，过滤掉文件夹结果（search_documents 为全库搜索/知识库浏览保留文件夹）
    items.value = (response?.items || []).filter((item) => !item.is_folder)
    total.value = Number(response?.total) || 0
    selectedDocument.value =
      items.value.find((item) => item.file_id === props.selectedFileId) || selectedDocument.value
  } catch (err) {
    if (currentRequestId !== requestId) return
    items.value = []
    total.value = 0
    error.value = err?.message || t('docVersion.searchFailed')
  } finally {
    if (currentRequestId === requestId) loading.value = false
  }
}

const confirmSelection = () => {
  if (!selectedDocument.value) return
  emit('select', selectedDocument.value)
  close()
}

const selectAndConfirm = (item) => {
  selectedDocument.value = item
  confirmSelection()
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    keyword.value = ''
    page.value = 1
    selectedDocument.value = null
    loadDocuments()
  }
)

watch(keyword, () => {
  if (!props.open) return
  if (searchTimer) clearTimeout(searchTimer)
  requestId += 1
  loading.value = true
  page.value = 1
  searchTimer = setTimeout(loadDocuments, 250)
})

onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer)
  requestId += 1
})
</script>

<style lang="less" scoped>
.document-search-input {
  margin-bottom: 16px;
}

.document-search-results {
  min-height: 280px;
  max-height: 420px;
  overflow-y: auto;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;

  :deep(.ant-spin),
  :deep(.ant-empty),
  :deep(.ant-alert) {
    margin: auto;
  }
}

.document-result {
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-800);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;

  &:hover {
    background: var(--gray-50);
  }

  &.selected {
    border-color: var(--main-color);
    background: var(--main-20);
  }
}

.document-result-main {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
}

.document-result-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 500;
}

.document-result-meta {
  color: var(--gray-500);
  font-size: 12px;
}

.document-search-pagination {
  margin-top: 16px;
  text-align: right;
}
</style>
