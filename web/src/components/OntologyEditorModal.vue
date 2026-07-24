<template>
  <a-modal
    :open="open"
    :title="modalTitle"
    width="860px"
    :confirm-loading="submitting"
    :ok-text="mode === 'edit' ? '保存修改' : '创建并启用'"
    cancel-text="关闭"
    :footer="mode === 'view' ? null : undefined"
    @ok="submit"
    @cancel="close"
  >
    <a-alert
      class="version-notice"
      type="info"
      show-icon
      :message="noticeMessage"
    />

    <a-form layout="vertical">
      <div class="form-grid identity-grid">
        <a-form-item label="展示名称" required>
          <a-input v-model:value="form.name" :disabled="isReadOnly" placeholder="例如：产品知识本体" />
        </a-form-item>
        <a-form-item label="Registry ID" required>
          <a-input v-model:value="form.registry_id" :disabled="mode !== 'create'" placeholder="例如：product-core" />
        </a-form-item>
        <a-form-item label="版本" required>
          <a-input v-model:value="form.version" :disabled="mode !== 'create'" placeholder="例如：1.0.0" />
        </a-form-item>
      </div>

      <section class="editor-section">
        <div class="section-title-row">
          <div>
            <h4>实体类型</h4>
            <p>定义业务中需要识别的对象类型，以及具体对象的标准名称和别名。</p>
          </div>
          <a-button v-if="!isReadOnly" @click="addEntity"><Plus :size="15" />添加实体</a-button>
        </div>

        <div v-for="(entity, entityIndex) in form.entities" :key="entity.id" class="editor-card">
          <button
            v-if="!isReadOnly && form.entities.length > 1"
            type="button"
            class="remove-button"
            aria-label="删除实体"
            @click="removeEntity(entityIndex)"
          >
            <Trash2 :size="15" />
          </button>
          <div class="form-grid two-columns">
            <a-form-item label="类型名称" required>
              <a-input v-model:value="entity.name" :disabled="isReadOnly" placeholder="例如：Product" />
            </a-form-item>
            <a-form-item label="业务说明">
              <a-input v-model:value="entity.description" :disabled="isReadOnly" placeholder="什么属于该类型" />
            </a-form-item>
          </div>
          <a-form-item label="典型示例">
            <a-select
              v-model:value="entity.examples"
              mode="tags"
              placeholder="输入示例后按 Enter"
              :disabled="isReadOnly"
              :token-separators="[',']"
            />
          </a-form-item>
          <div class="alias-header">
            <span>标准名称与别名</span>
            <a-button v-if="!isReadOnly" size="small" type="text" @click="addCanonicalAlias(entity)">
              <Plus :size="14" />添加
            </a-button>
          </div>
          <div
            v-for="(alias, aliasIndex) in entity.canonical_aliases"
            :key="alias.id"
            class="nested-row"
          >
            <a-input v-model:value="alias.canonical" :disabled="isReadOnly" placeholder="标准名称，例如：MCSTARS" />
            <a-select
              v-model:value="alias.aliases"
              mode="tags"
              placeholder="输入别名后按 Enter"
              :disabled="isReadOnly"
              :token-separators="[',']"
            />
            <a-button v-if="!isReadOnly" type="text" danger @click="entity.canonical_aliases.splice(aliasIndex, 1)">
              <Trash2 :size="15" />
            </a-button>
          </div>
        </div>
      </section>

      <section class="editor-section">
        <div class="section-title-row">
          <div>
            <h4>关系</h4>
            <p>关系方向为 source → relation → target，端点只能选择已定义实体或 Any。</p>
          </div>
          <a-button v-if="!isReadOnly" @click="addRelation"><Plus :size="15" />添加关系</a-button>
        </div>

        <a-empty v-if="!form.relations.length" description="暂无关系" />
        <div v-for="(relation, index) in form.relations" :key="relation.id" class="editor-card">
          <button
            v-if="!isReadOnly"
            type="button"
            class="remove-button"
            aria-label="删除关系"
            @click="form.relations.splice(index, 1)"
          >
            <Trash2 :size="15" />
          </button>
          <div class="form-grid two-columns">
            <a-form-item label="关系名称" required>
              <a-input v-model:value="relation.name" :disabled="isReadOnly" placeholder="例如：SUPPORTS" />
            </a-form-item>
            <a-form-item label="业务说明">
              <a-input v-model:value="relation.description" :disabled="isReadOnly" placeholder="该关系表达什么事实" />
            </a-form-item>
          </div>
          <div class="form-grid two-columns">
            <a-form-item label="Source" required>
              <a-select
                v-model:value="relation.source"
                mode="multiple"
                :options="endpointOptions"
                :disabled="isReadOnly"
                placeholder="选择起点类型"
              />
            </a-form-item>
            <a-form-item label="Target" required>
              <a-select
                v-model:value="relation.target"
                mode="multiple"
                :options="endpointOptions"
                :disabled="isReadOnly"
                placeholder="选择终点类型"
              />
            </a-form-item>
          </div>
          <a-form-item label="关系别名">
            <a-select
              v-model:value="relation.aliases"
              mode="tags"
              placeholder="例如：支持、具备、提供"
              :disabled="isReadOnly"
              :token-separators="[',']"
            />
          </a-form-item>
        </div>
      </section>

      <section class="editor-section">
        <div class="section-title-row">
          <div>
            <h4>属性</h4>
            <p>只添加需要筛选、比较或结构化展示的参数；属性 key 在所有分类中必须唯一。</p>
          </div>
          <a-button v-if="!isReadOnly" @click="addProperty"><Plus :size="15" />添加属性</a-button>
        </div>

        <a-empty v-if="!form.properties.length" description="暂无属性" />
        <div v-for="(property, index) in form.properties" :key="property.id" class="property-row">
          <a-input v-model:value="property.category" :disabled="isReadOnly" placeholder="分类，例如 Hardware" />
          <a-input v-model:value="property.name" :disabled="isReadOnly" placeholder="属性 key，例如 screen_size" />
          <a-select v-model:value="property.type" :disabled="isReadOnly" :options="propertyTypeOptions" />
          <a-input v-model:value="property.unit" :disabled="isReadOnly" placeholder="单位（可选）" />
          <a-button v-if="!isReadOnly" type="text" danger @click="form.properties.splice(index, 1)">
            <Trash2 :size="15" />
          </a-button>
        </div>
      </section>
      <section class="editor-section">
        <div class="section-title-row">
          <div>
            <h4>附加规则</h4>
            <p>保留 schema.json 中参与抽取 Prompt 的扩展规则。</p>
          </div>
        </div>
        <a-textarea v-model:value="form.rules_text" :disabled="isReadOnly" :rows="6" />
      </section>
    </a-form>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import { ontologyRegistryApi } from '@/apis/ontology_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  mode: { type: String, default: 'create' },
  detail: { type: Object, default: null }
})
const emit = defineEmits(['update:open', 'created'])

let rowId = 0
const nextId = () => ++rowId
const newAlias = () => ({ id: nextId(), canonical: '', aliases: [] })
const newEntity = () => ({
  id: nextId(),
  name: '',
  description: '',
  examples: [],
  canonical_aliases: []
})
const newRelation = () => ({
  id: nextId(),
  name: '',
  description: '',
  source: [],
  target: [],
  aliases: []
})
const newProperty = () => ({
  id: nextId(),
  category: '',
  name: '',
  type: 'string',
  unit: ''
})

const form = reactive({
  name: '',
  registry_id: '',
  version: '1.0.0',
  entities: [newEntity()],
  relations: [],
  properties: [],
  rules_text: '{}'
})
const submitting = ref(false)
const identityPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const isReadOnly = computed(() => props.mode === 'view')
const modalTitle = computed(() => {
  if (props.mode === 'view') return '查看 Core Ontology'
  if (props.mode === 'edit') return '编辑 Core Ontology'
  return '新建 Core Ontology'
})
const noticeMessage = computed(() => {
  if (props.mode === 'view') return '当前版本为只读详情。'
  if (props.mode === 'edit') return '保存后当前版本的 digest 将更新；被知识库引用时不能覆盖。'
  return 'Ontology 创建后立即启用；同一 ID 和版本不能重复发布不同内容。'
})
const propertyTypeOptions = [
  { label: '文本', value: 'string' },
  { label: '整数', value: 'int' },
  { label: '数字', value: 'float' },
  { label: '布尔值', value: 'bool' }
]
const endpointOptions = computed(() => [
  ...form.entities
    .map((entity) => entity.name.trim())
    .filter(Boolean)
    .map((name) => ({ label: name, value: name })),
  { label: 'Any（任意实体类型）', value: 'Any' }
])

const reset = () => {
  form.name = ''
  form.registry_id = ''
  form.version = '1.0.0'
  form.entities = [newEntity()]
  form.relations = []
  form.properties = []
  form.rules_text = '{}'
}

const fillDetail = (detail) => {
  const item = detail?.item || {}
  const definition = detail?.definition || {}
  form.name = definition.name || item.name || ''
  form.registry_id = item.registry_id || ''
  form.version = item.version || ''
  form.entities = (definition.entities || []).map((entity) => ({
    ...entity,
    id: nextId(),
    canonical_aliases: (entity.canonical_aliases || []).map((alias) => ({ ...alias, id: nextId() }))
  }))
  form.relations = (definition.relations || []).map((relation) => ({ ...relation, id: nextId() }))
  form.properties = (definition.properties || []).map((property) => ({
    ...property,
    id: nextId(),
    unit: property.unit || ''
  }))
  form.rules_text = JSON.stringify(definition.rules || {}, null, 2)
}

const close = () => emit('update:open', false)
const addEntity = () => form.entities.push(newEntity())
const removeEntity = (index) => form.entities.splice(index, 1)
const addCanonicalAlias = (entity) => entity.canonical_aliases.push(newAlias())
const addRelation = () => form.relations.push(newRelation())
const addProperty = () => form.properties.push(newProperty())
const cleanTags = (items) => [...new Set(items.map((item) => String(item).trim()).filter(Boolean))]
const ensureUnique = (values, label) => {
  const seen = new Set()
  for (const value of values) {
    const normalized = value.trim().toLowerCase()
    if (seen.has(normalized)) throw new Error(`${label}不能重复：${value}`)
    seen.add(normalized)
  }
}

const buildPayload = () => {
  const name = form.name.trim()
  const registryId = form.registry_id.trim()
  const version = form.version.trim()
  if (!name || !registryId || !version) throw new Error('请填写展示名称、Registry ID 和版本')
  if (!identityPattern.test(registryId) || !identityPattern.test(version)) {
    throw new Error('Registry ID 和版本只能包含字母、数字、点、下划线和中划线，长度 1-64')
  }

  const entities = form.entities.map((entity) => ({
    name: entity.name.trim(),
    description: entity.description.trim(),
    examples: cleanTags(entity.examples),
    canonical_aliases: entity.canonical_aliases
      .filter((item) => item.canonical.trim() || cleanTags(item.aliases).length)
      .map((item) => ({ canonical: item.canonical.trim(), aliases: cleanTags(item.aliases) }))
  }))
  if (entities.some((entity) => !entity.name)) throw new Error('实体类型名称不能为空')
  ensureUnique(entities.map((entity) => entity.name), '实体类型')

  const entityNames = new Set(entities.map((entity) => entity.name))
  const relations = form.relations.map((relation) => ({
    name: relation.name.trim(),
    description: relation.description.trim(),
    source: cleanTags(relation.source),
    target: cleanTags(relation.target),
    aliases: cleanTags(relation.aliases)
  }))
  ensureUnique(relations.map((relation) => relation.name), '关系名称')
  for (const relation of relations) {
    if (!relation.name || !relation.source.length || !relation.target.length) {
      throw new Error('关系名称、Source 和 Target 不能为空')
    }
    const invalid = [...relation.source, ...relation.target].find(
      (endpoint) => endpoint !== 'Any' && !entityNames.has(endpoint)
    )
    if (invalid) throw new Error(`关系 ${relation.name} 引用了未声明实体：${invalid}`)
  }

  const properties = form.properties.map((property) => ({
    category: property.category.trim(),
    name: property.name.trim(),
    type: property.type,
    unit: property.unit.trim() || null
  }))
  if (properties.some((property) => !property.category || !property.name)) {
    throw new Error('属性分类和属性 key 不能为空')
  }
  ensureUnique(properties.map((property) => property.name), '属性 key')
  let rules
  try {
    rules = JSON.parse(form.rules_text || '{}')
  } catch {
    throw new Error('附加规则必须是合法 JSON 对象')
  }
  if (!rules || Array.isArray(rules) || typeof rules !== 'object') {
    throw new Error('附加规则必须是 JSON 对象')
  }

  return { registry_id: registryId, version, name, entities, relations, properties, rules }
}

const submit = async () => {
  try {
    const payload = buildPayload()
    submitting.value = true
    const result = props.mode === 'edit'
      ? await ontologyRegistryApi.overwrite(props.detail.item, {
          ...payload,
          expected_digest: props.detail.item.digest
        })
      : await ontologyRegistryApi.create(payload)
    message.success(
      props.mode === 'edit'
        ? result.changed ? 'Core Ontology 已更新' : '内容没有变化'
        : result.already_exists ? '该 Ontology 版本已存在' : 'Core Ontology 创建成功'
    )
    emit('created', result.item)
    close()
    reset()
  } catch (error) {
    const detail = error?.response?.data?.detail || error?.message || '创建 Core Ontology 失败'
    message.error(detail)
  } finally {
    submitting.value = false
  }
}

watch(
  () => [props.open, props.detail, props.mode],
  ([open, detail]) => {
    if (open && detail) fillDetail(detail)
    if (!open) reset()
  }
)
</script>

<style scoped lang="less">
.version-notice {
  margin-bottom: 16px;
}

.form-grid {
  display: grid;
  gap: 12px;
}

.identity-grid {
  grid-template-columns: 1.4fr 1fr 0.7fr;
}

.two-columns {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.editor-section {
  margin-top: 20px;
}

.section-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;

  h4 {
    margin: 0 0 4px;
    color: var(--gray-1000);
  }

  p {
    margin: 0;
    color: var(--gray-600);
    font-size: 13px;
  }

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
}

.editor-card {
  position: relative;
  margin-bottom: 10px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.remove-button {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--color-error-500);
  cursor: pointer;
}

.alias-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  color: var(--gray-700);
  font-size: 13px;
}

.nested-row,
.property-row {
  display: grid;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.nested-row {
  grid-template-columns: 0.8fr 1.5fr 36px;
}

.property-row {
  grid-template-columns: 0.9fr 1.2fr 0.7fr 0.7fr 36px;
}

@media (max-width: 760px) {
  .identity-grid,
  .two-columns,
  .nested-row,
  .property-row {
    grid-template-columns: 1fr;
  }
}
</style>
