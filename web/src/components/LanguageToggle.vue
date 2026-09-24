<template>
  <a-dropdown :trigger="['click']" placement="bottomRight">
    <a-button class="lang-toggle-btn">
      <template #icon><Languages :size="16" /></template>
      <span>{{ $t('common.switchLanguage') }}</span>
    </a-button>
    <template #overlay>
      <a-menu :selected-keys="[localeStore.locale]" @click="handleSelect">
        <a-menu-item v-for="item in LANGUAGES" :key="item.key">{{ item.label }}</a-menu-item>
      </a-menu>
    </template>
  </a-dropdown>
</template>

<script setup>
import { useLocaleStore } from '@/stores/locale'
import { Languages } from 'lucide-vue-next'

// 语言名按惯例用各自的本族语言显示，不随界面语言翻译
const LANGUAGES = [
  { key: 'zh-CN', label: '中文' }, // i18n-ignore
  { key: 'en-US', label: 'English' }
]

const localeStore = useLocaleStore()

const handleSelect = ({ key }) => {
  localeStore.setLocale(key)
}
</script>

<style scoped>
.lang-toggle-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--gray-700);
  background: var(--gray-50);
  border: 1px solid var(--gray-200);
}

/* antd 默认按钮 hover 会换成主色描边 + 主色文字（.ant-btn-default:not(:disabled):hover，
   样式哈希用 :where() 包裹不占权重），这里带上变体类把 hover 压回中性色。 */
.lang-toggle-btn.ant-btn-default:not(:disabled):hover {
  color: var(--gray-700);
  background: var(--gray-100);
  border-color: var(--gray-200);
}
</style>
