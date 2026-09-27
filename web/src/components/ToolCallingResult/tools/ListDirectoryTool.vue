<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="true">
    <template #header>
      <div class="sep-header">
        <span class="note">{{ toolCallName }}</span>
        <span class="separator" v-if="dirPath">|</span>
        <span class="description code">{{ dirPath }}</span>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import BaseToolCall from '../BaseToolCall.vue'
import { getToolCallId, getToolLabel } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const { t } = useI18n()

const toolCallName = computed(
  () => getToolLabel(getToolCallId(props.toolCall)) || t('toolCall.badge.unknown')
)

const parsedArgs = computed(() => {
  const args = props.toolCall.args || props.toolCall.function?.arguments
  if (!args) return {}
  if (typeof args === 'object') return args
  try {
    return JSON.parse(args)
  } catch {
    return {}
  }
})

const dirPath = computed(() => {
  return parsedArgs.value.dir_path || parsedArgs.value.path || ''
})
</script>

<style lang="less" scoped></style>
