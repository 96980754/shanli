<template>
  <div
    class="extension-card-grid"
    :style="{ gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))` }"
  >
    <slot />
    <div v-if="!$slots.default && items.length === 0" class="extension-card-grid-empty">
      <a-empty :image="false" :description="resolvedEmptyText" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const props = defineProps({
  items: { type: Array, default: () => [] },
  emptyText: { type: String, default: '' },
  minWidth: { type: Number, default: 280 }
})
const resolvedEmptyText = computed(() => props.emptyText || t('common.noData'))
</script>

<style lang="less" scoped>
.extension-card-grid {
  display: grid;
  gap: 16px;
  padding: 16px var(--page-padding);

  &-empty {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 60px 0;
  }
}
</style>
