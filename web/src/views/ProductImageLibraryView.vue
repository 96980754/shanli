<template>
  <div class="product-image-library layout-container">
    <PageHeader :title="$t('nav.productImages')" />
    <a-card class="library-card">
      <div class="toolbar">
        <a-select
          v-model:value="selectedKbId"
          class="kb-select"
          :placeholder="$t('productImages.filterKbPlaceholder')"
          :options="kbOptions"
          allow-clear
        />
        <a-space :size="8">
          <a-button :loading="loading" @click="loadAll">{{ $t('common.refresh') }}</a-button>
          <a-button
            type="primary"
            :loading="rebuilding"
            :disabled="!selectedKbId"
            @click="handleRebuild"
          >
            {{ $t('productImages.rebuildIndex') }}
          </a-button>
        </a-space>
      </div>

      <a-upload-dragger
        v-if="selectedKbId"
        class="upload-area"
        :custom-request="queueUpload"
        :show-upload-list="false"
        accept=".jpg,.jpeg,.png,.webp,.bmp"
        multiple
      >
        <p class="ant-upload-drag-icon">
          <UploadCloud :size="36" :stroke-width="1.5" />
        </p>
        <p class="ant-upload-text">{{ $t('productImages.uploadText', { name: selectedKbName }) }}</p>
        <p class="ant-upload-hint">
          {{ $t('productImages.uploadHint') }}
        </p>
      </a-upload-dragger>
      <a-alert
        v-else-if="allImages.length > 0"
        class="upload-hint"
        type="info"
        show-icon
        :message="$t('productImages.selectKbHint')"
      />

      <a-table
        :columns="columns"
        :data-source="filteredImages"
        :loading="loading || uploading"
        row-key="object_name"
        size="middle"
        :pagination="false"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'thumbnail'">
            <img :src="rewriteMinioImageUrls(record.image_url)" class="ref-thumb" alt="" />
          </template>
          <template v-else-if="column.key === 'indexed'">
            <a-tag :color="record.indexed ? 'green' : 'default'">
              {{ record.indexed ? $t('productImages.indexed') : $t('productImages.notIndexed') }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'actions'">
            <a-popconfirm
              :title="$t('productImages.deleteConfirmTitle')"
              :ok-text="$t('common.delete')"
              :cancel-text="$t('common.cancel')"
              @confirm="handleRemove(record)"
            >
              <a-button type="link" danger>{{ $t('common.delete') }}</a-button>
            </a-popconfirm>
          </template>
        </template>
        <template #emptyText>
          <a-empty :description="$t('productImages.empty')" />
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import { UploadCloud } from 'lucide-vue-next'
import PageHeader from '@/components/shared/PageHeader.vue'
import { databaseApi, referenceImageApi } from '@/apis/knowledge_api'
import { rewriteMinioImageUrls } from '@/utils/minioUrl'

const { t } = useI18n()

const loading = ref(false)
const uploading = ref(false)
const rebuilding = ref(false)
const databases = ref([])
const allImages = ref([])
const selectedKbId = ref(undefined)

const kbOptions = computed(() =>
  databases.value
    .filter((db) => (db.kb_type || 'milvus') === 'milvus')
    .map((db) => ({ value: db.kb_id, label: db.name || db.kb_id }))
)
const selectedKbName = computed(
  () => kbOptions.value.find((item) => item.value === selectedKbId.value)?.label ?? ''
)
const filteredImages = computed(() => {
  if (!selectedKbId.value) return allImages.value
  return allImages.value.filter((item) => item.kb_id === selectedKbId.value)
})

const columns = computed(() => [
  { title: t('productImages.col.thumbnail'), key: 'thumbnail', width: 90 },
  { title: t('productImages.col.product'), dataIndex: 'product', key: 'product' },
  { title: t('db.title'), dataIndex: 'kb_name', key: 'kb_name' },
  { title: t('productImages.col.indexStatus'), key: 'indexed', width: 110 },
  { title: t('productImages.col.actions'), key: 'actions', width: 90 }
])

async function loadAll() {
  loading.value = true
  try {
    const [dbResp, imgResp] = await Promise.all([
      databaseApi.getDatabases(),
      referenceImageApi.listAll()
    ])
    databases.value = dbResp.databases || []
    allImages.value = imgResp.images || []
  } catch (err) {
    message.error(err?.message || t('productImages.loadFail'))
  } finally {
    loading.value = false
  }
}

// 拖拽/选择的多张图并入一个请求提交（后端接收 files 列表）
const pendingFiles = []
let flushTimer = null

function queueUpload({ file }) {
  pendingFiles.push(file)
  clearTimeout(flushTimer)
  flushTimer = setTimeout(flushUploads, 30)
}

async function flushUploads() {
  if (pendingFiles.length === 0) return
  const batch = pendingFiles.splice(0)
  uploading.value = true
  try {
    const resp = await referenceImageApi.upload(selectedKbId.value, batch)
    const count = resp.images?.length ?? batch.length
    message.success(t('productImages.uploadSuccess', { count }))
    await loadAll()
  } catch (err) {
    message.error(err?.message || t('productImages.uploadFail'))
  } finally {
    uploading.value = false
  }
}

async function handleRebuild() {
  rebuilding.value = true
  try {
    const resp = await referenceImageApi.rebuild(selectedKbId.value)
    const parts = [t('productImages.indexedCount', { count: resp.indexed ?? 0 })]
    if (resp.errors) parts.push(t('productImages.failedCount', { count: resp.errors }))
    message.success(t('productImages.rebuildComplete', { parts: parts.join('，') }))
    await loadAll()
  } catch (err) {
    message.error(err?.message || t('productImages.rebuildFail'))
  } finally {
    rebuilding.value = false
  }
}

async function handleRemove(record) {
  try {
    await referenceImageApi.remove(record.kb_id, record.product)
    message.success(t('productImages.deleted', { name: record.product }))
    await loadAll()
  } catch (err) {
    message.error(err?.message || t('common.deleteFailed'))
  }
}

onMounted(loadAll)
</script>

<style scoped lang="less">
.product-image-library {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  padding-bottom: 24px;
}
.library-card {
  margin: 0 var(--page-padding);
  flex: 1;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.kb-select {
  width: 240px;
}
.upload-area {
  margin-bottom: 16px;
}
.upload-hint {
  margin-bottom: 16px;
}
.ref-thumb {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--gray-100);
  background: var(--gray-50);
}
</style>
