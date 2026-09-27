<template>
  <div class="not-found">
    <h1>{{ $t('empty.title') }}</h1>
    <p>{{ $t('empty.desc') }}</p>
    <a-button type="primary" @click="router.push('/agent')">{{ $t('empty.backHome') }}</a-button>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 80vh;
  text-align: center;
}

.not-found h1 {
  font-size: 2rem;
  margin-bottom: 1rem;
}

.not-found p {
  font-size: 1.5rem;
  margin-bottom: 2rem;
}
</style>
