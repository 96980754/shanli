<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { documentBrowseApi, databaseApi } from '@/apis/knowledge_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const router = useRouter()
const keyword = ref('')
const publisher = ref('')
const dates = ref([])
const loading = ref(false)
const documents = ref([])
const hotDocuments = ref([])
const databases = ref([])

const categories = computed(() => databases.value.reduce((result, database) => {
  const category = database.kb_type || 'other'
  ;(result[category] ||= []).push(database)
  return result
}, {}))

const search = async () => {
  loading.value = true
  try {
    const response = await documentBrowseApi.search({
      keyword: keyword.value,
      publisher: publisher.value,
      updated_from: dates.value?.[0]?.toISOString(),
      updated_to: dates.value?.[1]?.endOf('day').toISOString()
    })
    documents.value = response.items || []
  } finally { loading.value = false }
}

// filename 是完整相对路径（如 定位资料-证书/C10—UN38.3的报告证书/xxx.pdf），目录路径 = 去掉最后一个 / 段
const folderPathOf = (filename = '') => {
  const index = filename.lastIndexOf('/')
  return index === -1 ? '' : filename.slice(0, index)
}

// 搜索/热门文档/分类入口统一跳转到知识库文件浏览，并定位到文档所在目录
const browseDirectory = (item) => {
  const folderPath = folderPathOf(item?.filename)
  router.push({
    path: `/extensions/knowledgebase/${item.kb_id}`,
    query: folderPath ? { path: folderPath } : undefined
  })
}

onMounted(async () => {
  const [databaseResponse, hotResponse] = await Promise.all([databaseApi.getAccessibleDatabases(), documentBrowseApi.hot()])
  databases.value = (databaseResponse.databases || []).filter((item) => item.supports_documents !== false)
  hotDocuments.value = hotResponse.items || []
})
</script>

<template>
  <div class="knowledge-browser layout-container">
    <PageHeader title="全库搜索" subtitle="按分类浏览目录，或搜索全部可访问文档" />
    <a-row :gutter="16">
      <a-col :xs="24" :lg="16">
        <a-card title="全知识库文档搜索">
          <a-space wrap class="filters">
            <a-input v-model:value="keyword" placeholder="文件名或正文关键词" @press-enter="search" />
            <a-input v-model:value="publisher" placeholder="发布人 UID" @press-enter="search" />
            <a-range-picker v-model:value="dates" />
            <a-button type="primary" :loading="loading" @click="search">搜索</a-button>
          </a-space>
          <a-list :loading="loading" :data-source="documents" class="document-list">
            <template #renderItem="{ item }">
              <a-list-item><a-list-item-meta :title="item.filename" :description="`${item.kb_name} · 发布人：${item.publisher_name || item.created_by || '未知'}`" /><template #actions><a @click="browseDirectory(item)">打开目录</a></template></a-list-item>
            </template>
          </a-list>
        </a-card>
      </a-col>
      <a-col :xs="24" :lg="8">
        <a-card title="热门文档" class="side-card"><a-list :data-source="hotDocuments" size="small"><template #renderItem="{ item }"><a-list-item><a @click="browseDirectory(item)">{{ item.filename }}</a><span>{{ item.view_count }} 次浏览</span></a-list-item></template></a-list></a-card>
        <a-card title="知识库分类 / 目录" class="side-card"><template v-for="(items, category) in categories" :key="category"><h4>{{ category }}</h4><a-list :data-source="items" size="small"><template #renderItem="{ item }"><a-list-item><a @click="browseDirectory(item)">{{ item.name }}</a></a-list-item></template></a-list></template></a-card>
      </a-col>
    </a-row>
  </div>
</template>

<style scoped lang="less">
.filters { width: 100%; } .filters :deep(.ant-input), .filters :deep(.ant-picker) { min-width: 180px; } .document-list { margin-top: 16px; } .side-card { margin-top: 16px; } .side-card:first-child { margin-top: 0; } .side-card .ant-list-item { display: flex; justify-content: space-between; gap: 12px; }
</style>
