<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 空白布局：用于登录页等无导航外壳的场景
 */
</script>

<template>
  <div class="blank-layout">
    <router-view />
  </div>
</template>

<style scoped>
.blank-layout {
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--app-bg);
}
</style>
