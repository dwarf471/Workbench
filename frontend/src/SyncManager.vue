<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Download, RefreshCw, Search, UserRound, UsersRound, X, Play } from 'lucide-vue-next'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from './api'
type Candidate = { conversation_type: number; target_id: string; title: string }
type Item = Candidate & { id: number; status: string; pages: number; fetched: number; inserted: number; oldest: number | null; newest: number | null; message: string }
type Task = { id: number; status: string; source_label: string; account_key: string; start_time: number; end_time: number; created_at: string; message: string; cancel_requested: boolean; items: Item[] }
const candidates = ref<Candidate[]>([])
const selected = ref<Candidate[]>([])
const tasks = ref<Task[]>([])
const discovered = ref(false)
const discovering = ref(false)
const submitting = ref(false)
const keyword = ref('')
const page = ref(1)
const total = ref(0)
const taskPage = ref(1)
const taskTotal = ref(0)
const searchMode = ref(false)
const rangeMode = ref('30')
const range = ref<[number, number] | null>(null)
const confirmed = ref(false)
const source = ref({ account_label: '', account_key: '' })
const filterType = ref(0)
const visible = computed(() => candidates.value.filter(c => !filterType.value || c.conversation_type === filterType.value))
const statusNames: Record<string, string> = { queued: '等待中', running: '同步中', finished: '范围拉取结束', failed: '失败', cancelled: '已取消', interrupted: '已中断', warning: '需关注' }
let timer: ReturnType<typeof setInterval> | undefined
let polling = false
let alive = true
function key(c: Candidate) { return `${c.conversation_type}:${c.target_id}` }
function choose(c: Candidate, checked: string | number | boolean) {
  if (checked && selected.value.length >= 100 && !selected.value.some(item => key(item) === key(c))) { ElMessage.error('每个任务最多选择 100 个会话'); return }
  selected.value = selected.value.filter(item => key(item) !== key(c))
  if (checked) selected.value.push(c)
}
function time(value: number | null) { return value === null ? '—' : new Date(value).toLocaleString('zh-CN', { hour12: false }) }
async function discover(search = false) {
  if (discovering.value) return
  discovering.value = true
  try {
    searchMode.value = search
    const data = await api(search ? `discovery/contacts?keyword=${encodeURIComponent(keyword.value.trim())}` : `discovery/conversations?page=${page.value}&page_size=20`)
    candidates.value = data.list
    total.value = search ? data.list.length : data.total
    discovered.value = true
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { discovering.value = false }
}
async function refreshTasks(showError = false) {
  if (polling) return
  polling = true
  try {
    const data = await api(`sync/tasks?page=${taskPage.value}&page_size=20`)
    if (alive) { tasks.value = data.list; taskTotal.value = data.total }
  } catch (error) { if (showError) ElMessage.error((error as Error).message) }
  finally { polling = false }
}
async function create() {
  if (!selected.value.length || !confirmed.value) return
  if (rangeMode.value === 'custom' && !range.value) { ElMessage.error('请选择时间范围'); return }
  submitting.value = true
  try {
    const current = await api('settings')
    if (current.account_key !== source.value.account_key) { confirmed.value = false; throw new Error('来源账户已变化，请刷新页面重新确认') }
    const end = Date.now()
    const times = rangeMode.value === 'all' ? [0, end] : rangeMode.value === 'custom' ? range.value! : [end - 30 * 86400000, end]
    const task = await api('sync/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ account_key: source.value.account_key, conversations: selected.value, start_time: Number(times[0]), end_time: Number(times[1]), account_confirmed: confirmed.value }) })
    ElMessage.success(`任务 #${task.id} 已创建`)
    confirmed.value = false
    taskPage.value = 1
    await refreshTasks(true)
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { submitting.value = false }
}
async function action(task: Task, command: 'cancel' | 'resume') {
  try {
    if (command === 'resume') await ElMessageBox.confirm(`请确认新点当前登录的是「${task.source_label}」，来源标识 ${task.account_key}。`, '继续同步', { confirmButtonText: '确认并继续', cancelButtonText: '返回' })
    await api(`sync/tasks/${task.id}/${command}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ account_confirmed: true }) })
    await refreshTasks(true)
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error((error as Error).message) }
}
onMounted(async () => {
  try { source.value = await api('settings') } catch (error) { ElMessage.error((error as Error).message) }
  await refreshTasks(true)
  timer = setInterval(() => refreshTasks(), 2000)
})
onUnmounted(() => { alive = false; clearInterval(timer) })
</script>

<template>
  <section class="source-banner"><strong>{{ source.account_label }}</strong><span>来源标识：{{ source.account_key }}</span><el-tag effect="plain">手动同步</el-tag></section>
  <section>
    <div class="section-bar"><h2>选择来源会话</h2><el-button :loading="discovering" @click="page=1; discover()"><RefreshCw :size="16"/>发现会话</el-button></div>
    <div class="discover-toolbar"><el-input v-model="keyword" placeholder="联系人或群组名称" clearable @keyup.enter="keyword.trim() && discover(true)"><template #prefix><Search :size="16"/></template></el-input><el-button :disabled="!keyword.trim() || discovering" @click="discover(true)">查找</el-button><el-radio-group v-model="filterType"><el-radio-button :value="0">全部</el-radio-button><el-radio-button :value="1">私聊</el-radio-button><el-radio-button :value="3">群聊</el-radio-button></el-radio-group></div>
    <div v-if="!discovered" class="empty"><UsersRound :size="28"/><span>暂无来源会话</span></div>
    <div v-else-if="!visible.length" class="empty">未找到匹配会话</div>
    <div class="candidate-list"><div v-for="c in visible" :key="key(c)" class="candidate"><el-checkbox :model-value="selected.some(item => key(item) === key(c))" @change="(value: string | number | boolean) => choose(c, value)" :aria-label="`选择 ${c.title}`"/><component :is="c.conversation_type===3 ? UsersRound : UserRound" :size="20"/><div><strong>{{c.title}}</strong><small>{{c.conversation_type===3 ? '群聊' : '私聊'}} · {{c.target_id}}</small></div></div></div>
    <el-pagination v-if="discovered && !searchMode && total>20" v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" @current-change="discover(false)"/>
  </section>
  <section>
    <div class="section-bar"><h2>同步范围</h2><span class="muted">已选 {{selected.length}} 个会话</span></div>
    <div class="selection-tags"><el-tag v-for="c in selected" :key="key(c)" closable @close="choose(c,false)">{{c.title}}</el-tag></div>
    <el-radio-group v-model="rangeMode"><el-radio-button value="30">最近 30 天</el-radio-button><el-radio-button value="custom">指定时间</el-radio-button><el-radio-button value="all">全部可获取历史</el-radio-button></el-radio-group>
    <div v-if="rangeMode==='custom'" class="date-range"><el-date-picker v-model="range" type="datetimerange" value-format="x" start-placeholder="开始时间" end-placeholder="结束时间"/></div>
    <div class="confirm-row"><el-checkbox v-model="confirmed">确认新点当前登录账号与来源「{{source.account_key}}」一致</el-checkbox></div>
    <div class="section-bar"><span class="muted">仅归档 MCP 返回的数据，不保证完整云端历史。</span><el-button type="primary" :disabled="!selected.length || !confirmed || selected.length>100" :loading="submitting" @click="create"><Download :size="16"/>开始同步</el-button></div>
  </section>
  <section>
    <div class="section-bar"><h2>同步任务</h2><el-button title="刷新任务" aria-label="刷新任务" @click="refreshTasks(true)"><RefreshCw :size="16"/></el-button></div>
    <div v-if="!tasks.length" class="empty">暂无同步任务</div>
    <article v-for="task in tasks" :key="task.id" class="task-row">
      <div class="task-heading"><strong>任务 #{{task.id}}</strong><el-tag :type="task.status==='failed' ? 'danger' : task.status==='finished' ? 'success' : 'info'">{{statusNames[task.status]}}</el-tag><span class="muted">{{task.source_label}} / {{task.account_key}}</span><div class="task-actions"><el-button v-if="['queued','running'].includes(task.status)" :disabled="task.cancel_requested" @click="action(task,'cancel')"><X :size="16"/>{{task.cancel_requested ? '取消中' : '取消'}}</el-button><el-button v-if="['failed','cancelled','interrupted','warning'].includes(task.status)" @click="action(task,'resume')"><Play :size="16"/>继续 / 重试</el-button></div></div>
      <p class="muted task-range">{{task.start_time ? time(task.start_time) : '全部可获取历史'}} — {{time(task.end_time)}}</p>
      <p v-if="task.message" class="task-error">{{task.message}}</p>
      <div v-for="item in task.items" :key="item.id" class="item-progress"><div class="item-title"><strong>{{item.title}}</strong><span>{{statusNames[item.status]}}</span></div><div class="item-stats">{{item.pages}} 页 · 拉取 {{item.fetched}} 条 · 新增 {{item.inserted}} 条</div><small class="muted">实际范围：{{time(item.oldest)}} — {{time(item.newest)}}</small><p v-if="item.message" class="task-error">{{item.message}}</p></div>
    </article>
    <el-pagination v-if="taskTotal>20" v-model:current-page="taskPage" :page-size="20" :total="taskTotal" layout="prev, pager, next" @current-change="refreshTasks(true)"/>
  </section>
</template>
