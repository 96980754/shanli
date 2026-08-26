<template>
  <div class="product-ref-panel">
    <div class="panel-header">
      <div>
        <h3>产品参照图</h3>
        <p>
          每款产品上传一张清晰图，文件名即产品名（需与库内产品名一致），供按外观检索产品（贴牌/无标识场景）。
        </p>
      </div>
      <a-space :size="8">
        <a-button :loading="loading" @click="loadImages">刷新</a-button>
        <a-button type="primary" :loading="rebuilding" @click="handleRebuild"> 重建索引 </a-button>
      </a-space>
    </div>

    <div class="upload-area">
      <a-upload-dragger
        :custom-request="queueUpload"
        :show-upload-list="false"
        accept=".jpg,.jpeg,.png,.webp,.bmp"
        multiple
      >
        <p class="ant-upload-drag-icon">
          <UploadCloud :size="36" :stroke-width="1.5" />
        </p>
        <p class="ant-upload-text">点击或拖拽上传参照图</p>
        <p class="ant-upload-hint">
          支持 jpg/jpeg/png/webp/bmp，单张 ≤ 20MB；上传后点击「重建索引」生效
        </p>
      </a-upload-dragger>
    </div>

    <a-table
      :columns="columns"
      :data-source="images"
      :loading="loading || uploading"
      row-key="object_name"
      size="middle"
      :pagination="false"
      class="ref-table"
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
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { UploadCloud } from 'lucide-vue-next'
import { referenceImageApi } from '@/apis/knowledge_api'
import { rewriteMinioImageUrls } from '@/utils/minioUrl'

const props = defineProps({
  kbId: { type: String, required: true }
})

const images = ref([])
const loading = ref(false)
const uploading = ref(false)
const rebuilding = ref(false)

const columns = [
  { title: '缩略图', key: 'thumbnail', width: 90 },
  { title: '产品名', dataIndex: 'product', key: 'product' },
  { title: '索引状态', key: 'indexed', width: 110 },
  { title: '操作', key: 'actions', width: 90 }
]

async function loadImages() {
  loading.value = true
  try {
    const resp = await referenceImageApi.list(props.kbId)
    images.value = resp.images || []
  } catch (err) {
    message.error(err?.message || '加载参照图失败')
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
    const resp = await referenceImageApi.upload(props.kbId, batch)
    const count = resp.images?.length ?? batch.length
    message.success(`已上传 ${count} 张参照图`)
    await loadImages()
  } catch (err) {
    message.error(err?.message || '上传参照图失败')
  } finally {
    uploading.value = false
  }
}

async function handleRebuild() {
  rebuilding.value = true
  try {
    const resp = await referenceImageApi.rebuild(props.kbId)
    const parts = [`已索引 ${resp.indexed ?? 0} 张`]
    if (resp.errors) parts.push(`失败 ${resp.errors} 张`)
    message.success(`索引重建完成：${parts.join('，')}`)
    await loadImages()
  } catch (err) {
    message.error(err?.message || '重建索引失败')
  } finally {
    rebuilding.value = false
  }
}

async function handleRemove(record) {
  try {
    await referenceImageApi.remove(props.kbId, record.product)
    message.success(`已删除：${record.product}`)
    await loadImages()
  } catch (err) {
    message.error(err?.message || '删除失败')
  }
}

onMounted(loadImages)
</script>

<style scoped lang="less">
.product-ref-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-shrink: 0;

  h3 {
    margin: 0 0 6px;
    font-size: 18px;
    color: var(--gray-1000);
  }
  p {
    margin: 0;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.6;
  }
}
.upload-area {
  flex-shrink: 0;
}
.ref-table {
  flex: 1;
  min-height: 0;
  overflow: auto;
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
