<template>
  <div
    v-if="versionedGroups.length"
    class="answer-version-notes"
    :class="{ clarify: versionIntent }"
  >
    <Info :size="13" class="note-icon" />
    <div class="note-body">
      <template v-if="versionIntent">
        <p class="note-heading">{{ $t('sources.versionIntentHeading') }}</p>
        <p v-for="group in versionedGroups" :key="getGroupKey(group)" class="note-line">
          {{
            $t('sources.versionIntentDocLine', {
              name: getGroupName(group),
              current: getCurrentVersion(group),
              history: getHistoryLabel(group)
            })
          }}
        </p>
        <p class="note-guide">{{ $t('sources.versionIntentGuide') }}</p>

        <!-- 主动询问：仅最后一个已收尾会话开放（allowAsk 由父级判定下传），
             每个版本家族各自就地展开「查看某一历史版本 / 对比两个版本」。 -->
        <template v-if="allowAsk">
          <p class="ask-guide">{{ $t('sources.versionAskPrompt') }}</p>
          <div
            v-for="group in versionedGroups"
            :key="`ask-${getGroupKey(group)}`"
            class="ask-block"
          >
            <div v-if="!isDismissed(group)" class="ask-content">
              <div class="ask-actions">
                <span class="ask-name">{{ getGroupName(group) }}</span>
                <button
                  type="button"
                  class="ask-btn"
                  :class="{ active: isOpen(group, 'read') }"
                  @click="toggleMode(group, 'read')"
                >
                  {{ $t('sources.versionAskReadAction') }}
                </button>
                <button
                  type="button"
                  class="ask-btn"
                  :class="{ active: isOpen(group, 'compare') }"
                  @click="toggleMode(group, 'compare')"
                >
                  {{ $t('sources.versionAskCompareAction') }}
                </button>
                <button type="button" class="ask-btn quiet" @click="dismissGroup(group)">
                  {{ $t('sources.versionAskDismiss') }}
                </button>
              </div>

              <div v-if="isOpen(group, 'read')" class="ask-panel">
                <p class="ask-heading">{{ $t('sources.versionAskReadHeading') }}</p>
                <ul class="ask-list">
                  <li
                    v-for="cand in readCandidates(group)"
                    :key="cand.file_id"
                    class="ask-item"
                    role="button"
                    tabindex="0"
                    @click="runView(group, cand)"
                    @keydown.enter="runView(group, cand)"
                  >
                    <span class="ask-row-label">{{ candidateLabel(group, cand) }}</span>
                    <span class="ask-run">{{ $t('sources.versionAskRunView') }}</span>
                  </li>
                </ul>
              </div>

              <div v-else-if="isOpen(group, 'compare')" class="ask-panel">
                <p class="ask-heading">{{ $t('sources.versionAskCompareHeading') }}</p>
                <ul class="ask-list">
                  <li
                    v-for="cand in compareCandidates(group)"
                    :key="cand.file_id"
                    class="ask-item"
                    :class="{ picked: isPicked(cand), current: cand.is_current }"
                    role="checkbox"
                    :aria-checked="isPicked(cand)"
                    tabindex="0"
                    @click="togglePick(cand)"
                    @keydown.enter="togglePick(cand)"
                  >
                    <span class="ask-check" :class="{ checked: isPicked(cand) }">
                      <Check v-if="isPicked(cand)" :size="11" />
                    </span>
                    <span class="ask-row-label">{{ candidateLabel(group, cand) }}</span>
                  </li>
                </ul>
                <p v-if="invalidPair" class="ask-error">
                  {{ $t('sources.versionAskInvalidPair') }}
                </p>
                <button
                  type="button"
                  class="ask-run-btn"
                  :disabled="selection.length !== 2"
                  @click="runCompare(group)"
                >
                  {{ $t('sources.versionAskRunCompare') }}
                </button>
              </div>
            </div>
          </div>
        </template>
      </template>
      <template v-else>
        <p v-for="group in versionedGroups" :key="getGroupKey(group)" class="note-line">
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
import { useI18n } from 'vue-i18n'
import { Check, Info } from 'lucide-vue-next'
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
  },
  // 是否对本注记开放「查看/对比历史版本」主动询问（仅最后一个已收尾会话下传 true）。
  allowAsk: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['version-ask-run', 'version-ask-dismiss'])

const { t } = useI18n()

const versionMap = ref(new Map())
let versionLoadId = 0
// 已加载请求指纹（kb 列表 + 排序后的文件 id 集）。父级在每次渲染时派生新的 chunks
// 数组引用，但被引用文档集合没变时不应重复拉取——旧实现每次重载都先清空 versionMap
// （注记瞬间消失）再等接口回填（重现），表现为注记条持续闪烁。
let loadedRequestFingerprint = null

const fileGroups = computed(() => MessageProcessor.groupKnowledgeChunksByDocument(props.chunks))

const buildRequestFingerprint = (requests) =>
  requests
    .map(({ kbId, fileIds }) => `${kbId}:${[...(fileIds || [])].sort().join(',')}`)
    .sort()
    .join('|')

// 与来源面板共用 list_for_current_files：只为实际被引用的文档拉取版本链信息。
// 面板展开时各自独立请求（轻量元数据接口，避免跨组件耦合共享状态）。
const loadVersions = async (groups) => {
  const requests = groupSourceFileIdsByKnowledgeBase(groups)
  const fingerprint = buildRequestFingerprint(requests)
  if (!requests.length) {
    // 无被引用文档：清空版本链（注记不显示），只执行一次。
    if (fingerprint === loadedRequestFingerprint) return
    loadedRequestFingerprint = fingerprint
    versionMap.value = new Map()
    return
  }
  // 被引用文档集合未变：沿用现有展示，跳过重载，避免注记消失/重现的闪烁。
  if (fingerprint === loadedRequestFingerprint) return
  const loadId = ++versionLoadId
  loadedRequestFingerprint = fingerprint
  // 回填前不清空 versionMap：旧数据保留到新结果到达再整体替换，界面不闪。

  const settled = await Promise.allSettled(
    requests.map(async ({ kbId, fileIds }) => ({
      kbId,
      ...(await documentApi.getSourceVersions(kbId, fileIds))
    }))
  )
  if (loadId !== versionLoadId) return
  const responses = settled.filter((item) => item.status === 'fulfilled').map((item) => item.value)
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

// §4 版本意图：规则命中（无 LLM 门禁）后切换为澄清注记 + 版本引导
const versionIntent = computed(() => detectVersionIntent(props.queryText))

// ---------------------------------------------------------------- 主动询问

// 当前文件的版本链快照（list_for_current_files 语义：条目即被引用文档的当前版本）
const currentCandidate = (group) => {
  const source = getSourceVersion(group)
  if (!source) return null
  return {
    file_id: source.file_id,
    filename: source.filename || '',
    document_version: source.document_version ?? null,
    is_current: true
  }
}

const sortByVersionDesc = (items) =>
  [...items].sort((a, b) => Number(b.document_version || 0) - Number(a.document_version || 0))

const historyCandidates = (group) =>
  sortByVersionDesc(getSourceVersion(group)?.history_versions || []).map((version) => ({
    file_id: version.file_id,
    filename: version.filename || '',
    document_version: version.document_version ?? null,
    is_current: false
  }))

// read 选择器只列历史版本（当前版已由主回答覆盖）；compare 含当前版，任选两个去重版本。
const readCandidates = (group) => historyCandidates(group)
const compareCandidates = (group) => {
  const list = []
  const current = currentCandidate(group)
  if (current) list.push(current)
  list.push(...historyCandidates(group))
  return list
}

// 去掉扩展名与可能内嵌的 _V1.1 片段，避免与 V 徽标重复展示
const baseName = (filename) =>
  String(filename || '')
    .replace(/\.\w+$/, '')
    .replace(/[_\-\s]*V\d+(\.\d+)*$/i, '')

const candidateLabel = (group, cand) => {
  const parts = []
  if (cand.is_current) parts.push(t('sources.versionAskCurrentLabel'))
  const version =
    cand.document_version !== null &&
    cand.document_version !== undefined &&
    String(cand.document_version).trim() !== ''
      ? `V${cand.document_version}`
      : ''
  if (version) parts.push(version)
  const name = baseName(cand.is_current ? getGroupName(group) : cand.filename)
  if (name) parts.push(name)
  return parts.join(' · ')
}

const openGroupKey = ref(null)
const openMode = ref(null) // 'read' | 'compare'
const selection = ref([])
const invalidPair = ref(false)
const dismissedKeys = ref(new Set())

const isDismissed = (group) => dismissedKeys.value.has(getGroupKey(group))
const isOpen = (group, mode) => openGroupKey.value === getGroupKey(group) && openMode.value === mode

const dismissGroup = (group) => {
  dismissedKeys.value = new Set(dismissedKeys.value).add(getGroupKey(group))
  if (openGroupKey.value === getGroupKey(group)) {
    openGroupKey.value = null
    openMode.value = null
    selection.value = []
    invalidPair.value = false
  }
  emit('version-ask-dismiss', { groupKey: getGroupKey(group) })
}

const toggleMode = (group, mode) => {
  const key = getGroupKey(group)
  if (openGroupKey.value === key && openMode.value === mode) {
    openGroupKey.value = null
    openMode.value = null
  } else {
    openGroupKey.value = key
    openMode.value = mode
  }
  selection.value = []
  invalidPair.value = false
}

const isPicked = (cand) => selection.value.some((item) => item.file_id === cand.file_id)

const togglePick = (cand) => {
  if (isPicked(cand)) {
    selection.value = selection.value.filter((item) => item.file_id !== cand.file_id)
  } else if (selection.value.length < 2) {
    selection.value = [...selection.value, cand]
  }
  invalidPair.value = false
}

const buildPayload = (group, action, candidates) => ({
  kb_id: group.kb_id,
  action,
  file_ids: candidates.map((cand) => cand.file_id),
  title: getGroupName(group),
  versions: candidates.map((cand) => ({
    file_id: cand.file_id,
    document_version: cand.document_version ?? null,
    filename: cand.filename || getGroupName(group),
    is_current: Boolean(cand.is_current)
  }))
})

const resetAsk = () => {
  openGroupKey.value = null
  openMode.value = null
  selection.value = []
  invalidPair.value = false
}

const runView = (group, cand) => {
  emit('version-ask-run', { mode: 'read', payload: buildPayload(group, 'read', [cand]) })
  resetAsk()
}

const runCompare = (group) => {
  const distinct = new Set(selection.value.map((cand) => cand.file_id))
  if (selection.value.length !== 2 || distinct.size !== 2) {
    invalidPair.value = true
    return
  }
  emit('version-ask-run', {
    mode: 'compare',
    payload: buildPayload(group, 'compare', selection.value)
  })
  resetAsk()
}
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
  .note-heading,
  .ask-guide {
    margin: 0;
  }

  .note-line + .note-line,
  .note-heading + .note-line {
    margin-top: 2px;
  }

  .note-guide {
    margin-top: 4px;
  }

  .ask-guide {
    margin-top: 6px;
    font-weight: 500;
  }

  .ask-block {
    margin-top: 6px;
  }

  .ask-content {
    padding: 6px 8px;
    border: 1px solid var(--main-100);
    border-radius: 6px;
    background: rgba(255, 255, 255, 0.5);
  }

  .ask-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 12px;
  }

  .ask-name {
    font-weight: 500;
    color: var(--main-800);
  }

  .ask-btn {
    padding: 0;
    border: none;
    background: none;
    color: var(--main-600);
    font-size: 12px;
    line-height: 1.6;
    cursor: pointer;

    &:hover,
    &.active {
      color: var(--main-800);
      text-decoration: underline;
    }

    &.quiet {
      color: var(--gray-500);
    }
  }

  .ask-panel {
    margin-top: 6px;
    border-top: 1px dashed var(--main-100);
    padding-top: 6px;
  }

  .ask-heading {
    margin: 0 0 4px;
    color: var(--main-700);
  }

  .ask-list {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .ask-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 3px 6px;
    margin: 2px 0;
    border-radius: 4px;
    color: var(--main-800);
    cursor: pointer;

    &:hover {
      background: var(--main-100);
    }

    &.current {
      font-weight: 500;
    }

    .ask-row-label {
      flex: 1;
      min-width: 0;
      word-break: break-all;
    }

    .ask-run {
      flex-shrink: 0;
      color: var(--main-600);
    }
  }

  .ask-check {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 13px;
    height: 13px;
    flex-shrink: 0;
    border: 1px solid var(--main-400);
    border-radius: 3px;
    color: #fff;
    background: transparent;

    &.checked {
      background: var(--main-600);
      border-color: var(--main-600);
    }
  }

  .ask-error {
    margin: 4px 0 0;
    color: var(--red-600, #d33);
  }

  .ask-run-btn {
    margin-top: 6px;
    padding: 3px 14px;
    border: 1px solid var(--main-600);
    border-radius: 4px;
    background: var(--main-600);
    color: #fff;
    font-size: 12px;
    cursor: pointer;

    &:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  }
}
</style>
