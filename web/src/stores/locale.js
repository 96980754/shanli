import { ref } from 'vue'
import { defineStore } from 'pinia'
import { applyLocale } from '@/i18n'

export const useLocaleStore = defineStore(
  'locale',
  () => {
    const saved = typeof localStorage !== 'undefined' ? localStorage.getItem('locale') : null
    const locale = ref(saved === 'en-US' ? 'en-US' : 'zh-CN')

    // 初始化时同步 <html lang> 与 dayjs locale
    applyLocale(locale.value)

    function setLocale(next) {
      const normalized = next === 'en-US' ? 'en-US' : 'zh-CN'
      locale.value = normalized
      applyLocale(normalized)
    }

    function toggleLocale() {
      setLocale(locale.value === 'en-US' ? 'zh-CN' : 'en-US')
    }

    return { locale, setLocale, toggleLocale }
  },
  {
    persist: {
      key: 'locale',
      storage: localStorage,
      pick: ['locale']
    }
  }
)
