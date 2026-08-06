import { createApp } from 'vue'
import { createPinia } from 'pinia'
import piniaPluginPersistedstate from 'pinia-plugin-persistedstate'

import App from './App.vue'
import router from './router'

import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import '@/assets/css/main.css'
import { i18n } from '@/i18n'

const app = createApp(App)
const pinia = createPinia()
pinia.use(piniaPluginPersistedstate)

app.use(pinia)
app.use(router)
app.use(Antd)
app.use(i18n)

// 预加载信息配置
import { useInfoStore } from '@/stores/info'
const infoStore = useInfoStore()
infoStore.loadInfoConfig()

app.mount('#app')
