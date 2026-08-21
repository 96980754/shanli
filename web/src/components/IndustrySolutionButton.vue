<template>
  <button
    class="input-action-btn"
    type="button"
    :class="{ disabled }"
    :disabled="disabled"
    title="生成多产品行业解决方案"
    @click="openModal"
  >
    <FileText :size="16" />
    <span class="hide-text">行业方案</span>
  </button>

  <a-modal
    v-model:open="open"
    title="生成多产品行业解决方案"
    ok-text="开始生成"
    cancel-text="取消"
    :ok-button-props="{ disabled: !canSubmit }"
    @ok="submit"
  >
    <div class="solution-form">
      <label>
        <span>行业 / 场景</span>
        <a-input v-model:value="industry" :maxlength="120" placeholder="例如：智慧园区" />
      </label>
      <label>
        <span>需求</span>
        <a-textarea
          v-model:value="requirement"
          :maxlength="2000"
          :rows="4"
          placeholder="描述业务目标、约束和期望效果"
        />
      </label>
      <label>
        <span>产品（2–5 个）</span>
        <div class="product-entry">
          <a-input
            v-model:value="productInput"
            :maxlength="80"
            placeholder="输入产品名称后按 Enter"
            @pressEnter="addProduct"
          />
          <a-button :disabled="!productInput.trim() || products.length >= 5" @click="addProduct">
            添加
          </a-button>
        </div>
      </label>
      <div v-if="products.length" class="product-tags">
        <a-tag v-for="product in products" :key="product" closable @close="removeProduct(product)">
          {{ product }}
        </a-tag>
      </div>
      <p class="form-hint">系统会分别检索每个产品资料，再整合生成带来源引用的方案和 Word。</p>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import { FileText } from 'lucide-vue-next'
import {
  MAX_INDUSTRY_SOLUTION_PRODUCTS,
  MIN_INDUSTRY_SOLUTION_PRODUCTS,
  buildIndustrySolutionPayload,
  normalizeIndustrySolutionProducts
} from '@/utils/industrySolution'

defineProps({ disabled: { type: Boolean, default: false } })
const emit = defineEmits(['generate'])

const open = ref(false)
const industry = ref('')
const requirement = ref('')
const productInput = ref('')
const products = ref([])

const canSubmit = computed(
  () =>
    industry.value.trim() &&
    requirement.value.trim() &&
    products.value.length >= MIN_INDUSTRY_SOLUTION_PRODUCTS
)

const openModal = () => {
  open.value = true
}

const addProduct = () => {
  const name = productInput.value.trim()
  if (!name) return
  if (products.value.length >= MAX_INDUSTRY_SOLUTION_PRODUCTS) {
    message.warning(`最多添加 ${MAX_INDUSTRY_SOLUTION_PRODUCTS} 个产品`)
    return
  }
  const normalized = normalizeIndustrySolutionProducts([...products.value, name])
  if (normalized.length === products.value.length) {
    message.info('该产品已添加')
    return
  }
  products.value = normalized
  productInput.value = ''
}

const removeProduct = (product) => {
  products.value = products.value.filter((item) => item !== product)
}

const submit = () => {
  if (!canSubmit.value) return
  emit(
    'generate',
    buildIndustrySolutionPayload({
      industry: industry.value,
      requirement: requirement.value,
      products: products.value
    })
  )
  open.value = false
}
</script>

<style lang="less" scoped>
.solution-form {
  display: flex;
  flex-direction: column;
  gap: 16px;

  label {
    display: flex;
    flex-direction: column;
    gap: 6px;
    color: var(--gray-800);
    font-size: 14px;
    font-weight: 500;
  }
}

.product-entry {
  display: flex;
  gap: 8px;
}

.product-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.form-hint {
  margin: 0;
  color: var(--gray-500);
  font-size: 12px;
}
</style>
