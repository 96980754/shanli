<template>
  <button
    class="input-action-btn"
    type="button"
    :class="{ disabled, active }"
    :disabled="disabled"
    title="开启/关闭 word产品方案模式：开启后直接输入需求发送即可"
    @click="$emit('toggle')"
  >
    <FileText :size="16" />
    <span class="hide-text">word产品方案</span>
  </button>
</template>

<script setup>
import { FileText } from 'lucide-vue-next'

defineProps({
  disabled: { type: Boolean, default: false },
  active: { type: Boolean, default: false }
})
defineEmits(['toggle'])
</script>
