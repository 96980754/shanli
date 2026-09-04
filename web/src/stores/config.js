import { ref } from 'vue'
import { defineStore } from 'pinia'
import { configApi } from '@/apis/system_api'

export const useConfigStore = defineStore('config', () => {
  const config = ref({})
  function setConfig(newConfig) {
    config.value = newConfig
  }

  function setConfigValue(key, value) {
    return setConfigValues({ [key]: value })
  }

  function setConfigValues(values) {
    Object.entries(values).forEach(([key, value]) => {
      config.value[key] = value
    })
    // 返回恒不 reject 的 Promise<{ ok }>：既有调用方忽略返回值（fire-and-forget）不受影响，
    // 需要反馈的调用方可 await 感知保存结果（如客服接入设置的「已保存/保存失败」状态）。
    return configApi
      .updateConfigBatch(values)
      .then((data) => {
        console.debug('Success:', data)
        setConfig(data)
        return { ok: true }
      })
      .catch((err) => {
        // 保存失败时收回乐观更新并重取服务端真值，避免界面与落盘配置静默脱钩（编辑/删除看似「全没了」）。
        console.warn('保存配置失败，已重取服务端配置:', err)
        return refreshConfig().then(() => ({ ok: false }), () => ({ ok: false }))
      })
  }

  async function refreshConfig() {
    const data = await configApi.getConfig()
    console.log('config', data)
    setConfig(data)
    return data
  }

  return { config, setConfigValue, setConfigValues, refreshConfig }
})
