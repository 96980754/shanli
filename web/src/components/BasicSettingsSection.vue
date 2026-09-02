<template>
  <div class="basic-settings-section">
    <template v-if="userStore.isAdmin">
      <div class="section-title">{{ $t('settings.defaultsTitle') }}</div>
      <div class="settings-panel">
        <template v-if="userStore.isSuperAdmin">
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">
                {{ items?.default_model?.des || $t('settings.defaultChatModel') }}
              </div>
              <div class="setting-content">
                <ModelSelectorComponent
                  @select-model="handleChatModelSelect"
                  :model_spec="configStore.config?.default_model"
                  :placeholder="$t('settings.selectDefaultModel')"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">{{ items?.fast_model?.des }}</div>
              <div class="setting-content">
                <ModelSelectorComponent
                  @select-model="handleFastModelSelect"
                  :model_spec="configStore.config?.fast_model"
                  :placeholder="$t('settings.selectModel')"
                />
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">{{ items?.embed_model?.des }}</div>
              <div class="setting-content">
                <EmbeddingModelSelector
                  :value="configStore.config?.embed_model"
                  @change="handleChange('embed_model', $event)"
                  style="width: 100%"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">{{ items?.reranker?.des }}</div>
              <div class="setting-content">
                <RerankModelSelector
                  :value="configStore.config?.reranker"
                  @change="handleChange('reranker', $event)"
                  style="width: 100%"
                />
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">
                {{ items?.default_ocr_engine?.des || $t('settings.defaultOcrEngine') }}
              </div>
              <div class="setting-content">
                <a-select
                  :value="configStore.config?.default_ocr_engine || 'rapid_ocr'"
                  @change="handleChange('default_ocr_engine', $event)"
                  class="full-width"
                >
                  <a-select-option
                    v-for="option in ocrEngineOptions"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ $t(option.label) }}
                  </a-select-option>
                </a-select>
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">
                {{ items?.transcription_model?.des || $t('settings.defaultTranscriptionModel') }}
              </div>
              <div class="setting-content">
                <ModelSelectorComponent
                  :model_spec="configStore.config?.transcription_model"
                  model-type="transcription"
                  :show-status="false"
                  clearable
                  :placeholder="$t('settings.selectTranscriptionModel')"
                  @select-model="handleTranscriptionModelSelect"
                />
              </div>
            </div>
          </div>
        </template>
      </div>

      <template v-if="userStore.isSuperAdmin">
        <div class="section-title">{{ $t('settings.contentGuardTitle') }}</div>
        <div class="section">
          <div class="card">
            <span class="label">{{ items?.enable_content_guard?.des }}</span>
            <a-switch
              :checked="configStore.config?.enable_content_guard"
              @change="handleChange('enable_content_guard', $event)"
            />
          </div>
          <div class="card" v-if="configStore.config?.enable_content_guard">
            <span class="label">{{ items?.enable_content_guard_llm?.des }}</span>
            <a-switch
              :checked="configStore.config?.enable_content_guard_llm"
              @change="handleChange('enable_content_guard_llm', $event)"
            />
          </div>
          <div
            class="card card-select"
            v-if="
              configStore.config?.enable_content_guard &&
              configStore.config?.enable_content_guard_llm
            "
          >
            <span class="label">{{ items?.content_guard_llm_model?.des }}</span>
            <ModelSelectorComponent
              @select-model="handleContentGuardModelSelect"
              :model_spec="configStore.config?.content_guard_llm_model"
              :placeholder="$t('settings.selectModel')"
            />
          </div>
        </div>
      </template>

      <!-- 企微客服（拒答转人工） -->
      <div class="section-title">{{ $t('settings.wecomTitle') }}</div>
      <div class="section wecom-section">
        <p class="section-description">{{ $t('settings.wecomDesc') }}</p>
        <div class="setting-row">
          <div class="col-item">
            <div class="setting-label">{{ $t('settings.wecomGlobalUrl') }}</div>
            <a-input
              v-model:value="globalWecomUrl"
              :placeholder="$t('settings.wecomGlobalUrlPlaceholder')"
              @change="wecomDirty = true"
              @blur="saveGlobalWecomUrl"
            />
          </div>
        </div>
        <div class="setting-label wecom-domain-title">{{ $t('settings.wecomDomainUrls') }}</div>
        <div v-for="(row, index) in wecomRows" :key="index" class="wecom-row">
          <a-input
            v-model:value="row.domain"
            :placeholder="$t('settings.wecomDomainKeyPlaceholder')"
            class="wecom-domain-key"
            @change="wecomDirty = true"
          />
          <a-input
            v-model:value="row.url"
            :placeholder="$t('settings.wecomDomainUrlPlaceholder')"
            class="wecom-domain-url"
            @change="wecomDirty = true"
          />
          <a-button type="text" class="wecom-remove-btn" @click="removeWecomRow(index)">
            {{ $t('settings.wecomDelete') }}
          </a-button>
        </div>
        <div class="wecom-actions">
          <a-button size="small" @click="addWecomRow">{{ $t('settings.wecomAddDomain') }}</a-button>
          <a-button size="small" type="primary" @click="saveWecomConfig">
            {{ $t('settings.wecomSave') }}
          </a-button>
        </div>
      </div>
    </template>

    <!-- 服务链接部分 -->
    <div v-if="userStore.isAdmin" class="section-title">{{ $t('settings.serviceLinks') }}</div>
    <div v-if="userStore.isAdmin">
      <p class="section-description">{{ $t('settings.serviceLinksDesc') }}</p>
      <div class="services-grid">
        <div class="service-link-card">
          <div class="service-info">
            <h4>{{ $t('settings.neo4jBrowser') }}</h4>
            <p>{{ $t('settings.neo4jBrowserDesc') }}</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:7474/')"
            :icon="h(Globe, { size: 18 })"
          >
            {{ $t('settings.visit') }}
          </a-button>
        </div>

        <div class="service-link-card">
          <div class="service-info">
            <h4>{{ $t('settings.apiDocs') }}</h4>
            <p>{{ $t('settings.apiDocsDesc') }}</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:5050/docs')"
            :icon="h(Globe, { size: 18 })"
          >
            {{ $t('settings.visit') }}
          </a-button>
        </div>

        <div class="service-link-card">
          <div class="service-info">
            <h4>{{ $t('settings.minioStorage') }}</h4>
            <p>{{ $t('settings.minioStorageDesc') }}</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:9001')"
            :icon="h(Globe, { size: 18 })"
          >
            {{ $t('settings.visit') }}
          </a-button>
        </div>

        <div class="service-link-card">
          <div class="service-info">
            <h4>Milvus WebUI</h4>
            <p>{{ $t('settings.milvusWebUIDesc') }}</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:9091/webui/')"
            :icon="h(Globe, { size: 18 })"
          >
            {{ $t('settings.visit') }}
          </a-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, h, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useUserStore } from '@/stores/user'
import { Globe } from 'lucide-vue-next'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import EmbeddingModelSelector from '@/components/EmbeddingModelSelector.vue'
import RerankModelSelector from '@/components/RerankModelSelector.vue'

const { t } = useI18n()

const configStore = useConfigStore()
const userStore = useUserStore()
const items = computed(() => configStore.config?._config_items || {})
const ocrEngineOptions = [
  { value: 'disable', label: 'settings.ocrDisabled' },
  { value: 'rapid_ocr', label: 'RapidOCR (ONNX)' },
  { value: 'mineru_ocr', label: 'MinerU OCR' },
  { value: 'mineru_official', label: 'MinerU Official API' },
  { value: 'pp_structure_v3_ocr', label: 'PP-Structure-V3' },
  { value: 'deepseek_ocr', label: 'DeepSeek OCR' },
  { value: 'paddleocr_vl_1_6', label: 'PaddleOCR-VL-1.6' },
  { value: 'paddleocr_pp_ocrv6', label: 'PP-OCRv6' }
]

const handleChange = (key, e) => {
  configStore.setConfigValue(key, e)
}

const handleChatModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('default_model', spec)
  }
}

const handleFastModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('fast_model', spec)
  }
}

const handleTranscriptionModelSelect = (spec) => {
  configStore.setConfigValue('transcription_model', spec || null)
}

const handleContentGuardModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('content_guard_llm_model', spec)
  }
}

// ---- 企微客服（拒答转人工）配置 ----
const isHttpsUrl = (url) => {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:'
  } catch {
    return false
  }
}

const wecomDirty = ref(false)
const globalWecomUrl = ref('')
const wecomRows = ref([])

// 配置异步加载时回填一次；用户在编辑中（wecomDirty）不覆盖。
watch(
  () => configStore.config?.wecom_customer_service_urls,
  (urls) => {
    if (wecomDirty.value) return
    wecomRows.value = Object.entries(urls || {}).map(([domain, url]) => ({ domain, url }))
    globalWecomUrl.value = configStore.config?.wecom_customer_service_url || ''
  },
  { immediate: true, deep: true }
)

const addWecomRow = () => {
  wecomRows.value.push({ domain: '', url: '' })
  wecomDirty.value = true
}

const removeWecomRow = (index) => {
  wecomRows.value.splice(index, 1)
  wecomDirty.value = true
}

const saveGlobalWecomUrl = () => {
  const url = (globalWecomUrl.value || '').trim()
  if (url && !isHttpsUrl(url)) {
    message.error(t('settings.wecomInvalidUrl'))
    return
  }
  wecomDirty.value = false
  configStore.setConfigValue('wecom_customer_service_url', url)
}

const saveWecomConfig = () => {
  const urls = {}
  for (const row of wecomRows.value) {
    const domain = (row.domain || '').trim()
    const url = (row.url || '').trim()
    if (!domain) continue
    if (!url) continue
    if (!isHttpsUrl(url)) {
      message.error(t('settings.wecomInvalidUrl'))
      return
    }
    urls[domain] = url
  }
  wecomDirty.value = false
  configStore.setConfigValue('wecom_customer_service_urls', urls)
  message.success(t('settings.wecomSaved'))
}

const openLink = (url) => {
  window.open(url, '_blank')
}
</script>

<style lang="less" scoped>
.basic-settings-section {
  .section {
    background-color: var(--gray-0);
    padding: 10px 16px;
    border-radius: 8px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    border: 1px solid var(--gray-150);
  }

  .settings-panel {
    background-color: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .setting-row {
    display: flex;
    flex-direction: column;
    gap: 8px;

    &.two-cols {
      flex-direction: row;
      gap: 20px;
    }

    .col-item {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
    }
  }

  .setting-label {
    font-size: 13px;
    font-weight: 500;
    color: var(--gray-700);
  }

  .setting-content {
    width: 100%;

    .full-width {
      width: 100%;
    }
  }

  .card {
    display: flex;
    align-items: center;
    justify-content: space-between;

    .label {
      margin-right: 20px;
      font-weight: 500;
      color: var(--gray-800);
      flex-shrink: 0;
      min-width: 140px;
    }

    &.card-select {
      align-items: flex-start;
      gap: 12px;

      .label {
        margin-right: 0;
        margin-top: 6px;
      }
    }
  }

  .agent-select {
    width: 320px;
    max-width: 100%;
  }

  .wecom-section {
    .wecom-domain-title {
      margin-top: 8px;
    }

    .wecom-row {
      display: flex;
      gap: 8px;
      align-items: center;
      margin-top: 8px;

      .wecom-domain-key {
        width: 200px;
        max-width: 30%;
      }

      .wecom-domain-url {
        flex: 1;
        min-width: 0;
      }
    }

    .wecom-actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 12px;
    }
  }

  .services-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 12px;
    margin-top: 16px;
  }

  .service-link-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 16px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    transition: all 0.2s;
    min-height: 70px;

    &:hover {
      box-shadow: 0 1px 8px var(--gray-150);
      border-color: var(--gray-100);
    }

    .service-info {
      flex: 1;
      margin-right: 16px;

      h4 {
        margin: 0 0 4px 0;
        color: var(--gray-900);
        font-size: 15px;
        font-weight: 500;
      }

      p {
        margin: 0;
        color: var(--gray-600);
        font-size: 13px;
        line-height: 1.4;
      }
    }
  }

  @media (max-width: 768px) {
    .agent-select {
      width: 100%;
    }
  }
}
</style>
