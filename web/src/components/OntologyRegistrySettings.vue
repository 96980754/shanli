<template>
  <div class="ontology-registry-settings">
    <div class="section-header">
      <div>
        <h3>Core Ontology</h3>
        <p>通过结构化表单定义实体、关系、别名和属性，创建成功后立即可用于知识图谱。</p>
      </div>
      <a-button v-if="userStore.isSuperAdmin" type="primary" class="create-btn" @click="openCreate">
        <Plus :size="16" />
        新建 Core Ontology
      </a-button>
    </div>

    <a-collapse v-if="userStore.isSuperAdmin" class="upload-collapse" ghost>
      <a-collapse-panel key="upload" header="高级：上传 Ontology Bundle">
        <div class="upload-row">
          <span>ZIP 根目录必须且只能包含 schema.json、entity.yaml、relation.yaml、property.yaml。</span>
          <a-upload
            accept=".zip,application/zip"
            :multiple="true"
            :show-upload-list="false"
            :before-upload="beforeUpload"
            :custom-request="uploadBundle"
          >
            <a-button :loading="uploading">
              <Upload :size="16" />
              上传 Bundle
            </a-button>
          </a-upload>
        </div>
      </a-collapse-panel>
    </a-collapse>

    <a-spin :spinning="loading">
      <a-empty v-if="!loading && !items.length" description="暂无可用 Ontology" />
      <div v-else class="registry-list">
        <div v-for="item in items" :key="entryKey(item)" class="registry-card">
          <div class="registry-main">
            <div class="registry-title">{{ item.name }}</div>
            <div class="registry-meta">
              <span>{{ item.registry_id }} · {{ item.version }}</span>
              <a-tag :color="item.source === 'builtin' ? 'blue' : 'green'">
                {{ item.source === 'builtin' ? '内置' : '自定义' }}
              </a-tag>
            </div>
          </div>
          <div class="registry-actions">
            <a-button size="small" type="text" @click="openDetail(item, 'view')">查看</a-button>
            <a-button
              v-if="userStore.isSuperAdmin && item.source === 'uploaded'"
              size="small"
              type="text"
              @click="openDetail(item, 'edit')"
            >编辑</a-button>
            <a-tooltip :title="item.digest">
              <code>{{ item.digest.slice(0, 12) }}</code>
            </a-tooltip>
          </div>
        </div>
      </div>
    </a-spin>

    <OntologyEditorModal
      v-model:open="editorOpen"
      :mode="editorMode"
      :detail="editorDetail"
      @created="loadItems"
    />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { Plus, Upload } from 'lucide-vue-next'
import { ontologyRegistryApi } from '@/apis/ontology_api'
import { useUserStore } from '@/stores/user'
import OntologyEditorModal from '@/components/OntologyEditorModal.vue'

const userStore = useUserStore()
const items = ref([])
const loading = ref(false)
const uploading = ref(false)
const editorOpen = ref(false)
const editorMode = ref('create')
const editorDetail = ref(null)

const openCreate = () => {
  editorMode.value = 'create'
  editorDetail.value = null
  editorOpen.value = true
}

const openDetail = async (item, mode) => {
  loading.value = true
  try {
    editorDetail.value = await ontologyRegistryApi.detail(item)
    editorMode.value = mode
    editorOpen.value = true
  } catch (error) {
    message.error(error?.response?.data?.detail || error?.message || '加载 Ontology 详情失败')
  } finally {
    loading.value = false
  }
}

const entryKey = (item) => `${item.registry_id}:${item.version}:${item.digest}`

const loadItems = async () => {
  loading.value = true
  try {
    const result = await ontologyRegistryApi.list()
    items.value = result.items || []
  } catch (error) {
    message.error(error?.response?.data?.detail || error?.message || '加载 Ontology 失败')
  } finally {
    loading.value = false
  }
}

const beforeUpload = (file) => {
  if (!file.name?.toLowerCase().endsWith('.zip')) {
    message.error(`${file.name} 不是 ZIP 文件`)
    return false
  }
  if (file.size > 5 * 1024 * 1024) {
    message.error(`${file.name} 超过 5 MiB`)
    return false
  }
  return true
}

const uploadBundle = async ({ file, onSuccess, onError }) => {
  uploading.value = true
  try {
    const result = await ontologyRegistryApi.upload(file)
    message.success(result.already_exists ? `${file.name} 已存在` : `${file.name} 上传成功`)
    onSuccess?.(result)
    await loadItems()
  } catch (error) {
    const detail = error?.response?.data?.detail || error?.message || '上传失败'
    message.error(`${file.name}: ${detail}`)
    onError?.(error)
  } finally {
    uploading.value = false
  }
}

onMounted(loadItems)
</script>

<style scoped lang="less">
.ontology-registry-settings {
  padding: 4px;
}

.section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;

  h3 {
    margin: 0 0 6px;
    color: var(--gray-1000);
  }

  p {
    margin: 0;
    color: var(--gray-600);
    line-height: 1.6;
  }
}

.create-btn,
.upload-row :deep(.ant-btn) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.upload-collapse {
  margin-bottom: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
}

.upload-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  color: var(--gray-600);
  font-size: 13px;
}

.registry-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.registry-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--gray-0);
}

.registry-title {
  color: var(--gray-1000);
  font-weight: 600;
}

.registry-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  color: var(--gray-600);
  font-size: 12px;
}

.registry-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

code {
  color: var(--gray-700);
  font-size: 12px;
}

@media (max-width: 720px) {
  .section-header,
  .upload-row {
    flex-direction: column;
  }
}
</style>
