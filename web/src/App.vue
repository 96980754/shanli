<script setup>
import { computed } from 'vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import enUS from 'ant-design-vue/es/locale/en_US'
import { useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import { useThemeStore } from '@/stores/theme'
import { useLocaleStore } from '@/stores/locale'
import { onMounted } from 'vue'

const agentStore = useAgentStore()
const userStore = useUserStore()
const themeStore = useThemeStore()
const localeStore = useLocaleStore()

const antdLocale = computed(() => (localeStore.locale === 'en-US' ? enUS : zhCN))

onMounted(async () => {
  if (userStore.isLoggedIn) {
    await agentStore.initialize()
  }
})
</script>
<template>
  <a-config-provider :theme="themeStore.currentTheme" :locale="antdLocale">
    <router-view />
  </a-config-provider>
</template>
