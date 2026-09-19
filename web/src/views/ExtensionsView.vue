<template>
  <div class="extensions-view extension-page-root">
    <PageHeader
      v-if="!isDetailPage"
      :title="$t('nav.knowledgeBase')"
      :loading="knowledgeRef?.loading || false"
      :show-border="true"
    />

    <div v-if="!isDetailPage" class="extensions-content">
      <div class="tab-panel">
        <DataBaseView ref="knowledgeRef" embedded />
      </div>
    </div>

    <router-view v-else />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import PageHeader from '@/components/shared/PageHeader.vue'
import DataBaseView from '@/views/DataBaseView.vue'

const route = useRoute()
const knowledgeRef = ref(null)

// 知识库详情的入口：进入知识库详情后由子路由接管整页，隐藏列表页头
const isDetailPage = computed(() => route.path.startsWith('/extensions/knowledgebase/'))
</script>

<style scoped lang="less">
@import '@/assets/css/extensions.less';

.extensions-view {
  .extensions-content {
    flex: 1;
    min-height: 0;
    overflow: hidden;

    .tab-panel {
      height: 100%;
      min-height: 0;
      overflow-y: auto;
    }
  }
}
</style>
