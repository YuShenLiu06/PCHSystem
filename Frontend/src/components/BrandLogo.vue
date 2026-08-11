<script setup lang="ts">
// 品牌徽标：wiki 仓同源资产（PCHSystem-wiki/src/assets/logo{,-dark}.svg）。
// 两版同构 viewBox 0 0 210 40，仅色值不同 → 按当前外观选图。
import { computed } from 'vue'
import { useTheme } from '../composables/useTheme'
import logoLight from '../assets/logo.svg'
import logoDark from '../assets/logo-dark.svg'

const props = withDefaults(defineProps<{ height?: number }>(), { height: 28 })

const { appearance } = useTheme()
const src = computed(() => (appearance.value === 'light' ? logoLight : logoDark))
// SVG 原比例 210:40，按高度算宽避免加载时布局跳动（CLS）
const width = computed(() => Math.round((props.height * 210) / 40))
</script>

<template>
  <!--
    SVG 内含 <title id="t">PCHSystem</title>，但经 <img> 引入时内部 title 不暴露给
    AT，故此处必须给 alt。
  -->
  <img
    class="pch-logo"
    :src="src"
    :width="width"
    :height="props.height"
    alt="PCHSystem"
    decoding="async"
  />
</template>

<style scoped>
.pch-logo {
  display: block;
  /* 像素徽标放大时保持硬边（isometric 方块不做插值模糊） */
  image-rendering: -webkit-optimize-contrast;
  user-select: none;
}
</style>
