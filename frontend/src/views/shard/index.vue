<template>
  <section class="page" data-module="shard">
    <header class="page-head">
      <div>
        <h2>任务分片调度管理</h2>
        <p class="page-desc">维护任务分片的范围划分、节点分配与重试控制；读取续采占用结果，展示断点所属团队与唯一续采批次，并绑定恢复批次的分片范围。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记分片任务</button>
        <button class="btn" type="button" @click="exportRows">导出任务分片调度清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="panel">
      <h3>断点计划归属汇总清单</h3>
      <p class="hint">由续采占用结果数据驱动；同一断点位置只会有一个生效中的续采批次，旧批次保留原团队依据。</p>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in summaryColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in resumeSummary" :key="index">
            <td v-for="column in summaryColumns" :key="column">{{ row[column] || '—' }}</td>
          </tr>
          <tr v-if="!resumeSummary.length">
            <td :colspan="summaryColumns.length" class="empty-state">暂无续采占用结果，启动续采后此处自动生成汇总</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>断点计划归属待办</h3>
      <p class="hint">其余业务入口登记断点或变更续采占用结果后，待办会一并更新。</p>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in todoColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in resumeTodos" :key="index">
            <td v-for="column in todoColumns" :key="column">{{ row[column] || '—' }}</td>
          </tr>
          <tr v-if="!resumeTodos.length">
            <td :colspan="todoColumns.length" class="empty-state">暂无待办，所有断点计划归属均已处理</td>
          </tr>
        </tbody>
      </table>
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
            <template v-if="column === '处理状态'">{{ row.status ?? '—' }}</template>
            <template v-else>{{ row[column] || '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
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
          <td :colspan="columns.length + 1" class="empty-state">暂无任务分片调度数据，可先登记分片任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条任务分片调度记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/shard'
const columns = ["分片编号", "采集计划", "分片范围", "断点所属团队", "执行团队", "续采批次", "分配节点", "处理状态", "分片状态"]
const summaryColumns = ["采集计划", "断点编号", "断点所属团队", "唯一续采批次", "执行团队", "分片范围", "处理状态", "原团队依据", "交接说明"]
const todoColumns = ["待办类型", "采集计划", "断点编号", "所属团队", "说明"]
const actions = ["分配节点", "重试分片", "标记完成"]
const stats = ref([{ label: "分片任务总数", value: 0 }, { label: "执行中分片", value: 0 }, { label: "已完成分片", value: 0 }, { label: "断点归属待办", value: 0 }])

const rows = ref<Row[]>([])
const resumeSummary = ref<Row[]>([])
const resumeTodos = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '分片任务登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '任务分片调度动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '任务分片调度操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const [listResponse, summaryResponse, todosResponse] = await Promise.all([
      request(`${ENDPOINT}?${query}`),
      request(`${ENDPOINT}/resume-summary`),
      request(`${ENDPOINT}/resume-todos`),
    ])
    if (!listResponse.ok) {
      throw new Error('分片任务列表读取失败')
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (summaryResponse.ok) {
      resumeSummary.value = (await summaryResponse.json()).items ?? []
    }
    if (todosResponse.ok) {
      resumeTodos.value = (await todosResponse.json()).items ?? []
    }
    stats.value = [
      { label: '分片任务总数', value: total.value },
      { label: '执行中分片', value: rows.value.filter((row) => row.status === '执行中').length },
      { label: '已完成分片', value: rows.value.filter((row) => row.status === '已完成').length },
      { label: '断点归属待办', value: resumeTodos.value.length },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '任务分片调度列表读取失败'
  }
}

onMounted(reload)
</script>
