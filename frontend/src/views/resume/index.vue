<template>
  <section class="page" data-module="resume">
    <header class="page-head">
      <div>
        <h2>断点续采恢复管理</h2>
        <p class="page-desc">维护断点位置、采集计划归属与已采集量，启动续采前校验恢复权限并仲裁断点位置占用，同一断点位置只授予一个续采批次。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="showCreate = !showCreate">登记断点记录</button>
        <button class="btn" type="button" @click="exportRows">导出断点续采恢复清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div v-if="showCreate" class="panel">
      <h3>登记断点记录</h3>
      <p class="hint">登记后归属待办会同步到分片调度；所属团队决定谁拥有恢复权限。</p>
      <div class="form-row">
        <label><span>断点编号</span><input v-model="createForm.断点编号" placeholder="RESU-0004" /></label>
        <label><span>采集计划</span><input v-model="createForm.采集计划" placeholder="计划名称" /></label>
        <label><span>断点位置</span><input v-model="createForm.断点位置" placeholder="如 20000" /></label>
        <label><span>剩余量</span><input v-model="createForm.剩余量" placeholder="如 5000" /></label>
        <label><span>所属团队</span>
          <select v-model="createForm.所属团队">
            <option v-for="team in session.teams" :key="team" :value="team">{{ team }}</option>
          </select>
        </label>
        <button class="btn primary" type="button" @click="submitCreate">提交登记</button>
        <button class="btn ghost" type="button" @click="showCreate = false">取消</button>
      </div>
    </div>

    <div v-if="actionPanel" class="panel">
      <h3>{{ actionPanel.action }} · {{ actionPanel.row.断点编号 }}</h3>
      <div v-if="actionPanel.action === '失败释放'" class="form-row">
        <label><span>本次已用量</span><input v-model.number="actionForm.本次已用" type="number" min="0" placeholder="失败前已采集量" /></label>
        <span class="hint">只释放本次未使用范围，断点水位按已用量前进、不回退。</span>
      </div>
      <div v-else-if="actionPanel.action === '转移归属'" class="form-row">
        <label><span>新归属团队</span>
          <select v-model="actionForm.新团队">
            <option v-for="team in session.teams" :key="team" :value="team">{{ team }}</option>
          </select>
        </label>
        <label><span>交接方式</span>
          <select v-model="actionForm.交接方式">
            <option value="原团队完成当前批次">原团队完成当前批次</option>
            <option value="新团队接管当前批次">新团队接管当前批次</option>
          </select>
        </label>
        <span class="hint">方式一：分片调度仍显示原团队执行；方式二：新团队接管并留下责任交接记录。</span>
      </div>
      <div class="form-row" style="margin-top: 8px">
        <button class="btn primary" type="button" @click="confirmAction">确认{{ actionPanel.action }}</button>
        <button class="btn ghost" type="button" @click="actionPanel = null">取消</button>
      </div>
    </div>

    <div v-if="historyPanel" class="panel">
      <h3>断点历史 · {{ historyPanel.断点编号 }}</h3>
      <template v-if="!historyPanel.只读">
        <ul class="history-list">
          <li v-for="(item, index) in historyPanel.断点历史" :key="index">
            <strong>{{ item.时间 }}</strong> · {{ item.事件 }}：{{ item.说明 }}
          </li>
        </ul>
        <p v-if="!historyPanel.断点历史 || !historyPanel.断点历史.length" class="hint">暂无历史记录</p>
      </template>
      <p v-else class="hint">{{ historyPanel.提示 }}</p>
      <div class="form-row" style="margin-top: 8px">
        <button class="btn ghost" type="button" @click="historyPanel = null">收起</button>
      </div>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '所属团队'">
              {{ row[column] || '—' }}
              <span v-if="row[column] !== session.team" class="tag readonly">越权只读</span>
            </template>
            <template v-else-if="column === '续采批次'">{{ row[column] || '—' }}</template>
            <template v-else-if="column === '处理状态'">{{ row.status ?? '—' }}</template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无断点续采恢复数据，可先登记断点记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条断点续采恢复记录 · 当前团队 {{ session.team }}</span>
      <span v-if="noticeMessage" class="hint">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, any>

const ENDPOINT = '/api/resume'
const session = useSessionStore()
const columns = ["断点编号", "采集计划", "断点位置", "已采集量", "剩余量", "所属团队", "续采批次", "占用状态", "处理状态", "记录状态"]
const stats = ref([{ label: "断点记录总数", value: 0 }, { label: "恢复中断点", value: 0 }, { label: "已恢复断点", value: 0 }])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const createForm = ref<Record<string, string>>({ 断点编号: '', 采集计划: '', 断点位置: '', 剩余量: '', 所属团队: session.team })
const actionPanel = ref<{ action: string; row: Row } | null>(null)
const actionForm = ref<Record<string, any>>({ 本次已用: 0, 新团队: '', 交接方式: '原团队完成当前批次' })
const historyPanel = ref<Row | null>(null)

function availableActions(row: Row): string[] {
  const actions: string[] = []
  if (row.status === '已记录') actions.push('启动续采')
  if (row.status === '已记录' && row.续采批次) actions.push('重试续采')
  if (row.status === '恢复中') actions.push('失败释放', '完成批次')
  actions.push('转移归属')
  if (row.status === '已记录' && !row.续采批次) actions.push('放弃恢复')
  actions.push('查看历史')
  return actions
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function submitCreate() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm.value } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '断点记录登记失败')
    }
    noticeMessage.value = payload.message
    showCreate.value = false
    createForm.value = { 断点编号: '', 采集计划: '', 断点位置: '', 剩余量: '', 所属团队: session.team }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '断点记录登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  if (action === '查看历史') {
    await openHistory(row)
    return
  }
  if (action === '失败释放' || action === '转移归属') {
    actionForm.value = { 本次已用: 0, 新团队: session.teams.find((team) => team !== row.所属团队) ?? '', 交接方式: '原团队完成当前批次' }
    actionPanel.value = { action, row }
    return
  }
  await postAction(action, row, {})
}

async function confirmAction() {
  if (!actionPanel.value) return
  const { action, row } = actionPanel.value
  const extra: Record<string, any> = {}
  if (action === '失败释放') extra.本次已用 = Number(actionForm.value.本次已用) || 0
  if (action === '转移归属') {
    extra.新团队 = actionForm.value.新团队
    extra.交接方式 = actionForm.value.交接方式
  }
  actionPanel.value = null
  await postAction(action, row, extra)
}

async function postAction(action: string, row: Row, extra: Record<string, any>) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, 操作团队: session.team, ...extra } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '断点续采恢复动作未生效')
    }
    noticeMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '断点续采恢复操作失败'
  }
}

async function openHistory(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}?team=${encodeURIComponent(session.team)}`)
    if (!response.ok) {
      throw new Error('断点明细读取失败')
    }
    historyPanel.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '断点明细读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('断点记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = [
      { label: '断点记录总数', value: total.value },
      { label: '恢复中断点', value: rows.value.filter((row) => row.status === '恢复中').length },
      { label: '已恢复断点', value: rows.value.filter((row) => row.status === '已恢复').length },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '断点续采恢复列表读取失败'
  }
}

onMounted(reload)
</script>
