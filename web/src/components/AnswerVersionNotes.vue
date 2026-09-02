<template>
  <div v-if="versionedGroups.length" class="answer-version-notes" :class="{ clarify: versionIntent }">
    <Info :size="13" class="note-icon" />
    <div class="note-body">
      <template v-if="versionIntent">
        <p class="note-heading">{{ $t('sources.versionIntentHeading') }}</p>
        <p
          v-for="group in versionedGroups"
          :key="getGroupKey(group)"
          class="note-line"
        >
          {{
            $t('sources.versionIntentDocLine', {
              name: getGroupName(group),
              current: getCurrentVersion(group),
              history: getHistoryLabel(group)
            })
          }}
        </p>
        <p class="note-guide">{{ $t('sources.versionIntentGuide') }}</p>
      </template>
      <template v-else>
        <p
          v-for="group in versionedGroups"
          :key="getGroupKey(group)"
          class="note-line"
        >
          {{
            $t('sources.answerBasedOnCurrentVersion', {
              name: getGroupName(group),
              version: getCurrentVersion(group)
            })
          }}
        </p>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Info } from 'lucide-vue-next'
import { documentApi } from '@/apis/knowledge_api'
import { MessageProcessor } from '@/utils/messageProcessor'
import {
  groupSourceFileIdsByKnowledgeBase,
  normalizeSourceVersions
} from '@/utils/knowledgeSourceVersions'
import { detectVersionIntent } from '@/utils/versionIntent'

const props = defineProps({
  chunks: {
    type: Array,
    default: () => []
  },
  queryText: {
    type: String,
    default: ''
  }
})

const versionMap = ref(new Map())
let versionLoadId = 0

const fileGroups = computed(() => MessageProcessor.groupKnowledgeChunksByDocument(props.chunks))

// 与来源面板共用 list_for_current_files：只为实际被引用的文档拉取版本链信息。
// 面板展开时各自独立请求（轻量元数据接口，避免跨组件耦合共享状态）。
const loadVersions = async (groups) => {
  const requests = groupSourceFileIdsByKnowledgeBase(groups)
  const loadId = ++versionLoadId
  versionMap.value = new Map()
  if (!requests.length) return

  const settled = await Promise.allSettled(
    requests.map(async ({ kbId, fileIds }) => ({
      kbId,
      ...(await documentApi.getSourceVersions(kbId, fileIds))
    }))
  )
  if (loadId !== versionLoadId) return
  const responses = settled
    .filter((item) => item.status === 'fulfilled')
    .map((item) => item.value)
  versionMap.value = normalizeSourceVersions(responses)
}

watch(fileGroups, loadVersions, { immediate: true })

const getGroupKey = (group) => `${group.kb_id}::${group.file_id || group.filename}`
const getSourceVersion = (group) => versionMap.value.get(getGroupKey(group))

// 仅当被引用文档真实存在历史版本时展示注记（§3 bullet-2 / §4 澄清的前提）
const versionedGroups = computed(() =>
  fileGroups.value.filter((group) => getSourceVersion(group)?.history_versions?.length > 0)
)

const getGroupName = (group) => group.displayName || group.filename || ''
const getCurrentVersion = (group) => getSourceVersion(group)?.document_version ?? ''
const getHistoryLabel = (group) => {
  const history = getSourceVersion(group)?.history_versions || []
  const sorted = [...history].sort(
    (a, b) => Number(a.document_version || 0) - Number(b.document_version || 0)
  )
  return sorted.map((version) => `V${version.document_version}`).join('/')
}

// §4 版本意图：规则命中（无 LLM 门禁）后切换为澄清注记 + 对比引导
const versionIntent = computed(() => detectVersionIntent(props.queryText))
</script>

<style scoped lang="less">
.answer-version-notes {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
  color: var(--gray-600);
  font-size: 12px;
  line-height: 1.6;

  &.clarify {
    border-color: var(--main-100);
    background: var(--main-50);
    color: var(--main-700);
  }

  .note-icon {
    flex-shrink: 0;
    margin-top: 3px;
    color: var(--gray-500);
  }

  &.clarify .note-icon {
    color: var(--main-700);
  }

  .note-body {
    min-width: 0;
    flex: 1;
  }

  .note-heading {
    font-weight: 500;
    color: var(--main-700);
  }

  .note-line,
  .note-guide,
  .note-heading {
    margin: 0;
  }

  .note-line + .note-line,
  .note-heading + .note-line {
    margin-top: 2px;
  }

  .note-guide {
    margin-top: 4px;
  }
}
</style>
