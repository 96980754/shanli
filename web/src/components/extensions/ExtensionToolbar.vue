<template>
  <div class="extension-toolbar">
    <div class="extension-toolbar-left">
      <a-input
        v-model:value="searchModel"
        :placeholder="resolvedPlaceholder"
        allow-clear
        class="extension-search-input"
      >
        <template #prefix><Search :size="14" class="text-muted" /></template>
      </a-input>
      <slot name="filters" />
    </div>
    <div class="extension-toolbar-right">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Search } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const searchModel = defineModel('search', { type: String, default: '' })

const props = defineProps({
  searchPlaceholder: { type: String, default: '' }
})
const resolvedPlaceholder = computed(() => props.searchPlaceholder || t('tools.searchPlaceholder'))
</script>

<style lang="less" scoped>
.extension-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px var(--page-padding) 0;

  &-left {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  &-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}

.extension-search-input {
  width: 280px;

  :deep(.ant-input-affix-wrapper) {
    height: 32px;
    padding: 0 10px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background-color: var(--gray-0);

    &:hover,
    &:focus,
    &.ant-input-affix-wrapper-focused {
      border-color: var(--gray-200);
      box-shadow: none;
    }
  }

  :deep(.ant-input-prefix) {
    margin-right: 8px;
    color: var(--gray-400);
  }

  :deep(.ant-input) {
    height: 100%;
    background-color: transparent;
  }
}
</style>
