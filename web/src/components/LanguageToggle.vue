<template>
  <a-tooltip :title="$t('common.switchLanguage')" placement="right">
    <a-dropdown :trigger="['click']" placement="topRight">
      <a-button type="text" class="lang-toggle-btn" aria-label="Switch language">
        <template #icon><Languages :size="18" /></template>
      </a-button>
      <template #overlay>
        <a-menu :selected-keys="[localeStore.locale]" @click="handleSelect">
          <a-menu-item key="zh-CN">中文</a-menu-item>
          <a-menu-item key="en-US">English</a-menu-item>
        </a-menu>
      </template>
    </a-dropdown>
  </a-tooltip>
</template>

<script setup>
import { useLocaleStore } from '@/stores/locale'
import { Languages } from 'lucide-vue-next'

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
  transition: all 0.3s ease;
}

.lang-toggle-btn:hover {
  transform: rotate(10deg);
}
</style>
