<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { DatabaseBackup, Download, MessagesSquare, Search, UserRound, UsersRound, RefreshCw } from 'lucide-vue-next'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from './api'
type Conversation = { id: number; title: string; conversation_type: number; message_count?: number; latest_time_readable?: string; sync_status?: string }
type Message = { id: number; conversation_id: number; conversation_title: string; conversation_type: number; source_id: number; sender_id: string; sender_name: string; sent_time_readable: string; message_type: string; text_content: string; content: Record<string, unknown> }
const sources = ref<{id: number; label: string; account_key: string}[]>([])
const sourceId = ref<number | null>(null)
const mode = ref('conversations')
const conversationQuery = ref('')
const conversationType = ref(0)
const conversations = ref<Conversation[]>([])
const selected = ref<Conversation | null>(null)
const conversationPage = ref(1)
const conversationTotal = ref(0)
const messages = ref<Message[]>([])
const messagePage = ref(1)
const messageTotal = ref(0)
const q = ref('')
const sender = ref('')
const range = ref<[string, string] | null>(null)
const busy = ref(false)
const listing = ref(false)
const backupBusy = ref(false)
const focusId = ref<number | null>(null)
const backupFile = ref('')
const lastQuery = ref('')
let messageRequest = 0
let conversationRequest = 0
let conversationDebounce: ReturnType<typeof setTimeout> | undefined
const kinds: Record<string, string> = { TextMessage: '文本', ImageMessage: '图片', GIFMessage: '动图', FileMessage: '文件', VoiceMessage: '语音', HQVoiceMessage: '语音', SightMessage: '视频', ReferenceMessage: '引用', RichContentMessage: '图文', RecallCommandMessage: '撤回通知' }
const statuses: Record<string, string> = { finished: '范围拉取结束', queued: '等待中', running: '同步中', failed: '失败', cancelled: '已取消', interrupted: '已中断', warning: '需关注' }
function sourceParams() {
  const params = new URLSearchParams()
  if (sourceId.value !== null) params.set('source_id', String(sourceId.value))
  return params
}
function messageParams() {
  const params = sourceParams()
  if (mode.value === 'conversations' && selected.value) params.set('conversation_id', String(selected.value.id))
  if (q.value) params.set('q', q.value)
  if (sender.value) params.set('sender', sender.value)
  if (range.value) { params.set('start_time', String(Number(range.value[0]))); params.set('end_time', String(Number(range.value[1]))) }
  return params
}
async function loadMessages(focus?: number, reuseFilters = false) {
  const request = ++messageRequest
  if (mode.value === 'conversations' && !selected.value) { messages.value = []; messageTotal.value = 0; busy.value = false; return }
  busy.value = true
  const params = reuseFilters ? new URLSearchParams(lastQuery.value) : messageParams()
  params.delete('focus_id')
  params.set('page', String(messagePage.value))
  params.set('page_size', '50')
  if (focus) params.set('focus_id', String(focus))
  try {
    const data = await api(`archive/messages?${params}`)
    if (request !== messageRequest) return
    messages.value = data.list; messageTotal.value = data.total; messagePage.value = data.page
    lastQuery.value = params.toString()
    await nextTick()
    if (focus) document.getElementById(`message-${focus}`)?.scrollIntoView({ block: 'center' })
  } catch (error) { if (request === messageRequest) { messages.value = []; messageTotal.value = 0; ElMessage.error((error as Error).message) } }
  finally { if (request === messageRequest) busy.value = false }
}
async function loadConversations() {
  clearTimeout(conversationDebounce)
  const request = ++conversationRequest
  listing.value = true
  const params = sourceParams()
  params.set('page', String(conversationPage.value)); params.set('page_size', '20')
  if (conversationQuery.value) params.set('q', conversationQuery.value)
  if (conversationType.value) params.set('conversation_type', String(conversationType.value))
  try {
    const data = await api(`archive/conversations?${params}`)
    if (request !== conversationRequest) return
    conversations.value = data.list; conversationTotal.value = data.total
    if (!selected.value && conversations.value.length) choose(conversations.value[0]!)
  } catch (error) { if (request === conversationRequest) ElMessage.error((error as Error).message) }
  finally { if (request === conversationRequest) listing.value = false }
}
function searchConversations() {
  clearTimeout(conversationDebounce)
  conversationDebounce = setTimeout(() => { conversationPage.value = 1; loadConversations() }, 250)
}
function apply() { messagePage.value = 1; focusId.value = null; loadMessages() }
function clearFilters() { q.value = ''; sender.value = ''; range.value = null; apply() }
function choose(c: Conversation) { selected.value = c; messagePage.value = 1; focusId.value = null; loadMessages() }
function switchMode() { messagePage.value = 1; focusId.value = null; loadMessages() }
async function changeSource() {
  selected.value = null; messages.value = []; messageTotal.value = 0; conversationPage.value = 1; conversationQuery.value = ''; conversationType.value = 0
  conversations.value = []; conversationTotal.value = 0
  q.value = ''; sender.value = ''; range.value = null; focusId.value = null; messagePage.value = 1
  ++messageRequest
  await loadConversations()
  if (mode.value === 'search') loadMessages()
}
function locate(message: Message) {
  mode.value = 'conversations'
  q.value = ''; sender.value = ''; range.value = null
  selected.value = { id: message.conversation_id, title: message.conversation_title, conversation_type: message.conversation_type }
  focusId.value = message.id
  loadMessages(message.id)
}
function mediaName(message: Message) {
  const content = message.content.content
  return typeof content === 'object' && content !== null && 'name' in content ? String(content.name) : ''
}
function download(url: string) {
  const link = document.createElement('a')
  link.href = url; link.click()
}
function exportData(format: string) {
  if (!messageTotal.value) return
  const params = new URLSearchParams(lastQuery.value)
  params.delete('page'); params.delete('page_size'); params.delete('focus_id'); params.set('format', format)
  download(`/api/archive/export?${params}`)
}
async function backup() {
  if (backupBusy.value) return
  backupBusy.value = true
  try {
    await ElMessageBox.confirm('备份包含全部来源的归档聊天及同步任务，文件仅保存在本机。', '备份数据库', { confirmButtonText: '创建备份', cancelButtonText: '返回' })
    const data = await api('archive/backups', { method: 'POST' })
    backupFile.value = data.filename
    download(`/api/archive/backups/${data.filename}`)
    ElMessage.success('数据库备份已生成')
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message) }
  finally { backupBusy.value = false }
}
onMounted(async () => {
  try {
    const data = await api('archive/sources')
    sources.value = data.list
    sourceId.value = sources.value.find(source => source.account_key === data.current_account_key)?.id ?? null
    await loadConversations()
  } catch (error) { ElMessage.error((error as Error).message) }
})
onUnmounted(() => { ++messageRequest; ++conversationRequest; clearTimeout(conversationDebounce) })
</script>

<template>
  <section class="archive-toolbar">
    <el-select v-model="sourceId" placeholder="当前来源（暂无归档）" aria-label="归档来源" @change="changeSource"><el-option v-for="source in sources" :key="source.id" :value="source.id" :label="`${source.label} / ${source.account_key}`"/></el-select>
    <el-radio-group v-model="mode" @change="switchMode"><el-radio-button value="conversations">会话浏览</el-radio-button><el-radio-button value="search">全局搜索</el-radio-button></el-radio-group>
    <el-button :loading="backupBusy" @click="backup"><DatabaseBackup :size="16"/>备份数据库</el-button>
  </section>
  <p v-if="backupFile" class="backup-file">最新备份：{{backupFile}}</p>
  <div class="archive-layout" :class="{'search-layout': mode==='search'}">
    <div v-if="mode==='conversations'" class="conversation-pane">
      <div class="pane-heading"><h2>已归档会话</h2><el-button title="刷新会话" aria-label="刷新会话" :loading="listing" @click="loadConversations"><RefreshCw :size="16"/></el-button></div>
      <el-input v-model="conversationQuery" placeholder="搜索会话名称" clearable @input="searchConversations" @keyup.enter="conversationPage=1; loadConversations()" @clear="conversationPage=1; loadConversations()"><template #prefix><Search :size="16"/></template></el-input>
      <el-radio-group v-model="conversationType" class="conversation-types" @change="conversationPage=1; loadConversations()"><el-radio-button :value="0">全部</el-radio-button><el-radio-button :value="1">私聊</el-radio-button><el-radio-button :value="3">群聊</el-radio-button></el-radio-group>
      <div v-if="!conversations.length" class="empty"><MessagesSquare :size="24"/><span>{{listing ? '加载中' : '暂无已归档会话'}}</span></div>
      <button v-for="c in conversations" :key="c.id" class="conversation-choice" :class="{selected:selected?.id===c.id}" @click="choose(c)"><component :is="c.conversation_type===3 ? UsersRound : UserRound" :size="19"/><div><strong>{{c.title}}</strong><small>{{c.message_count}} 条 · {{statuses[c.sync_status || ''] || '未同步'}}</small><small>{{c.latest_time_readable || '—'}}</small></div></button>
      <el-pagination v-if="conversationTotal>20" v-model:current-page="conversationPage" :page-size="20" :total="conversationTotal" layout="prev, next" @current-change="loadConversations"/>
    </div>
    <div class="message-pane">
      <div class="pane-heading"><h2>{{mode==='search' ? '全局搜索' : selected?.title || '聊天记录'}}</h2><span class="muted">{{messageTotal}} 条</span></div>
      <div class="message-filters"><el-input v-model="q" placeholder="消息关键词" clearable @keyup.enter="apply" @clear="apply"/><el-input v-model="sender" placeholder="发送者姓名或 ID" clearable @keyup.enter="apply" @clear="apply"/><el-date-picker v-model="range" type="datetimerange" value-format="x" start-placeholder="开始时间" end-placeholder="结束时间"/><div class="filter-actions"><el-button type="primary" @click="apply"><Search :size="16"/>查询</el-button><el-button @click="clearFilters">重置</el-button><el-dropdown @command="exportData"><el-button :disabled="!messageTotal || busy"><Download :size="16"/>导出</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="json">JSON</el-dropdown-item><el-dropdown-item command="csv">CSV</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div></div>
      <div v-if="busy" class="empty">加载中</div>
      <div v-else-if="!messages.length" class="empty"><MessagesSquare :size="28"/><span>{{mode==='conversations' && !selected ? '尚未选择会话' : '没有匹配消息'}}</span></div>
      <div v-else class="message-list"><article v-for="message in messages" :id="`message-${message.id}`" :key="message.id" class="message-row" :class="{focused:message.id===focusId}"><div class="message-meta"><strong>{{message.sender_name || message.sender_id || '未知发送者'}}</strong><time>{{message.sent_time_readable || '未提供可读时间'}}</time><span class="message-kind">{{kinds[message.message_type] || message.message_type}}</span></div><button v-if="mode==='search'" class="result-conversation" @click="locate(message)">{{message.conversation_title}} · 查看上下文</button><p v-if="message.text_content" class="message-text">{{message.text_content}}</p><p v-else class="message-text media-summary">{{mediaName(message) || kinds[message.message_type] || message.message_type}}</p><details v-if="message.message_type!=='TextMessage'"><summary>消息元信息</summary><pre>{{JSON.stringify(message.content, null, 2)}}</pre></details></article></div>
      <el-pagination v-if="messageTotal>50" v-model:current-page="messagePage" :page-size="50" :total="messageTotal" layout="prev, pager, next" @current-change="focusId=null; loadMessages(undefined,true)"/>
    </div>
  </div>
</template>
