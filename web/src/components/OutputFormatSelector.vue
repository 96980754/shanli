<template>
  <a-dropdown trigger="click" :open="dropdownOpen" :disabled="props.disabled" @open-change="handleOpenChange">
    <div class="model-select format-select" :class="formatSelectClasses" @click.prevent.stop @mousedown.stop>
      <div class="model-select-content">
        <component :is="currentIcon" :size="14" class="format-icon" />
        <span class="model-text text">{{ currentLabel }}</span>
      </div>
    </div>
    <template #overlay>
      <a-menu class="format-dropdown">
        <a-menu-item v-for="option in formatOptions" :key="option.value" @click="handleSelect(option.value)">
          <span class="format-option">
            <component :is="option.icon" :size="14" />
            <span>{{ option.label }}</span>
          </span>
        </a-menu-item>
      </a-menu>
    </template>
  </a-dropdown>
</template>

<script setup>
import { computed, ref } from 'vue'
import { AlignLeft, List, ListOrdered, Table } from 'lucide-vue-next'

const props = defineProps({
  value: {
    type: String,
    default: 'default'
  },
  disabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['select-format'])

const formatOptions = [
  { value: 'default', label: '格式·默认', icon: AlignLeft },
  { value: 'table', label: '格式·表格', icon: Table },
  { value: 'steps', label: '格式·步骤', icon: ListOrdered },
  { value: 'list', label: '格式·列表', icon: List }
]

const dropdownOpen = ref(false)

const currentOption = computed(() => formatOptions.find((option) => option.value === props.value) || formatOptions[0])
const currentLabel = computed(() => currentOption.value.label)
const currentIcon = computed(() => currentOption.value.icon)

const formatSelectClasses = computed(() => ({
  'model-select--nano': true,
  'model-select--disabled': props.disabled
}))

const handleOpenChange = (open) => {
  if (props.disabled) {
    dropdownOpen.value = false
    return
  }
  dropdownOpen.value = open
}

const handleSelect = (value) => {
  if (props.disabled) return
  emit('select-format', value)
  dropdownOpen.value = false
}
</script>

<style lang="less" scoped>
@import '@/assets/css/model-selector-common.less';

.format-select {
  &.model-select--nano {
    height: 30px;
    padding: 6px 8px;
    border: none;
    border-radius: 8px;
    font-size: 13px;
    line-height: 1;
    color: var(--gray-600);
    background: transparent;
    transition: all 0.2s ease;
    user-select: none;
    max-width: 100%;

    &:hover {
      color: var(--gray-900);
      background: var(--gray-50);
    }
  }

  &.model-select--disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }
}

.format-icon {
  color: currentColor;
  flex-shrink: 0;
}

.format-dropdown {
  min-width: 132px;
  border-radius: 8px;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.08);
}

.format-option {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
</style>
