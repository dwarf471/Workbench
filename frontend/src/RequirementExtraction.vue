<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { FileSearch, Eye, Square, Save, ClipboardCheck } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { api } from './api'
type Scope = { source_id: number; conversation_id: number; start_time: number; end_time: number }
type Candidate = { id: number; summary: string; uncertainties: string; selected: boolean; evidence_ids: number[] }
type Evidence = { id: number; time: string; sender: string; direction: string; text: string }
type Receipt = { added: number; skipped: number; items: {candidate_id: number; requirement_id: string; duplicate: boolean}[] }
type Task = Scope & { id: number; status: string; total_messages: number; text_messages: number; skipped_messages: number; completed_chunks: number; total_chunks: number; conversation_title: string; source_label: string; base_url: string; model: string; error: string; created_at: string; candidates?: Candidate[]; evidence?: Evidence[]; registration?: Receipt }
type Plan = { task_id: number; expected_updated_at: string; added: number; skipped: number; rows: (Candidate & {duplicate_id: string; duplicate_candidate_id: number | null; similar: {id: string; summary: string}[]})[] }
const props = defineProps<{ scope: Scope | null; sourceId: number | null; conversationId: number | null }>()
const tasks = ref<Task[]>([])
const draft = ref<Task | null>(null)
const current = ref<Task | null>(null)
const confirmed = ref(false)
const confirmOpen = ref(false)
const previewOpen = ref(false)
const preparing = ref(false)
const sending = ref(false)
const saving = ref(false)
const registrationOpen = ref(false)
const registrationPlan = ref<Plan | null>(null)
const registrationConfirmed = ref(false)
const registering = ref(false)
const preparingRegistration = ref(false)
const registrationError = ref('')
let generation = 0
let disposed = false
const statuses: Record<string, string> = { queued: '等待中', running: '分析中', finished: '待人工评审', failed: '失败', cancelled: '已取消', interrupted: '已中断' }
function time(value: number | string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
function running(task: Task) { return task.status === 'queued' || task.status === 'running' }
function registered(task: Task) { return !!task.registration?.items }
async function loadTasks() {
  const request = generation
  if (!props.sourceId || !props.conversationId) { tasks.value = []; return }
  try {
    const data = await api(`extraction/tasks?source_id=${props.sourceId}&conversation_id=${props.conversationId}`)
    if (!disposed && request === generation) tasks.value = data.list
  } catch (error) { if (!disposed && request === generation) ElMessage.error((error as Error).message) }
}
async function open() {
  if (!props.scope || preparing.value) return
  const request = generation
  preparing.value = true
  try {
    const data = await api('extraction/prepare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(props.scope) })
    if (disposed || request !== generation) return
    draft.value = data; confirmed.value = false; confirmOpen.value = true
  } catch (error) { if (!disposed) ElMessage.error((error as Error).message) }
  finally { preparing.value = false }
}
async function start() {
  if (!draft.value || !confirmed.value || sending.value) return
  sending.value = true
  try {
    current.value = await api(`extraction/tasks/${draft.value.id}/start`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmed: true }) })
    confirmOpen.value = false; previewOpen.value = true
    await loadTasks()
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { sending.value = false }
}
async function inspect(task: Task) {
  try { current.value = await api(`extraction/tasks/${task.id}`); registrationError.value = ''; previewOpen.value = true }
  catch (error) { ElMessage.error((error as Error).message) }
}
async function cancel(task: Task) {
  try {
    const data = await api(`extraction/tasks/${task.id}/cancel`, { method: 'POST' })
    if (current.value?.id === task.id) current.value = data
    await loadTasks()
  } catch (error) { ElMessage.error((error as Error).message) }
}
async function save(notify = true) {
  if (!current.value || saving.value) return
  saving.value = true
  try {
    const rows = (current.value.candidates || []).map(({ id, summary, uncertainties, selected }) => ({ id, summary, uncertainties, selected }))
    current.value = await api(`extraction/tasks/${current.value.id}/review`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows }) })
    if (notify) ElMessage.success('候选草稿已保存')
    return true
  } catch (error) { ElMessage.error((error as Error).message); return false }
  finally { saving.value = false }
}
async function prepareRegistration() {
  if (!current.value || preparingRegistration.value) return
  preparingRegistration.value = true
  try {
    if (!(await save(false)) || !current.value) return
    registrationPlan.value = await api(`extraction/tasks/${current.value.id}/registration-preview`)
    registrationConfirmed.value = false; registrationOpen.value = true
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { preparingRegistration.value = false }
}
async function register() {
  if (!registrationPlan.value || !registrationConfirmed.value || registering.value) return
  registering.value = true
  try {
    const plan = registrationPlan.value
    const result = await api(`extraction/tasks/${plan.task_id}/register`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmed: true, expected_updated_at: plan.expected_updated_at }) })
    registrationOpen.value = false
    registrationError.value = result.error || ''
    if (current.value?.id === plan.task_id) current.value.registration = result.receipt
    if (current.value?.id === plan.task_id) current.value = await api(`extraction/tasks/${plan.task_id}`)
    await loadTasks()
    if (result.file_ready) ElMessage.success(`登记完成：新增 ${result.receipt.added} 项，重复跳过 ${result.receipt.skipped} 项`)
    else ElMessage.warning(result.error)
  } catch (error) { ElMessage.error((error as Error).message) }
  finally { registering.value = false }
}
let polling = false
const timer = setInterval(async () => {
  if (polling || disposed) return
  polling = true
  try {
    if (tasks.value.some(running) || (current.value && running(current.value))) await loadTasks()
    if (previewOpen.value && current.value && running(current.value)) {
      const id = current.value.id
      const data = await api(`extraction/tasks/${id}`)
      if (!disposed && previewOpen.value && current.value?.id === id) current.value = data
    }
  } catch (error) { if (!disposed) ElMessage.error((error as Error).message) }
  finally { polling = false }
}, 2000)
watch(() => [props.sourceId, props.conversationId], () => {
  generation++; tasks.value = []; confirmOpen.value = false; previewOpen.value = false; registrationOpen.value = false; registrationError.value = ''; current.value = null
  loadTasks()
}, { immediate: true })
onUnmounted(() => { disposed = true; generation++; clearInterval(timer) })
defineExpose({ open, preparing })
</script>

<template>
  <section v-if="tasks.length" class="extraction-tasks">
    <div class="section-heading"><FileSearch :size="18"/><h2>需求提取任务</h2></div>
    <article v-for="task in tasks" :key="task.id" class="task-row">
      <div class="task-heading"><strong>#{{task.id}}</strong><el-tag :type="task.status==='failed' ? 'danger' : task.status==='finished' ? 'success' : 'info'">{{registered(task) ? '已登记' : statuses[task.status] || task.status}}</el-tag><span class="muted">{{task.completed_chunks}} / {{task.total_chunks}} 段</span><div class="task-actions"><el-button @click="inspect(task)"><Eye :size="16"/>查看结果</el-button><el-button v-if="running(task)" @click="cancel(task)"><Square :size="14"/>取消</el-button></div></div>
      <p class="muted">{{time(task.start_time)}} 至 {{time(task.end_time)}} · {{task.text_messages}} 条文本</p>
      <p v-if="task.error" class="task-error">{{task.error}}</p>
    </article>
  </section>
  <el-dialog v-model="confirmOpen" title="确认提取需求" width="min(720px, calc(100vw - 24px))" :close-on-click-modal="false" :show-close="!sending" :close-on-press-escape="!sending">
    <template v-if="draft">
      <dl class="extraction-meta"><dt>会话</dt><dd>{{draft.source_label}} / {{draft.conversation_title}}</dd><dt>时间范围</dt><dd>{{time(draft.start_time)}} 至 {{time(draft.end_time)}}</dd><dt>消息快照</dt><dd>{{draft.total_messages}} 条消息，{{draft.text_messages}} 条文本，{{draft.skipped_messages}} 条无文本消息未分析</dd><dt>模型</dt><dd>{{draft.model}}</dd><dt>发送地址</dt><dd>{{draft.base_url}}</dd></dl>
      <el-alert title="提取包含范围内全部发出和收到的文本，不沿用关键词、发送者过滤，也不限于当前页。图片、语音和附件实体不解析。" type="info" :closable="false"/>
      <el-alert v-if="draft.base_url.startsWith('http://')" title="HTTP 明文传输，聊天文本和 API Key 可能被截获。" type="warning" :closable="false"/>
      <div class="confirm-row"><el-checkbox v-model="confirmed" :disabled="sending">确认将以上范围的聊天文本发送至该模型服务，可能产生调用费用</el-checkbox></div>
    </template>
    <template #footer><el-button :disabled="sending" @click="confirmOpen=false">返回</el-button><el-button type="primary" :disabled="!confirmed" :loading="sending" @click="start"><FileSearch :size="16"/>开始分析</el-button></template>
  </el-dialog>
  <el-dialog v-model="previewOpen" title="需求候选预览" width="min(900px, calc(100vw - 24px))" :close-on-click-modal="false" :show-close="!registering && !preparingRegistration" :close-on-press-escape="!registering && !preparingRegistration">
    <template v-if="current">
      <p class="extraction-wrap">{{current.conversation_title}} · {{time(current.start_time)}} 至 {{time(current.end_time)}}</p>
      <p class="muted">{{statuses[current.status] || current.status}} · {{current.completed_chunks}} / {{current.total_chunks}} 段 · 无文本消息未分析 {{current.skipped_messages}} 条</p>
      <el-alert v-if="current.error" :title="current.error" type="error" :closable="false"/>
      <el-alert v-else-if="current.status==='cancelled'" title="任务已取消；已经发送给模型的内容无法撤回。" type="warning" :closable="false"/>
      <el-alert v-if="current.status==='finished' && !registered(current)" title="核对并勾选需要备案的需求，再确认登记到需求清单。" type="info" :closable="false"/>
      <el-alert v-if="registered(current)" :title="`已登记：新增 ${current.registration?.added} 项，重复跳过 ${current.registration?.skipped} 项，候选已锁定。`" type="success" :closable="false"/>
      <el-alert v-if="registrationError" :title="registrationError" type="warning" :closable="false"/>
      <p v-if="current.status==='finished' && !current.candidates?.length" class="empty">所选文本中未提取到开发需求</p>
      <div v-for="row in current.candidates || []" :key="row.id" class="requirement-row">
        <el-checkbox v-model="row.selected" :disabled="registered(current) || saving || preparingRegistration || registering || registrationOpen" :aria-label="`选择候选 ${row.id}`">候选 {{row.id}}</el-checkbox>
        <el-form label-position="top" :disabled="registered(current) || saving || preparingRegistration || registering || registrationOpen"><el-form-item label="需求概述"><el-input v-model="row.summary" type="textarea" :rows="2" maxlength="1000"/></el-form-item><el-form-item label="待确认事项"><el-input v-model="row.uncertainties" type="textarea" :rows="2" maxlength="2000"/></el-form-item></el-form>
        <p v-if="registered(current) && row.selected" class="muted">备案编号：{{current.registration?.items.find(item=>item.candidate_id===row.id)?.requirement_id || '—'}}</p>
        <details><summary>来源消息（{{row.evidence_ids.length}} 条）</summary><article v-for="message in (current.evidence || []).filter(message=>row.evidence_ids.includes(message.id))" :key="message.id" class="extraction-evidence"><small>#{{message.id}} · {{message.time}} · {{message.sender}} · {{message.direction}}</small><p>{{message.text}}</p></article></details>
      </div>
    </template>
    <template #footer><div class="preview-actions"><el-button v-if="current && running(current)" @click="cancel(current)"><Square :size="14"/>取消任务</el-button><el-button v-if="current?.status==='finished' && current.candidates?.length && !registered(current)" :loading="saving" :disabled="preparingRegistration || registering || registrationOpen" @click="save()"><Save :size="16"/>保存候选草稿</el-button><el-button v-if="current?.status==='finished' && !registered(current)" type="primary" :loading="preparingRegistration" :disabled="saving || registering || !current.candidates?.some(row=>row.selected) || registrationOpen" @click="prepareRegistration"><ClipboardCheck :size="16"/>登记需求</el-button><el-button :disabled="registering || preparingRegistration" @click="previewOpen=false">关闭</el-button></div></template>
  </el-dialog>
  <el-dialog v-model="registrationOpen" title="确认登记需求" width="min(760px, calc(100vw - 24px))" :close-on-click-modal="false" :show-close="!registering" :close-on-press-escape="!registering">
    <template v-if="registrationPlan">
      <p class="extraction-wrap">预计新增 {{registrationPlan.added}} 项，重复跳过 {{registrationPlan.skipped}} 项</p>
      <p class="extraction-wrap muted">data/requirements/需求清单.csv · 全部来源的统一清单</p>
      <article v-for="row in registrationPlan.rows" :key="row.id" class="requirement-row"><strong>候选 {{row.id}}</strong><p class="extraction-wrap">{{row.summary}}</p><p v-if="row.uncertainties" class="extraction-wrap muted">待确认：{{row.uncertainties}}</p><el-alert v-if="row.duplicate_id || row.duplicate_candidate_id" :title="`完全重复，将跳过（${row.duplicate_id || '候选 ' + row.duplicate_candidate_id}）`" type="info" :closable="false"/><template v-if="row.similar.length"><el-alert title="发现疑似相似需求，请核对；需要取消该项时返回修改勾选。" type="warning" :closable="false"/><p v-for="similar in row.similar" :key="similar.id" class="extraction-wrap muted">{{similar.id}}：{{similar.summary}}</p></template></article>
      <div class="confirm-row"><el-checkbox v-model="registrationConfirmed" :disabled="registering">已核对选中需求，确认备案；登记后本任务锁定，未勾选项不登记</el-checkbox></div>
    </template>
    <template #footer><el-button :disabled="registering" @click="registrationOpen=false">返回修改</el-button><el-button type="primary" :disabled="!registrationConfirmed" :loading="registering" @click="register"><ClipboardCheck :size="16"/>确认登记</el-button></template>
  </el-dialog>
</template>

<style scoped>
.extraction-meta{margin:0 0 20px;grid-template-columns:80px minmax(0,1fr);gap:12px}.extraction-meta dd,.extraction-wrap{overflow-wrap:anywhere}.el-alert{margin-top:12px}.requirement-row{border-top:1px solid #dfe5e8;padding:20px 0}.requirement-row .el-form{margin-top:12px}.requirement-row summary{cursor:pointer;color:#397caf}.extraction-evidence{padding:12px 0;border-bottom:1px solid #e1e7e9}.extraction-evidence small{color:#6a7f89;overflow-wrap:anywhere}.extraction-evidence p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}.extraction-tasks .muted{overflow-wrap:anywhere}
.preview-actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}.preview-actions .el-button{margin:0}
</style>
