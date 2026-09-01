<template>
  <button
    class="input-action-btn"
    type="button"
    :class="{ disabled, active }"
    :disabled="disabled"
    :title="t('industry.modeTooltip')"
    @click="$emit('toggle')"
  >
    <FileText :size="16" />
    <span class="hide-text">{{ $t('industry.modeLabel') }}</span>
  </button>
</template>

<script setup>
import { FileText } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps({
  disabled: { type: Boolean, default: false },
  active: { type: Boolean, default: false }
})
defineEmits(['toggle'])
</script>
