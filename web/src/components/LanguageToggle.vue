<template>
  <a-tooltip
    :title="$t('common.switchLanguage')"
    placement="right"
    :open="showLabel ? false : undefined"
  >
    <a-dropdown :trigger="['click']" placement="topRight">
      <a-button class="lang-toggle-btn" :class="{ 'is-icon-only': !showLabel }">
        <template #icon><Languages :size="16" /></template>
        <!-- 按钮标签固定为英文 languages，不随界面语言翻译 -->
        <span v-if="showLabel" class="lang-toggle-label">languages</span>
      </a-button>
      <template #overlay>
        <a-menu :selected-keys="[localeStore.locale]" @click="handleSelect">
          <a-menu-item v-for="item in LANGUAGES" :key="item.key">{{ item.label }}</a-menu-item>
        </a-menu>
      </template>
    </a-dropdown>
  </a-tooltip>
</template>

<script setup>
import { useLocaleStore } from '@/stores/locale'
import { Languages } from 'lucide-vue-next'

// 语言名按惯例用各自的本族语言显示，不随界面语言翻译
const LANGUAGES = [
  { key: 'zh-CN', label: '中文' }, // i18n-ignore
  { key: 'en-US', label: 'English' }
]

defineProps({
  // 侧栏收起时只留图标，与 UserInfoComponent 的 show-role 同一套用法
  showLabel: { type: Boolean, default: true }
})

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
  width: 100%;
  color: var(--gray-700);
  background: var(--gray-50);
  border: 1px solid var(--gray-200);
}

/* 侧栏底色是 near-white 的 --main-5，纯白按钮会糊在上面，故给一层浅灰底把按钮托出来。
   antd 默认按钮 hover 会换成主色描边 + 主色文字（.ant-btn-default:not(:disabled):hover，
   样式哈希用 :where() 包裹不占权重），这里带上变体类把 hover 压回中性色。 */
.lang-toggle-btn.ant-btn-default:not(:disabled):hover {
  color: var(--gray-700);
  background: var(--gray-100);
  border-color: var(--gray-200);
}

.lang-toggle-btn.is-icon-only {
  width: auto;
}

.lang-toggle-label {
  font-size: 13px;
}
</style>
