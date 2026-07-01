<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { http } from '../utils/http'

interface Me { uuid: string; name: string; role: string }
const me = ref<Me | null>(null)

onMounted(async () => {
  const { data } = await http.get<Me>('/me')
  me.value = data
})
</script>

<template>
  <el-card v-if="me" header="当前身份">
    <p>UUID：{{ me.uuid }}</p>
    <p>名称：{{ me.name }}</p>
    <p>角色：{{ me.role }}</p>
  </el-card>
</template>
