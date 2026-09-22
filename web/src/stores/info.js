import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { brandApi } from '@/apis/system_api'
import { useLocaleStore } from '@/stores/locale'

// 品牌文案里需要双语的字段：在 info.template.yaml 中以 `<字段>_en` 声明英文值。
// 英文界面下用它覆盖基准字段，未配置 `_en` 时回退中文——品牌配置来自静态
// yaml 而非语言包，所以英文只能由配置方提供，这里只负责按语言挑选。
const LOCALIZED_TEXT_KEYS = {
  organization: ['name'],
  branding: ['name', 'title', 'subtitle', 'subtitles'],
  footer: ['copyright']
}

function withEnglishText(section, keys) {
  if (!section) return section
  const localized = { ...section }
  for (const key of keys) {
    const english = section[`${key}_en`]
    if (Array.isArray(english) ? english.length > 0 : english) {
      localized[key] = english
    }
  }
  return localized
}

export const useInfoStore = defineStore('info', () => {
  const localeStore = useLocaleStore()

  // 状态
  const infoConfig = ref({})
  const isLoading = ref(false)
  const isLoaded = ref(false)
  const debugMode = ref(false)

  // 按当前语言挑好文案的配置，下面三个计算属性都从它取值
  const localizedInfo = computed(() => {
    const config = infoConfig.value || {}
    if (localeStore.locale !== 'en-US') return config
    return {
      ...config,
      organization: withEnglishText(config.organization, LOCALIZED_TEXT_KEYS.organization),
      branding: withEnglishText(config.branding, LOCALIZED_TEXT_KEYS.branding),
      footer: withEnglishText(config.footer, LOCALIZED_TEXT_KEYS.footer)
    }
  })

  // 计算属性 - 组织信息
  const organization = computed(
    () =>
      localizedInfo.value.organization || {
        name: '',
        logo: '',
        avatar: ''
      }
  )

  // 计算属性 - 品牌信息
  const branding = computed(
    () =>
      localizedInfo.value.branding || {
        name: '',
        title: '',
        subtitle: '',
        subtitles: []
      }
  )

  // 计算属性 - 页脚信息
  const footer = computed(() => ({
    copyright: '',
    user_agreement_url: '',
    privacy_policy_url: '',
    ...(localizedInfo.value.footer || {})
  }))

  // 动作方法
  function setInfoConfig(newConfig) {
    infoConfig.value = newConfig
    isLoaded.value = true
  }

  function toggleDebugMode() {
    debugMode.value = !debugMode.value
  }

  async function loadInfoConfig(force = false) {
    // 如果已经加载过且不强制刷新，则不重新加载
    if (isLoaded.value && !force) {
      return infoConfig.value
    }

    try {
      isLoading.value = true
      const response = await brandApi.getInfoConfig()

      if (response.success && response.data) {
        setInfoConfig(response.data)
        console.debug('信息配置加载成功:', response.data) // i18n-ignore
        return response.data
      } else {
        console.warn('信息配置加载失败，使用默认配置') // i18n-ignore
        return null
      }
    } catch (error) {
      console.error('加载信息配置时发生错误:', error) // i18n-ignore
      return null
    } finally {
      isLoading.value = false
    }
  }

  return {
    // 状态
    infoConfig,
    isLoading,
    isLoaded,
    debugMode,

    // 计算属性
    organization,
    branding,
    footer,

    // 方法
    toggleDebugMode,
    loadInfoConfig
  }
})
