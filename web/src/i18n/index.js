import { createI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import zhCN from './locales/zh-CN'
import enUS from './locales/en-US'

// 初始 locale 从 localStorage 读取，避免首帧闪回默认中文
const saved = typeof localStorage !== 'undefined' ? localStorage.getItem('locale') : null
const initialLocale = saved === 'en-US' ? 'en-US' : 'zh-CN'

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: initialLocale,
  fallbackLocale: 'zh-CN',
  messages: {
    'zh-CN': zhCN,
    'en-US': enUS
  }
})

// 应用语言偏好：同步 vue-i18n、<html lang> 与 dayjs locale
export function applyLocale(locale) {
  i18n.global.locale.value = locale
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('lang', locale)
  }
  dayjs.locale(locale === 'en-US' ? 'en' : 'zh-cn')
}

// 模块加载时同步一次（仿 theme.js 初始化 updateDocumentTheme 的模式）
applyLocale(initialLocale)
