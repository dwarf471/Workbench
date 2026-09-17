<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Cable, Database, LayoutDashboard, RefreshCw, Save, ShieldCheck, ListTodo, MessagesSquare, PlugZap } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import SyncManager from './SyncManager.vue'
import ArchiveBrowser from './ArchiveBrowser.vue'
import ModelSettings from './ModelSettings.vue'
import { api } from './api'
const tab = ref('archive')
const settings = ref({ account_key: 'default', account_label: '', mcp_url: '', settings_path: '' })
const db = ref({ ready: false, revision: '', path: '' })
const busy = ref(false)
const saving = ref(false)
const result = ref<{connected: boolean; message: string; server?: string; version?: string; protocol?: string; tools?: string[]} | null>(null)
async function save() {
  saving.value = true
  try {
    settings.value = await api('settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(settings.value)})
    result.value = null
    ElMessage.success('配置已保存')
    return true
  } catch (error) { ElMessage.error((error as Error).message); return false }
  finally { saving.value = false }
}
async function check() {
  busy.value = true
  try {
    if (!(await save())) return
    result.value = await api('connection/check', {method: 'POST'})
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { busy.value = false }
}
onMounted(async () => {
  try { settings.value = await api('settings'); db.value = (await api('health')).database }
  catch (error) { ElMessage.error((error as Error).message) }
})
</script>

<template>
  <div class="shell">
    <aside><div class="brand"><LayoutDashboard :size="22"/><strong>我的工作台</strong></div><button class="nav" :class="{active:tab==='archive'}" @click="tab='archive'"><MessagesSquare :size="18"/>聊天归档</button><button class="nav" :class="{active:tab==='sync'}" @click="tab='sync'"><ListTodo :size="18"/>同步管理</button><button class="nav" :class="{active:tab==='settings'}" @click="tab='settings'"><Cable :size="18"/>连接设置</button><button class="nav" :class="{active:tab==='model'}" @click="tab='model'"><PlugZap :size="18"/>大模型设置</button><div class="local"><ShieldCheck :size="16"/>本机工作空间</div></aside>
    <main>
      <header><div><span class="breadcrumb">工作台 / {{tab==='settings' || tab==='model' ? '系统设置' : '数据归档'}}</span><h1>{{tab==='model' ? '大模型设置' : tab==='sync' ? '同步管理' : tab==='archive' ? '聊天归档' : '连接设置'}}</h1></div><el-tag type="info" effect="plain">首版已验收</el-tag></header>
      <section class="status"><Database :size="22"/><div><strong>本地数据库</strong><p>{{db.ready ? 'SQLite 已就绪' : '等待数据库连接'}}</p></div><el-tag :type="db.ready ? 'success' : 'info'">{{db.ready ? '正常' : '未连接'}}</el-tag><div class="revision">迁移版本 <b>{{db.revision || '—'}}</b></div></section>
      <ArchiveBrowser v-if="tab==='archive'"/><SyncManager v-else-if="tab==='sync'"/><ModelSettings v-else-if="tab==='model'"/>
      <template v-else><section class="config"><div class="section-heading"><Cable :size="20"/><h2>新点即时通讯</h2></div>
        <el-form label-position="top" @submit.prevent="save">
          <el-form-item label="来源账户名称"><el-input v-model="settings.account_label" maxlength="100"/></el-form-item>
          <el-form-item label="来源账户标识"><el-input v-model="settings.account_key" maxlength="100"/></el-form-item>
          <el-form-item label="MCP 服务地址"><el-input v-model="settings.mcp_url"/></el-form-item>
          <el-form-item label="客户端配置文件"><el-input v-model="settings.settings_path"/></el-form-item>
          <div class="actions"><el-button :loading="saving" :disabled="busy" @click="save"><Save :size="16"/>保存配置</el-button><el-button type="primary" :loading="busy" @click="check"><RefreshCw v-if="!busy" :size="16"/>检查连接</el-button></div>
        </el-form>
      </section>
      <section class="connection"><h2>连接状态</h2><template v-if="result"><el-alert :title="result.message" :type="result.connected ? 'success' : 'error'" :closable="false"/><dl v-if="result.connected"><dt>服务</dt><dd>{{result.server}} · {{result.version}}</dd><dt>协议</dt><dd>{{result.protocol}}</dd><dt>可用工具</dt><dd class="tools"><code v-for="tool in result.tools" :key="tool">{{tool}}</code></dd></dl></template><div v-else class="empty"><Cable :size="28"/><span>尚未检查</span></div></section>
      </template><footer><ShieldCheck :size="15"/>MCP 令牌不落盘 · 模型凭证加密保存 <span>·</span> 数据库位置：{{db.path || '—'}}</footer>
    </main>
  </div>
</template>
