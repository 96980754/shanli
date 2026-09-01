<template>
  <a-modal
    :open="open"
    :title="modalTitle"
    width="860px"
    :confirm-loading="submitting"
    :ok-text="mode === 'edit' ? $t('ontology.saveChanges') : $t('ontology.createAndEnable')"
    :cancel-text="$t('common.close')"
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
        <a-form-item :label="$t('ontology.displayName')" required>
          <a-input v-model:value="form.name" :disabled="isReadOnly" :placeholder="$t('ontology.namePlaceholder')" />
        </a-form-item>
        <a-form-item label="Registry ID" required>
          <a-input v-model:value="form.registry_id" :disabled="mode !== 'create'" :placeholder="$t('ontology.registryIdPlaceholder')" />
        </a-form-item>
        <a-form-item :label="$t('ontology.versionLabel')" required>
          <a-input v-model:value="form.version" :disabled="mode !== 'create'" :placeholder="$t('ontology.versionPlaceholder')" />
        </a-form-item>
      </div>

      <section class="editor-section">
        <div class="section-title-row">
          <div>
            <h4>{{ $t('ontology.entityTypesTitle') }}</h4>
            <p>{{ $t('ontology.entityTypesDesc') }}</p>
          </div>
          <a-button v-if="!isReadOnly" @click="addEntity"><Plus :size="15" />{{ $t('ontology.addEntity') }}</a-button>
        </div>

        <div v-for="(entity, entityIndex) in form.entities" :key="entity.id" class="editor-card">
          <button
            v-if="!isReadOnly && form.entities.length > 1"
            type="button"
            class="remove-button"
            :aria-label="$t('ontology.deleteEntity')"
            @click="removeEntity(entityIndex)"
          >
            <Trash2 :size="15" />
          </button>
          <div class="form-grid two-columns">
            <a-form-item :label="$t('ontology.typeNameLabel')" required>
              <a-input v-model:value="entity.name" :disabled="isReadOnly" :placeholder="$t('ontology.typeNamePlaceholder')" />
            </a-form-item>
            <a-form-item :label="$t('ontology.businessDesc')">
              <a-input v-model:value="entity.description" :disabled="isReadOnly" :placeholder="$t('ontology.businessDescPlaceholder')" />
            </a-form-item>
          </div>
          <a-form-item :label="$t('ontology.typicalExamples')">
            <a-select
              v-model:value="entity.examples"
              mode="tags"
              :placeholder="$t('ontology.enterExamplePlaceholder')"
              :disabled="isReadOnly"
              :token-separators="[',']"
            />
          </a-form-item>
          <div class="alias-header">
            <span>{{ $t('ontology.canonicalAliasesTitle') }}</span>
            <a-button v-if="!isReadOnly" size="small" type="text" @click="addCanonicalAlias(entity)">
              <Plus :size="14" />{{ $t('ontology.add') }}
            </a-button>
          </div>
          <div
            v-for="(alias, aliasIndex) in entity.canonical_aliases"
            :key="alias.id"
            class="nested-row"
          >
            <a-input v-model:value="alias.canonical" :disabled="isReadOnly" :placeholder="$t('ontology.canonicalPlaceholder')" />
            <a-select
              v-model:value="alias.aliases"
              mode="tags"
              :placeholder="$t('ontology.enterAliasPlaceholder')"
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
            <h4>{{ $t('ontology.relationsTitle') }}</h4>
            <p>{{ $t('ontology.relationsDesc') }}</p>
          </div>
          <a-button v-if="!isReadOnly" @click="addRelation"><Plus :size="15" />{{ $t('ontology.addRelation') }}</a-button>
        </div>

        <a-empty v-if="!form.relations.length" :description="$t('ontology.noRelations')" />
        <div v-for="(relation, index) in form.relations" :key="relation.id" class="editor-card">
          <button
            v-if="!isReadOnly"
            type="button"
            class="remove-button"
            :aria-label="$t('ontology.deleteRelation')"
            @click="form.relations.splice(index, 1)"
          >
            <Trash2 :size="15" />
          </button>
          <div class="form-grid two-columns">
            <a-form-item :label="$t('ontology.relationNameLabel')" required>
              <a-input v-model:value="relation.name" :disabled="isReadOnly" :placeholder="$t('ontology.relationNamePlaceholder')" />
            </a-form-item>
            <a-form-item :label="$t('ontology.businessDesc')">
              <a-input v-model:value="relation.description" :disabled="isReadOnly" :placeholder="$t('ontology.relationDescPlaceholder')" />
            </a-form-item>
          </div>
          <div class="form-grid two-columns">
            <a-form-item label="Source" required>
              <a-select
                v-model:value="relation.source"
                mode="multiple"
                :options="endpointOptions"
                :disabled="isReadOnly"
                :placeholder="$t('ontology.selectSourceType')"
              />
            </a-form-item>
            <a-form-item label="Target" required>
              <a-select
                v-model:value="relation.target"
                mode="multiple"
                :options="endpointOptions"
                :disabled="isReadOnly"
                :placeholder="$t('ontology.selectTargetType')"
              />
            </a-form-item>
          </div>
          <a-form-item :label="$t('ontology.relationAliases')">
            <a-select
              v-model:value="relation.aliases"
              mode="tags"
              :placeholder="$t('ontology.relationAliasesPlaceholder')"
              :disabled="isReadOnly"
              :token-separators="[',']"
            />
          </a-form-item>
        </div>
      </section>

      <section class="editor-section">
        <div class="section-title-row">
          <div>
            <h4>{{ $t('ontology.propertiesTitle') }}</h4>
            <p>{{ $t('ontology.propertiesDesc') }}</p>
          </div>
          <a-button v-if="!isReadOnly" @click="addProperty"><Plus :size="15" />{{ $t('ontology.addProperty') }}</a-button>
        </div>

        <a-empty v-if="!form.properties.length" :description="$t('ontology.noProperties')" />
        <div v-for="(property, index) in form.properties" :key="property.id" class="editor-card">
          <button
            v-if="!isReadOnly"
            type="button"
            class="remove-button"
            :aria-label="$t('ontology.deleteProperty')"
            @click="form.properties.splice(index, 1)"
          >
            <Trash2 :size="15" />
          </button>
          <div class="form-grid property-grid">
            <a-form-item :label="$t('ontology.categoryLabel')" required>
              <a-input v-model:value="property.category" :disabled="isReadOnly" :placeholder="$t('ontology.categoryPlaceholder')" />
            </a-form-item>
            <a-form-item :label="$t('ontology.propertyKeyLabel')" required>
              <a-input v-model:value="property.name" :disabled="isReadOnly" :placeholder="$t('ontology.propertyKeyPlaceholder')" />
            </a-form-item>
            <a-form-item :label="$t('ontology.propertyTypeLabel')" required>
              <a-select v-model:value="property.type" :disabled="isReadOnly" :options="propertyTypeOptions" />
            </a-form-item>
            <a-form-item :label="$t('ontology.unitLabel')">
              <a-input v-model:value="property.unit" :disabled="isReadOnly" :placeholder="$t('ontology.optionalPlaceholder')" />
            </a-form-item>
          </div>
          <div class="form-grid two-columns">
            <a-form-item :label="$t('ontology.ownerLabel')">
              <a-select
                v-model:value="property.owners"
                mode="multiple"
                :options="propertyOwnerOptions"
                :disabled="isReadOnly"
                :placeholder="$t('ontology.ownerPlaceholder')"
              />
            </a-form-item>
            <a-form-item :label="$t('ontology.enumLabel')">
              <a-select
                v-model:value="property.enum"
                mode="tags"
                :disabled="isReadOnly || property.type !== 'string'"
                :token-separators="[',']"
                :placeholder="$t('ontology.enumPlaceholder')"
              />
            </a-form-item>
          </div>
          <a-form-item :label="$t('ontology.propertyDescLabel')">
            <a-input v-model:value="property.description" :disabled="isReadOnly" :placeholder="$t('ontology.propertyDescPlaceholder')" />
          </a-form-item>
        </div>
      </section>
      <section class="editor-section">
        <div class="section-title-row">
          <div>
            <h4>{{ $t('ontology.extraRulesTitle') }}</h4>
            <p>{{ $t('ontology.extraRulesDesc') }}</p>
          </div>
        </div>
        <a-textarea v-model:value="form.rules_text" :disabled="isReadOnly" :rows="6" />
      </section>
    </a-form>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { message } from 'ant-design-vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import { ontologyRegistryApi } from '@/apis/ontology_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  mode: { type: String, default: 'create' },
  detail: { type: Object, default: null }
})
const emit = defineEmits(['update:open', 'created'])

const { t } = useI18n()

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
  unit: '',
  owners: [],
  enum: [],
  description: ''
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
  if (props.mode === 'view') return t('ontology.viewTitle')
  if (props.mode === 'edit') return t('ontology.editTitle')
  return t('ontology.createTitle')
})
const noticeMessage = computed(() => {
  if (props.mode === 'view') return t('ontology.viewNotice')
  if (props.mode === 'edit') return t('ontology.editNotice')
  return t('ontology.createNotice')
})
const propertyTypeOptions = computed(() => [
  { label: t('ontology.propertyTypeText'), value: 'string' },
  { label: t('ontology.propertyTypeInt'), value: 'int' },
  { label: t('ontology.propertyTypeFloat'), value: 'float' },
  { label: t('ontology.propertyTypeBool'), value: 'bool' }
])
const propertyOwnerOptions = computed(() =>
  form.entities
    .map((entity) => entity.name.trim())
    .filter(Boolean)
    .map((name) => ({ label: name, value: name }))
)
const endpointOptions = computed(() => [
  ...form.entities
    .map((entity) => entity.name.trim())
    .filter(Boolean)
    .map((name) => ({ label: name, value: name })),
  { label: t('ontology.anyEntityType'), value: 'Any' }
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
    unit: property.unit || '',
    owners: property.owners || [],
    enum: property.enum || [],
    description: property.description || ''
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
    if (seen.has(normalized)) throw new Error(t('ontology.duplicateError', { label, value }))
    seen.add(normalized)
  }
}

const buildPayload = () => {
  const name = form.name.trim()
  const registryId = form.registry_id.trim()
  const version = form.version.trim()
  if (!name || !registryId || !version) throw new Error(t('ontology.fillIdentityRequired'))
  if (!identityPattern.test(registryId) || !identityPattern.test(version)) {
    throw new Error(t('ontology.identityPatternError'))
  }

  const entities = form.entities.map((entity) => ({
    name: entity.name.trim(),
    description: entity.description.trim(),
    examples: cleanTags(entity.examples),
    canonical_aliases: entity.canonical_aliases
      .filter((item) => item.canonical.trim() || cleanTags(item.aliases).length)
      .map((item) => ({ canonical: item.canonical.trim(), aliases: cleanTags(item.aliases) }))
  }))
  if (entities.some((entity) => !entity.name)) throw new Error(t('ontology.entityNameRequired'))
  ensureUnique(entities.map((entity) => entity.name), t('ontology.entityTypesTitle'))

  const entityNames = new Set(entities.map((entity) => entity.name))
  const relations = form.relations.map((relation) => ({
    name: relation.name.trim(),
    description: relation.description.trim(),
    source: cleanTags(relation.source),
    target: cleanTags(relation.target),
    aliases: cleanTags(relation.aliases)
  }))
  ensureUnique(relations.map((relation) => relation.name), t('ontology.relationNameLabel'))
  for (const relation of relations) {
    if (!relation.name || !relation.source.length || !relation.target.length) {
      throw new Error(t('ontology.relationFieldsRequired'))
    }
    const invalid = [...relation.source, ...relation.target].find(
      (endpoint) => endpoint !== 'Any' && !entityNames.has(endpoint)
    )
    if (invalid) throw new Error(t('ontology.relationInvalidEndpoint', { name: relation.name, endpoint: invalid }))
  }

  const properties = form.properties.map((property) => ({
    category: property.category.trim(),
    name: property.name.trim(),
    type: property.type,
    unit: property.unit.trim() || null,
    owners: cleanTags(property.owners),
    enum: cleanTags(property.enum),
    description: property.description.trim()
  }))
  if (properties.some((property) => !property.category || !property.name)) {
    throw new Error(t('ontology.propertyFieldsRequired'))
  }
  for (const property of properties) {
    const invalidOwner = property.owners.find((owner) => !entityNames.has(owner))
    if (invalidOwner) throw new Error(t('ontology.propertyInvalidOwner', { name: property.name, endpoint: invalidOwner }))
    if (property.enum.length && property.type !== 'string') {
      throw new Error(t('ontology.propertyEnumTextOnly', { name: property.name }))
    }
  }
  ensureUnique(properties.map((property) => property.name), t('ontology.propertyKeyLabel'))
  let rules
  try {
    rules = JSON.parse(form.rules_text || '{}')
  } catch {
    throw new Error(t('ontology.rulesInvalidJson'))
  }
  if (!rules || Array.isArray(rules) || typeof rules !== 'object') {
    throw new Error(t('ontology.rulesNotObject'))
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
        ? result.changed ? t('ontology.updatedSuccess') : t('ontology.noChange')
        : result.already_exists ? t('ontology.versionExists') : t('ontology.createdSuccess')
    )
    emit('created', result.item)
    close()
    reset()
  } catch (error) {
    const detail = error?.response?.data?.detail || error?.message || t('ontology.createFailed')
    message.error(typeof detail === 'object' ? detail.message || t('ontology.operationFailed') : detail)
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

.nested-row {
  display: grid;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.nested-row {
  grid-template-columns: 0.8fr 1.5fr 36px;
}

.property-grid {
  grid-template-columns: 0.9fr 1.2fr 0.7fr 0.7fr;
}

@media (max-width: 760px) {
  .identity-grid,
  .two-columns,
  .nested-row,
  .property-grid {
    grid-template-columns: 1fr;
  }
}
</style>
