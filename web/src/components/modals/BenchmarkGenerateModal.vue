<template>
  <a-modal
    v-model:open="visible"
    :title="t('benchmark.generateModalTitle')"
    width="600px"
    :mask-closable="!generating"
    :closable="!generating"
    @cancel="handleCancel"
  >
    <a-form ref="formRef" :model="formState" :rules="rules" layout="vertical">
      <a-form-item :label="t('benchmark.benchmarkNameLabel')" name="name">
        <a-input v-model:value="formState.name" :placeholder="t('benchmark.namePlaceholder')" />
      </a-form-item>

      <a-form-item :label="t('benchmark.descriptionLabel')" name="description">
        <a-textarea
          v-model:value="formState.description"
          :placeholder="t('benchmark.descriptionPlaceholder')"
          :rows="3"
        />
      </a-form-item>

      <a-form-item :label="t('benchmark.generationModeLabel')" name="generation_mode">
        <div class="generation-mode-cards" role="radiogroup" :aria-label="t('benchmark.generationModeLabel')">
          <div
            v-for="option in generationModeOptions"
            :key="option.value"
            class="generation-mode-card"
            :class="{
              active: formState.generation_mode === option.value,
              disabled: option.disabled
            }"
            role="radio"
            :aria-checked="formState.generation_mode === option.value"
            :aria-disabled="option.disabled"
            :tabindex="option.disabled ? -1 : 0"
            @click="selectGenerationMode(option)"
            @keydown.enter.prevent="selectGenerationMode(option)"
            @keydown.space.prevent="selectGenerationMode(option)"
          >
            <div class="card-header">
              <component :is="option.icon" class="mode-icon" />
              <span class="mode-title">{{ option.label }}</span>
              <a-tag v-if="option.tag" class="mode-tag" size="small">{{ option.tag }}</a-tag>
            </div>
            <div class="card-description">{{ option.description }}</div>
            <div v-if="option.helper" class="card-helper" :class="{ warning: option.disabled }">
              {{ option.helper }}
            </div>
          </div>
        </div>
      </a-form-item>

      <a-form-item
        :label="t('benchmark.llmModelConfigLabel')"
        name="llm_model_spec"
        :rules="[{ required: true, message: t('benchmark.selectLlmModel') }]"
      >
        <ModelSelectorComponent
          :model_spec="formState.llm_model_spec"
          :placeholder="t('benchmark.selectLlmModelPlaceholder')"
          @select-model="handleSelectLLMModel"
        />
      </a-form-item>

      <a-form-item :label="t('benchmark.generationParamsLabel')" name="params">
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item
              :label="t('benchmark.questionQuantityLabel')"
              name="count"
              :labelCol="{ span: 24 }"
              :wrapperCol="{ span: 24 }"
            >
              <a-input-number
                v-model:value="formState.count"
                :min="1"
                :max="100"
                style="width: 100%"
                :placeholder="t('benchmark.questionQuantityPlaceholder')"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item name="neighbors_count" :labelCol="{ span: 24 }" :wrapperCol="{ span: 24 }">
              <template #label>
                <span class="field-label-with-help">
                  {{ $t('benchmark.candidateChunkCountLabel') }}
                  <a-tooltip :title="t('benchmark.candidateChunkTooltip')">
                    <CircleHelp class="help-icon" />
                  </a-tooltip>
                </span>
              </template>
              <a-input-number
                v-model:value="formState.neighbors_count"
                :min="0"
                :max="10"
                style="width: 100%"
                :placeholder="t('benchmark.defaultPlaceholder', { value: 1 })"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item
              name="concurrency_count"
              :labelCol="{ span: 24 }"
              :wrapperCol="{ span: 24 }"
            >
              <template #label>
                <span class="field-label-with-help">
                  {{ $t('benchmark.buildConcurrencyLabel') }}
                  <a-tooltip :title="t('benchmark.buildConcurrencyTooltip')">
                    <CircleHelp class="help-icon" />
                  </a-tooltip>
                </span>
              </template>
              <a-input-number
                v-model:value="formState.concurrency_count"
                :min="1"
                :max="20"
                style="width: 100%"
                :placeholder="t('benchmark.defaultPlaceholder', { value: 10 })"
              />
            </a-form-item>
          </a-col>
          <a-col v-if="formState.generation_mode === 'graph_enhanced'" :span="12">
            <a-form-item
              name="graph_expand_top_k"
              :labelCol="{ span: 24 }"
              :wrapperCol="{ span: 24 }"
            >
              <template #label>
                <span class="field-label-with-help">
                  {{ $t('benchmark.expandChunkPerRoundLabel') }}
                  <a-tooltip :title="t('benchmark.expandChunkTooltip')">
                    <CircleHelp class="help-icon" />
                  </a-tooltip>
                </span>
              </template>
              <a-input-number
                v-model:value="formState.graph_expand_top_k"
                :min="1"
                :max="3"
                style="width: 100%"
                :placeholder="t('benchmark.defaultPlaceholder', { value: 1 })"
              />
            </a-form-item>
          </a-col>
        </a-row>
      </a-form-item>
    </a-form>
    <template #footer>
      <div class="benchmark-modal-footer">
        <div class="footer-actions">
          <a-button :disabled="generating" @click="handleCancel">{{ $t('common.cancel') }}</a-button>
          <a-button
            type="primary"
            :loading="generating"
            :disabled="generating"
            @click="handleGenerate"
          >
            {{ $t('common.ok') }}
          </a-button>
        </div>
      </div>
    </template>
  </a-modal>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { message } from 'ant-design-vue'
import { CircleHelp, Database, Network } from 'lucide-vue-next'
import { evaluationApi, graphBuildApi } from '@/apis/knowledge_api'
import { useConfigStore } from '@/stores/config'
import { useI18n } from 'vue-i18n'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'

const { t } = useI18n()
const configStore = useConfigStore()

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  kbId: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['update:visible', 'success'])

// 默认基准名称
const defaultBenchmarkName = () => {
  const today = new Date().toISOString().slice(0, 10)
  const suffix = Array.from(
    { length: 4 },
    () => '0123456789abcdefghijklmnopqrstuvwxyz'[Math.floor(Math.random() * 36)]
  ).join('')
  return `Test-${today}-${suffix}`
}

// 响应式数据
const formRef = ref()
const generating = ref(false)
const graphIndexedChunks = ref(0)

const formState = reactive({
  name: defaultBenchmarkName(),
  description: '',
  count: 10,
  neighbors_count: 1,
  concurrency_count: 10,
  generation_mode: 'vector',
  graph_expand_top_k: 1,
  llm_model_spec: configStore.config?.default_model || ''
})

// 表单验证规则
const rules = computed(() => ({
  name: [
    { required: true, message: t('benchmark.nameRequired'), trigger: 'blur' },
    { min: 2, max: 100, message: t('benchmark.nameLengthRange'), trigger: 'blur' }
  ],
  count: [{ required: true, message: t('benchmark.countRequired'), trigger: 'blur' }],
  concurrency_count: [{ required: true, message: t('benchmark.concurrencyRequired'), trigger: 'blur' }]
}))

// 双向绑定visible
const visible = computed({
  get: () => props.visible,
  set: (val) => emit('update:visible', val)
})

const graphEnhancedDisabled = computed(() => graphIndexedChunks.value <= 0)

const generationModeOptions = computed(() => [
  {
    value: 'vector',
    label: t('benchmark.vectorBuildLabel'),
    tag: t('benchmark.defaultTag'),
    description: t('benchmark.vectorBuildDesc'),
    helper: t('benchmark.vectorBuildHelper'),
    icon: Database,
    disabled: false
  },
  {
    value: 'graph_enhanced',
    label: t('benchmark.graphEnhancedBuildLabel'),
    tag: t('benchmark.graphTag'),
    description: t('benchmark.graphBuildDesc'),
    helper: graphEnhancedDisabled.value
      ? t('benchmark.graphBuildDisabledHelper')
      : t('benchmark.graphBuiltChunksHelper', { count: graphIndexedChunks.value }),
    icon: Network,
    disabled: graphEnhancedDisabled.value
  }
])

const loadGraphBuildStatus = async () => {
  if (!props.kbId) return
  try {
    const status = await graphBuildApi.getStatus(props.kbId)
    graphIndexedChunks.value = Number(status?.indexed_chunks || 0)
    if (graphEnhancedDisabled.value && formState.generation_mode === 'graph_enhanced') {
      formState.generation_mode = 'vector'
    }
  } catch (error) {
    console.error('加载图谱构建状态失败:', error)
    graphIndexedChunks.value = 0
    if (formState.generation_mode === 'graph_enhanced') {
      formState.generation_mode = 'vector'
    }
  }
}

const selectGenerationMode = (option) => {
  if (option.disabled) return
  formState.generation_mode = option.value
}

// 生成基准
const handleGenerate = async () => {
  if (generating.value) return

  try {
    // 表单验证
    await formRef.value.validate()

    generating.value = true

    const params = {
      name: formState.name,
      description: formState.description,
      count: formState.count,
      neighbors_count: formState.neighbors_count,
      concurrency_count: formState.concurrency_count,
      generation_mode: formState.generation_mode,
      graph_expand_top_k: formState.graph_expand_top_k,
      llm_model_spec: formState.llm_model_spec
    }

    const response = await evaluationApi.generateDataset(props.kbId, params)

    if (response.message === 'success') {
      message.success(t('benchmark.generateTaskSubmitted'))
      visible.value = false
      resetForm()
      emit('success')
    } else {
      generating.value = false
      message.error(response.message || t('benchmark.generateFailed'))
    }
  } catch (error) {
    console.error('生成失败:', error)
    generating.value = false
    message.error(error?.response?.data?.detail || t('benchmark.generateFailed'))
  }
}

// 取消操作
const handleCancel = () => {
  if (generating.value) return
  visible.value = false
  resetForm()
}

// 重置表单
const resetForm = () => {
  formRef.value?.resetFields()
  Object.assign(formState, {
    name: defaultBenchmarkName(),
    description: '',
    count: 10,
    neighbors_count: 1,
    concurrency_count: 10,
    generation_mode: 'vector',
    graph_expand_top_k: 1,
    llm_model_spec: configStore.config?.default_model || ''
  })
  generating.value = false
}

// 选择LLM模型
const handleSelectLLMModel = (modelSpec) => {
  formState.llm_model_spec = modelSpec
}

// 监听visible变化
watch(visible, (val) => {
  if (val && !generating.value) {
    resetForm()
    loadGraphBuildStatus()
  }
})
</script>

<style scoped lang="less">
.generation-mode-cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.field-label-with-help {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.help-icon {
  width: 14px;
  height: 14px;
  color: var(--gray-500);
  cursor: help;
}

.benchmark-modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.benchmark-help-text {
  font-size: 13px;
  line-height: 1.5;
  color: var(--gray-600);
}

.benchmark-help-link {
  margin-left: 2px;
}

.footer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.generation-mode-card {
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  padding: 14px;
  cursor: pointer;
  transition: all 0.2s ease;
  background: var(--gray-0);
  outline: none;

  &:hover,
  &:focus-visible {
    border-color: var(--main-color);
  }

  &:focus-visible {
    box-shadow: 0 0 0 2px var(--main-20);
  }

  &.active {
    border-color: var(--main-color);
    background: var(--main-10);
    box-shadow: 0 0 0 1px var(--main-20);

    .mode-icon {
      color: var(--main-color);
    }
  }

  &.disabled {
    cursor: not-allowed;
    opacity: 0.72;
    background: var(--gray-50);

    &:hover {
      border-color: var(--gray-150);
    }
  }

  .card-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
  }

  .mode-icon {
    width: 20px;
    height: 20px;
    color: var(--main-color);
    flex-shrink: 0;
  }

  .mode-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--gray-800);
  }

  .mode-tag {
    margin-left: auto;
    margin-right: 0;
  }

  .card-description {
    font-size: 13px;
    color: var(--gray-600);
    line-height: 1.5;
  }

  .card-helper {
    margin-top: 10px;
    font-size: 12px;
    line-height: 1.5;
    color: var(--gray-500);

    &.warning {
      color: var(--color-warning-500);
    }
  }
}

@media (max-width: 640px) {
  .generation-mode-cards {
    grid-template-columns: 1fr;
  }

  .benchmark-modal-footer {
    align-items: flex-start;
    flex-direction: column;
  }

  .footer-actions {
    align-self: flex-end;
  }
}
</style>
