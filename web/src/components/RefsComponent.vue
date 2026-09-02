<template>
  <div class="refs" v-if="showRefs">
    <!-- 问答侧多版本提示/澄清：命中多版本文档时在回答底部提示「基于当前版本」；版本意图命中时切换为澄清注记与对比引导 -->
    <AnswerVersionNotes
      v-if="showAnswerNotes"
      :chunks="knowledgeChunks"
      :query-text="queryText"
    />
    <div class="tags">
      <!-- 反馈 -->
      <span
        class="item btn"
        :class="{ disabled: feedbackState.hasSubmitted }"
        @click="likeThisResponse(msg)"
        :title="
          t(feedbackState.hasSubmitted && feedbackState.rating === 'like' ? 'refs.liked' : 'refs.like')
        "
      >
        <ThumbsUp size="12" :fill="feedbackState.rating === 'like' ? 'currentColor' : 'none'" />
      </span>
      <span
        class="item btn"
        :class="{ disabled: feedbackState.hasSubmitted }"
        @click="dislikeThisResponse(msg)"
        :title="
          t(
            feedbackState.hasSubmitted && feedbackState.rating === 'dislike' ? 'refs.disliked' : 'refs.dislike'
          )
        "
      >
        <ThumbsDown
          size="12"
          :fill="feedbackState.rating === 'dislike' ? 'currentColor' : 'none'"
        />
      </span>
      <!-- 模型名称 -->
      <span v-if="showKey('model') && getModelName(msg)" class="item" @click="console.log(msg)">
        <Bot size="12" /> {{ getModelName(msg) }}
      </span>
      <!-- 复制 -->
      <span v-if="showKey('copy')" class="item btn" @click="copyText(msg.content)" :title="t('refs.copy')">
        <Check v-if="isCopied" size="12" />
        <Copy v-else size="12" />
      </span>

      <!-- 重试 -->
      <span
        v-if="showKey('regenerate')"
        class="item btn"
        @click="regenerateMessage()"
        :title="t('refs.regenerate')"
        ><RotateCcw size="12" />
      </span>

      <!-- 来源按钮 - 使用 flex-grow 占据剩余空间并右对齐 -->
      <!-- 发生过知识检索即显示按钮（拒答时点开为空），纯聊天不显示 -->
      <div v-if="showSourceButton && showKey('sources')" class="sources-spacer"></div>
      <span
        v-if="showSourceButton && showKey('sources')"
        class="item btn sources-btn"
        :class="{ expanded: isSourcesExpanded }"
        @click="toggleSources"
        :title="t(isSourcesExpanded ? 'refs.collapseDetails' : 'refs.viewSourceDetails')"
      >
        <BookOpen size="12" />
        <span class="sources-label">
          {{ $t('refs.sources') }}
          <template v-if="sourceCount > 0">
            {{ sourceCount }}
          </template>
        </span>
        <ChevronDown :size="12" class="expand-icon" :class="{ rotated: isSourcesExpanded }" />
      </span>
    </div>

    <!-- 来源详情面板 -->
    <div v-if="isSourcesExpanded" class="sources-panel-body">
      <KnowledgeSourceSection v-if="knowledgeChunks.length > 0" :chunks="knowledgeChunks" />
      <WebSearchSourceSection v-if="webSources.length > 0" :sources="webSources" />
    </div>
  </div>

  <!-- Dislike reason modal -->
  <a-modal
    v-model:open="dislikeModalVisible"
    :title="t('refs.dislikeTitle')"
    @ok="submitDislikeFeedback"
    @cancel="cancelDislike"
    :confirmLoading="submittingFeedback"
    :okText="t('refs.submit')"
    :cancelText="t('common.cancel')"
  >
    <div class="dislike-form">
      <div class="reason-hint">{{ $t('refs.chooseReason') }}</div>
      <a-radio-group v-model:value="dislikeReasonCode" class="reason-options">
        <a-radio v-for="option in dislikeReasonOptions" :key="option.value" :value="option.value">
          {{ $t(option.label) }}
        </a-radio>
      </a-radio-group>

      <div class="reason-hint detail-hint">{{ $t('refs.detailOptional') }}</div>
      <a-textarea
        v-model:value="dislikeReasonDetail"
        :rows="4"
        :placeholder="t('refs.detailPlaceholder')"
        :maxlength="500"
        show-count
      />
    </div>
  </a-modal>
</template>

<script setup>
import { ref, computed, reactive, watch } from 'vue'
import { useClipboard } from '@vueuse/core'
import { message as antMessage } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import {
  ThumbsUp,
  ThumbsDown,
  Bot,
  Copy,
  Check,
  RotateCcw,
  BookOpen,
  ChevronDown
} from 'lucide-vue-next'
import { agentApi } from '@/apis'
import { MessageProcessor } from '@/utils/messageProcessor'
import KnowledgeSourceSection from '@/components/KnowledgeSourceSection.vue'
import WebSearchSourceSection from '@/components/WebSearchSourceSection.vue'
import AnswerVersionNotes from '@/components/AnswerVersionNotes.vue'

const emit = defineEmits(['retry', 'openRefs'])
const { t } = useI18n()
const props = defineProps({
  message: Object,
  showRefs: {
    type: [Array, Boolean],
    default: () => false
  },
  isLatestMessage: {
    type: Boolean,
    default: false
  },
  sources: {
    type: Object,
    default: () => ({})
  },
  // 问答侧多版本提示：由对话级（会话底部）调用方开启并传入该轮用户问题
  showAnswerNotes: {
    type: Boolean,
    default: false
  },
  queryText: {
    type: String,
    default: ''
  }
})

const msg = ref(props.message)

// Sources state
const isSourcesExpanded = ref(false)

// 展示全部知识库来源（已按相关度降序排好），不做条数截断
const knowledgeChunks = computed(() =>
  Array.isArray(props.sources?.knowledgeChunks) ? props.sources.knowledgeChunks : []
)
const webSources = computed(() =>
  Array.isArray(props.sources?.webSources) ? props.sources.webSources : []
)

const hasSources = computed(() => knowledgeChunks.value.length > 0 || webSources.value.length > 0)

// 发生过知识检索（含拒答轮次）即显示来源按钮；纯聊天无来源无活动则不显示
const showSourceButton = computed(
  () => hasSources.value || Boolean(props.sources?.knowledgeActivity)
)

// 「来源 N」按去重后的文档数计：同一文档在多个知识库命中时只算 1，与面板卡片数一致
const knowledgeDocCount = computed(
  () => MessageProcessor.groupKnowledgeChunksByDocument(knowledgeChunks.value).length
)
const sourceCount = computed(() => knowledgeDocCount.value + webSources.value.length)

const toggleSources = () => {
  isSourcesExpanded.value = !isSourcesExpanded.value
}

// Feedback state
const feedbackState = reactive({
  hasSubmitted: false,
  rating: null, // 'like' or 'dislike'
  reason: null
})

// 初始化反馈状态 - 从 antMessage.feedback 读取历史反馈
const initFeedbackState = () => {
  if (msg.value?.feedback) {
    feedbackState.hasSubmitted = true
    feedbackState.rating = msg.value.feedback.rating
    feedbackState.reason = msg.value.feedback.reason
  } else {
    feedbackState.hasSubmitted = false
    feedbackState.rating = null
    feedbackState.reason = null
  }
}

// 监听 message prop 变化 (用于切换对话时更新状态)
watch(
  () => props.message,
  () => {
    msg.value = props.message
    initFeedbackState()
  },
  { immediate: true }
)

// Modal state for dislike
const dislikeModalVisible = ref(false)
const dislikeReasonCode = ref(null)
const dislikeReasonDetail = ref('')
const submittingFeedback = ref(false)
const dislikeReasonOptions = [
  { value: 'answer_incorrect', label: 'refs.reasonAnswerIncorrect' },
  { value: 'outdated', label: 'refs.reasonOutdated' },
  { value: 'irrelevant', label: 'refs.reasonIrrelevant' },
  { value: 'other', label: 'refs.reasonOther' }
]

const selectedReasonLabel = computed(() => {
  const key = dislikeReasonOptions.find((item) => item.value === dislikeReasonCode.value)?.label
  return key ? t(key) : ''
})

const resetDislikeForm = () => {
  dislikeReasonCode.value = null
  dislikeReasonDetail.value = ''
}

const buildDislikeReason = () => {
  const label = selectedReasonLabel.value
  const detail = dislikeReasonDetail.value.trim()
  return detail ? `${label}\n${detail}` : label
}

// 使用 useClipboard 实现复制功能
const { copy, isSupported } = useClipboard()

const showKey = (key) => {
  if (props.showRefs === true) {
    return true
  }
  return props.showRefs.includes(key)
}

// 复制状态
const isCopied = ref(false)

// 定义 copy 方法
const copyText = async (text) => {
  if (isSupported) {
    try {
      await copy(text)
      antMessage.success(t('refs.copiedToClipboard'))
      isCopied.value = true
      setTimeout(() => {
        isCopied.value = false
      }, 2000)
    } catch (error) {
      console.error('复制失败:', error)
      antMessage.error(t('refs.copyFailedManual'))
    }
  } else {
    console.warn('浏览器不支持自动复制')
    antMessage.warning(t('refs.copyNotSupported'))
  }
}

const showRefs = computed(() => {
  // 如果只是为了显示模型信息，不需要检查状态
  if (props.showRefs && Array.isArray(props.showRefs) && props.showRefs.includes('model')) {
    return true
  }
  // 原有的逻辑
  return (
    (msg.value.role == 'received' || msg.value.role == 'assistant') &&
    msg.value.status == 'finished'
  )
})

// 添加重新生成方法
const regenerateMessage = () => {
  emit('retry')
}

// 获取模型名称
const getModelName = (msg) => {
  if (msg.response_metadata?.model_name) {
    return msg.response_metadata.model_name
  }
  return null
}
// Handle like action
const likeThisResponse = async (msg) => {
  if (feedbackState.hasSubmitted) {
    antMessage.info(t('refs.feedbackAlreadySubmitted'))
    return
  }

  if (!msg?.id) {
    antMessage.error(t('refs.feedbackNoMsgId'))
    console.error('Message object:', msg)
    return
  }

  try {
    submittingFeedback.value = true
    await agentApi.submitMessageFeedback(msg.id, 'like', null)

    feedbackState.hasSubmitted = true
    feedbackState.rating = 'like'

    antMessage.success(t('refs.feedbackThanks'))
  } catch (error) {
    console.error('Failed to submit like feedback:', error)
    if (error.message?.includes('already submitted')) {
      antMessage.info(t('refs.feedbackAlreadySubmitted'))
      feedbackState.hasSubmitted = true
    } else {
      antMessage.error(t('refs.feedbackSubmitFailed'))
    }
  } finally {
    submittingFeedback.value = false
  }
}

// Handle dislike action
const dislikeThisResponse = async (msg) => {
  if (feedbackState.hasSubmitted) {
    antMessage.info(t('refs.feedbackAlreadySubmitted'))
    return
  }

  if (!msg?.id) {
    antMessage.error(t('refs.feedbackNoMsgId'))
    console.error('Message object:', msg)
    return
  }

  resetDislikeForm()
  dislikeModalVisible.value = true
}

// Submit dislike feedback with structured reason
const submitDislikeFeedback = async () => {
  if (!dislikeReasonCode.value) {
    antMessage.warning(t('refs.chooseReasonFirst'))
    return
  }

  const reason = buildDislikeReason()

  try {
    submittingFeedback.value = true
    await agentApi.submitMessageFeedback(msg.value.id, 'dislike', reason)

    feedbackState.hasSubmitted = true
    feedbackState.rating = 'dislike'
    feedbackState.reason = reason

    dislikeModalVisible.value = false
    resetDislikeForm()

    antMessage.success(t('refs.feedbackThanks'))
  } catch (error) {
    console.error('Failed to submit dislike feedback:', error)
    if (error.message?.includes('already submitted')) {
      antMessage.info(t('refs.feedbackAlreadySubmitted'))
      feedbackState.hasSubmitted = true
      dislikeModalVisible.value = false
      resetDislikeForm()
    } else {
      antMessage.error(t('refs.feedbackSubmitFailed'))
    }
  } finally {
    submittingFeedback.value = false
  }
}

// Cancel dislike modal
const cancelDislike = () => {
  dislikeModalVisible.value = false
  resetDislikeForm()
}
</script>

<style lang="less" scoped>
.refs {
  display: flex;
  flex-direction: column;
  margin-bottom: 20px;
  margin-top: 10px;
  color: var(--gray-500);
  font-size: 13px;
  gap: 12px;

  .item {
    background: var(--gray-50);
    color: var(--gray-700);
    padding: 6px 8px;
    border-radius: 8px;
    font-size: 13px;
    user-select: none;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    line-height: 1;

    &.btn {
      cursor: pointer;
      &:hover {
        background: var(--gray-100);
      }
      &:active {
        background: var(--gray-200);
      }

      // Disabled state - when feedback has been submitted
      &.disabled {
        &:hover {
          background: var(--gray-50);
        }
      }
    }
  }

  .tags {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    width: 100%;

    .sources-spacer {
      flex-grow: 1;
    }

    .sources-btn {
      margin-left: auto;
      background: var(--gray-50);
      border: 1px solid transparent;
      padding: 6px 10px;

      &:hover {
        background: var(--gray-100);
      }

      &.expanded {
        background: var(--main-50);
        color: var(--main-700);
        border-color: var(--main-100);
      }

      .sources-label {
        font-weight: 500;
        margin-left: 2px;
      }

      .expand-icon {
        margin-left: 4px;
        transition: transform 0.2s ease;

        &.rotated {
          transform: rotate(180deg);
        }
      }
    }
  }

  .sources-panel-body {
    background: var(--gray-25);
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    animation: slideDown 0.2s ease-out;
  }
}

.dislike-form {
  display: flex;
  flex-direction: column;
  gap: 12px;

  .reason-hint {
    color: var(--gray-700);
    font-size: 13px;
    font-weight: 500;
  }

  .detail-hint {
    margin-top: 4px;
  }

  .reason-options {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 16px;
  }
}

@keyframes slideDown {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
