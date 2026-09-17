<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Save, PlugZap, RotateCcw } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { api } from './api'
const settings = ref({ base_url: '', model: '', timeout: 120, key_configured: false, extraction_prompt: '' })
const key = ref('')
const clearKey = ref(false)
const saving = ref(false)
const checking = ref(false)
const restoring = ref(false)
const result = ref<{connected: boolean; message: string} | null>(null)
async function save() {
  saving.value = true
  try {
    settings.value = await api('model/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_url: settings.value.base_url, model: settings.value.model, timeout: settings.value.timeout, extraction_prompt: settings.value.extraction_prompt, api_key: key.value || null, clear_key: clearKey.value }) })
    key.value = ''; clearKey.value = false; result.value = null
    ElMessage.success('模型配置已保存')
    return true
  } catch (error) { ElMessage.error((error as Error).message); return false }
  finally { saving.value = false }
}
async function check() {
  checking.value = true
  try { if (await save()) result.value = await api('model/check', { method: 'POST' }) }
  catch (error) { ElMessage.error((error as Error).message) }
  finally { checking.value = false }
}
async function restore() {
  restoring.value = true
  try {
    settings.value.extraction_prompt = (await api('model/prompt-default')).extraction_prompt
    result.value = null
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { restoring.value = false }
}
onMounted(async () => {
  try { settings.value = await api('model/settings') }
  catch (error) { ElMessage.error((error as Error).message) }
})
</script>
<template>
  <section class="config">
    <div class="section-heading"><PlugZap :size="20"/><h2>大模型设置</h2></div>
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item label="接口协议"><el-input model-value="OpenAI 兼容 Chat Completions" disabled/></el-form-item>
      <el-form-item label="API 基础地址"><el-input v-model="settings.base_url" placeholder="https://模型服务地址/v1" maxlength="2000"/></el-form-item>
      <el-alert v-if="settings.base_url.trim().toLowerCase().startsWith('http://')" title="HTTP 不加密传输，API Key 和请求内容可能被截获，请仅用于可信网络。" type="warning" :closable="false"/>
      <el-form-item label="模型名称"><el-input v-model="settings.model" maxlength="200"/></el-form-item>
      <el-form-item :label="`API Key（${settings.key_configured ? '已配置，留空保留' : '未配置，可选'}）`"><el-input v-model="key" type="password" autocomplete="new-password" :disabled="clearKey" maxlength="4096"/></el-form-item>
      <el-form-item><el-checkbox v-model="clearKey" @change="key=''">清除已保存的 API Key</el-checkbox></el-form-item>
      <el-form-item label="请求超时（秒）"><el-input-number v-model="settings.timeout" :min="5" :max="300"/></el-form-item>
      <el-form-item label="需求提取提示词"><el-input v-model="settings.extraction_prompt" type="textarea" :rows="12" maxlength="8000" show-word-limit :disabled="saving || checking || restoring"/></el-form-item>
      <div class="prompt-reset"><el-button :loading="restoring" :disabled="saving || checking" @click="restore"><RotateCcw :size="16"/>恢复默认提示词</el-button></div>
      <div class="actions"><el-button :loading="saving" :disabled="checking || restoring" @click="save"><Save :size="16"/>保存配置</el-button><el-button type="primary" :loading="checking" :disabled="saving || restoring" @click="check"><PlugZap :size="16"/>测试连接</el-button></div>
    </el-form>
  </section>
  <section class="connection"><h2>模型连接状态</h2><el-alert v-if="result" :title="result.message" :type="result.connected ? 'success' : 'error'" :closable="false"/><div v-else class="empty">尚未测试</div></section>
</template>
<style scoped>
.prompt-reset{margin-bottom:16px}.config :deep(.el-textarea__inner){line-height:1.7;padding-bottom:28px}
</style>
