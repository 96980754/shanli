<template>
  <div class="ai-textarea-wrapper" :class="`action-${actionPlacement}`">
    <a-textarea
      :value="modelValue"
      @update:value="$emit('update:modelValue', $event)"
      :placeholder="placeholder"
      :rows="rows"
      :auto-size="autoSize"
    />
    <a-tooltip v-if="name" :title="t('msgInput.aiTooltip')">
      <a-button
        class="ai-btn"
        type="text"
        size="small"
        :loading="loading"
        @click="generateDescription"
      >
        <template #icon>
          <WandSparkles size="14" />
        </template>
        <span v-if="!loading" class="ai-text">{{
          modelValue?.trim() ? $t('msgInput.polish') : $t('msgInput.generate')
        }}</span>
      </a-button>
    </a-tooltip>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import { databaseApi } from '@/apis/knowledge_api'
import { WandSparkles } from 'lucide-vue-next'

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  name: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: ''
  },
  rows: {
    type: Number,
    default: 4
  },
  autoSize: {
    type: [Boolean, Object],
    default: false
  },
  files: {
    type: Array,
    default: () => []
  },
  actionPlacement: {
    type: String,
    default: 'inside'
  }
})

const emit = defineEmits(['update:modelValue'])
const { t } = useI18n()

const loading = ref(false)

const generateDescription = async () => {
  if (!props.name?.trim()) {
    message.warning(t('msgInput.nameRequired'))
    return
  }

  loading.value = true
  try {
    const result = await databaseApi.generateDescription(props.name, props.modelValue, props.files)
    if (result.status === 'success' && result.description) {
      emit('update:modelValue', result.description)
      message.success(t('msgInput.descSuccess'))
    } else {
      message.error(result.message || t('msgInput.genFailed'))
    }
  } catch (error) {
    console.error('生成描述失败:', error)
    message.error(error.message || t('msgInput.genDescFailed'))
  } finally {
    loading.value = false
  }
}
</script>

<style lang="less" scoped>
.ai-textarea-wrapper {
  position: relative;

  .ai-btn {
    position: absolute;
    opacity: 0.9;
    top: 4px;
    right: 4px;
    z-index: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    min-width: 54px;
    padding: 2px 6px;
    height: 24px;
    color: var(--main-color);
    background: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 4px;
    font-size: 12px;
    transition: all 0.2s ease;

    &:hover {
      background: var(--main-10);
      border-color: var(--main-color);
    }

    .ai-text {
      font-weight: 500;
    }

    :deep(.ant-btn-loading-icon) {
      display: inline-flex;
      margin-inline-end: 0;
    }
  }

  &.action-header {
    .ai-btn {
      top: -31px;
      right: 0;
      height: 26px;
      min-width: 58px;
      padding: 0 9px;
      border-radius: 6px;
      background: var(--gray-0);
      border-color: var(--gray-150);
      color: var(--main-700);
      box-shadow: 0 1px 2px var(--shadow-1);

      &:hover {
        background: var(--gray-50);
        border-color: var(--gray-200);
        color: var(--gray-900);
      }
    }
  }
}
</style>
