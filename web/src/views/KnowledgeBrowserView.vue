<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Folder } from 'lucide-vue-next'
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

// 搜索方式：文件名 / 文件夹 / 内容，默认只按文件名匹配
const searchType = ref('filename')
const searchTypeOptions = [
  { label: '文件名', value: 'filename' },
  { label: '文件夹', value: 'folder' },
  { label: '内容', value: 'content' }
]
const searchPlaceholder = computed(() => ({
  filename: '搜索文件名',
  folder: '搜索文件夹名',
  content: '搜索正文关键词'
})[searchType.value])

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
      search_type: searchType.value,
      publisher: publisher.value,
      updated_from: dates.value?.[0]?.toISOString(),
      updated_to: dates.value?.[1]?.endOf('day').toISOString()
    })
    documents.value = response.items || []
  } finally { loading.value = false }
}

// filename 是完整相对路径（如 定位资料-证书/C10—UN38.3的报告证书/xxx.pdf），
// 展示时拆成「目录路径 → 文件名」两行，实现文件夹/文件分离：目录灰色小字在上、文件名加粗在下。
const splitPath = (filename = '') => {
  const index = filename.lastIndexOf('/')
  return index === -1
    ? { folder: '', name: filename }
    : { folder: filename.slice(0, index), name: filename.slice(index + 1) }
}

// 目录路径 = 去掉最后一个 / 段（用于跳转定位）
const folderPathOf = (filename = '') => splitPath(filename).folder

// 结果行展示路径：虚拟文件夹（路径派生目录）的 filename 即完整目录路径，拆成「父目录 → 文件夹名」两行；
// 真实文件夹的 filename 即文件夹名（无父目录行）；文件为「目录路径 → 文件名」。
const displayPath = (item) => {
  if (item.is_virtual_folder) return splitPath(item.filename)
  if (item.is_folder) return { folder: '', name: item.filename }
  return splitPath(item.filename)
}

// 搜索/热门文档/分类入口统一跳转到知识库文件浏览，并定位到目标目录。
// 真实文件夹（is_folder）走 folder_id 深链（parent_id 树）；文件走 path 路径型虚拟目录；
// 知识库分类卡片项（无 filename/is_folder）进入知识库根层。
const browseDirectory = (item) => {
  if (item?.is_folder) {
    if (item.is_virtual_folder) {
      // 虚拟目录（从文件路径派生，无真实 folder 行）：用 ?path= 打开该目录
      router.push({
        path: `/extensions/knowledgebase/${item.kb_id}`,
        query: { path: item.filename }
      })
      return
    }
    router.push({
      path: `/extensions/knowledgebase/${item.kb_id}`,
      query: { folder_id: item.file_id }
    })
    return
  }
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
            <a-segmented v-model:value="searchType" :options="searchTypeOptions" />
            <a-input v-model:value="keyword" :placeholder="searchPlaceholder" @press-enter="search" />
            <a-input v-model:value="publisher" placeholder="发布人 UID" @press-enter="search" />
            <a-range-picker v-model:value="dates" />
            <a-button type="primary" :loading="loading" @click="search">搜索</a-button>
          </a-space>
          <a-list :loading="loading" :data-source="documents" class="document-list">
            <template #renderItem="{ item }">
              <a-list-item>
                <a-list-item-meta>
                  <template #title>
                    <span class="result-title">
                      <Folder v-if="item.is_folder" :size="14" class="folder-icon" />
                      <span class="doc-leaf">{{ displayPath(item).name }}</span>
                      <a-tag v-if="item.is_folder" class="folder-tag">文件夹</a-tag>
                    </span>
                  </template>
                  <template #description>
                    <span v-if="displayPath(item).folder" class="doc-folder">{{ displayPath(item).folder }}</span>
                    <span class="doc-meta">
                      {{
                        item.is_folder
                          ? `${item.kb_name} · 文件夹`
                          : `${item.kb_name} · 发布人：${item.publisher_name || item.created_by || '未知'}`
                      }}
                    </span>
                  </template>
                </a-list-item-meta>
                <template #actions><a @click="browseDirectory(item)">打开目录</a></template>
              </a-list-item>
            </template>
          </a-list>
        </a-card>
      </a-col>
      <a-col :xs="24" :lg="8">
        <a-card title="热门文档" class="side-card"><a-list :data-source="hotDocuments" size="small"><template #renderItem="{ item }"><a-list-item><a @click="browseDirectory(item)">{{ splitPath(item.filename).name }}</a><span>{{ item.view_count }} 次浏览</span></a-list-item></template></a-list></a-card>
        <a-card title="知识库分类 / 目录" class="side-card"><template v-for="(items, category) in categories" :key="category"><h4>{{ category }}</h4><a-list :data-source="items" size="small"><template #renderItem="{ item }"><a-list-item><a @click="browseDirectory(item)">{{ item.name }}</a></a-list-item></template></a-list></template></a-card>
      </a-col>
    </a-row>
  </div>
</template>

<style scoped lang="less">
.filters { width: 100%; } .filters :deep(.ant-input), .filters :deep(.ant-picker) { min-width: 180px; } .document-list { margin-top: 16px; } .side-card { margin-top: 16px; } .side-card:first-child { margin-top: 0; } .side-card .ant-list-item { display: flex; justify-content: space-between; gap: 12px; } .result-title { display: inline-flex; align-items: center; gap: 6px; } .folder-icon { color: var(--main-color); flex-shrink: 0; } .folder-tag { margin-inline-end: 0; font-size: 12px; line-height: 18px; } .doc-leaf { font-weight: 500; } .doc-folder { display: block; color: var(--gray-500); font-size: 12px; } .doc-meta { display: block; color: var(--text-color-secondary); font-size: 12px; }
</style>
