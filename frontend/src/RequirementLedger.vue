<script setup lang="ts">
import { ref } from 'vue'
import { ClipboardList, Download, RefreshCw } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { api } from './api'
type Record = { id: string; registered_at: string; source_label: string; account_key: string; conversation_title: string; summary: string; uncertainties: string; status: string; task_id: number; evidence_ids: number[] }
const open = ref(false)
const rows = ref<Record[]>([])
const total = ref(0)
const page = ref(1)
const busy = ref(false)
const rebuilding = ref(false)
const downloading = ref(false)
const fileState = ref('missing')
const states: {[key: string]: string} = { ready: 'CSV 已就绪', missing: 'CSV 尚未生成或已被移走', pending: 'CSV 待更新', conflict: 'CSV 被外部修改，先另存或移走文件，再重新生成' }
async function load() {
  busy.value = true
  try {
    const data = await api(`extraction/requirements?page=${page.value}`)
    rows.value = data.list; total.value = data.total; fileState.value = data.file_state
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { busy.value = false }
}
async function show() { page.value = 1; open.value = true; await load() }
async function rebuild() {
  rebuilding.value = true
  try { await api('extraction/requirements/rebuild', { method: 'POST' }); await load(); ElMessage.success('CSV 已重新生成') }
  catch (error) { ElMessage.error((error as Error).message) }
  finally { rebuilding.value = false }
}
async function download() {
  downloading.value = true
  try {
    const response = await fetch('/api/extraction/requirements/file')
    if (!response.ok) { const data = await response.json(); throw new Error(data.detail || 'CSV 下载失败') }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a'); link.href = url; link.download = '需求清单.csv'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { ElMessage.error((error as Error).message); await load() }
  finally { downloading.value = false }
}
</script>
<template>
  <el-button @click="show"><ClipboardList :size="16"/>需求清单</el-button>
  <el-dialog v-model="open" title="需求清单" width="min(1000px, calc(100vw - 24px))" :close-on-click-modal="false">
    <div class="ledger-heading"><span>{{total}} 项 · 全部来源</span><el-button title="刷新需求清单" aria-label="刷新需求清单" :loading="busy" @click="load"><RefreshCw :size="16"/></el-button></div>
    <p class="ledger-path">data/requirements/需求清单.csv</p>
    <el-alert :title="states[fileState] || fileState" :type="fileState==='ready' ? 'success' : 'warning'" :closable="false"/>
    <div v-if="busy" class="empty">加载中</div><div v-else-if="!rows.length" class="empty">暂无已登记需求</div>
    <article v-for="row in rows" :key="row.id" class="ledger-row"><div class="ledger-heading"><strong>{{row.id}}</strong><el-tag>{{row.status}}</el-tag></div><p class="ledger-summary">{{row.summary}}</p><p v-if="row.uncertainties" class="ledger-uncertainty">待确认：{{row.uncertainties}}</p><p class="muted">{{row.source_label}} / {{row.account_key}} · {{row.conversation_title}} · 任务 #{{row.task_id}} · 消息 {{row.evidence_ids.join('、')}}</p></article>
    <el-pagination v-if="total>20" v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" @current-change="load"/>
    <template #footer><div class="ledger-actions"><el-button :loading="rebuilding" :disabled="busy || downloading" @click="rebuild"><RefreshCw :size="16"/>重新生成 CSV</el-button><el-button :disabled="fileState!=='ready' || busy || rebuilding" :loading="downloading" @click="download"><Download :size="16"/>下载 CSV</el-button><el-button @click="open=false">关闭</el-button></div></template>
  </el-dialog>
</template>
<style scoped>
.ledger-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.ledger-path,.ledger-summary,.ledger-uncertainty,.muted{overflow-wrap:anywhere}.ledger-path{font-size:12px;color:#6a7f89}.ledger-row{border-bottom:1px solid #dfe5e8;padding:20px 0}.ledger-summary{font-size:14px;line-height:1.7}.ledger-uncertainty{color:#97733b;font-size:13px}.ledger-actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}.ledger-actions .el-button{margin:0}.el-pagination{max-width:100%;flex-wrap:wrap}
</style>
