<template>
  <div class="product-image-library layout-container">
    <PageHeader title="产品图库" />
    <a-card class="library-card">
      <div class="toolbar">
        <a-select
          v-model:value="selectedKbId"
          class="kb-select"
          placeholder="筛选知识库（全部）"
          :options="kbOptions"
          allow-clear
        />
        <a-space :size="8">
          <a-button :loading="loading" @click="loadAll">刷新</a-button>
          <a-button
            type="primary"
            :loading="rebuilding"
            :disabled="!selectedKbId"
            @click="handleRebuild"
          >
            重建索引
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
        <p class="ant-upload-text">点击或拖拽上传参照图到「{{ selectedKbName }}」</p>
        <p class="ant-upload-hint">
          每款产品一张清晰图，文件名即产品名（需与库内产品名一致）；上传后点击「重建索引」生效
        </p>
      </a-upload-dragger>
      <a-alert
        v-else-if="allImages.length > 0"
        class="upload-hint"
        type="info"
        show-icon
        message="选择上方知识库后可上传参照图或重建索引"
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
              {{ record.indexed ? '已索引' : '未索引' }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'actions'">
            <a-popconfirm
              title="删除该参照图？将同时清理其索引向量。"
              ok-text="删除"
              cancel-text="取消"
              @confirm="handleRemove(record)"
            >
              <a-button type="link" danger>删除</a-button>
            </a-popconfirm>
          </template>
        </template>
        <template #emptyText>
          <a-empty description="还没有产品参照图" />
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { UploadCloud } from 'lucide-vue-next'
import PageHeader from '@/components/shared/PageHeader.vue'
import { databaseApi, referenceImageApi } from '@/apis/knowledge_api'
import { rewriteMinioImageUrls } from '@/utils/minioUrl'

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

const columns = [
  { title: '缩略图', key: 'thumbnail', width: 90 },
  { title: '产品名', dataIndex: 'product', key: 'product' },
  { title: '知识库', dataIndex: 'kb_name', key: 'kb_name' },
  { title: '索引状态', key: 'indexed', width: 110 },
  { title: '操作', key: 'actions', width: 90 }
]

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
    message.error(err?.message || '加载产品图库失败')
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
    message.success(`已上传 ${count} 张参照图`)
    await loadAll()
  } catch (err) {
    message.error(err?.message || '上传参照图失败')
  } finally {
    uploading.value = false
  }
}

async function handleRebuild() {
  rebuilding.value = true
  try {
    const resp = await referenceImageApi.rebuild(selectedKbId.value)
    const parts = [`已索引 ${resp.indexed ?? 0} 张`]
    if (resp.errors) parts.push(`失败 ${resp.errors} 张`)
    message.success(`索引重建完成：${parts.join('，')}`)
    await loadAll()
  } catch (err) {
    message.error(err?.message || '重建索引失败')
  } finally {
    rebuilding.value = false
  }
}

async function handleRemove(record) {
  try {
    await referenceImageApi.remove(record.kb_id, record.product)
    message.success(`已删除：${record.product}`)
    await loadAll()
  } catch (err) {
    message.error(err?.message || '删除失败')
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
