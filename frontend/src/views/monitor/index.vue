<template>
  <section class="page" data-module="monitor">
    <header class="page-head">
      <div>
        <h2>采集监控管理</h2>
        <p class="page-desc">
          监控读取响应校验数据集写下的同一检测窗口结论：每个检测窗口只有一条可解释结果，
          重复刷新不会出现重复计数。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记监控指标</button>
        <button class="btn" type="button" @click="exportRows">导出采集监控清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <h3 class="section-title">检测窗口监控结果</h3>
    <p class="page-desc">与响应校验共用同一份窗口结论，不按样本重复计数。</p>
    <form class="filter-bar" @submit.prevent="reloadWindows">
      <label class="filter-item">
        <span>检测窗口</span>
        <input v-model="windowFilters.keyword" placeholder="按检测窗口检索" />
      </label>
      <label class="filter-item">
        <span>窗口状态</span>
        <select v-model="windowFilters.status">
          <option value="">全部状态</option>
          <option value="开启">开启</option>
          <option value="已关闭">已关闭</option>
        </select>
      </label>
      <label class="filter-item">
        <span>结论</span>
        <select v-model="windowFilters.conclusion">
          <option value="">全部结论</option>
          <option value="通过">通过</option>
          <option value="告警">告警</option>
          <option value="阻断">阻断</option>
          <option value="待判定">待判定</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetWindowFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in windowColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in windowRows" :key="String(row['检测窗口'])">
          <td v-for="column in windowColumns" :key="column">{{ row[column] ?? '—' }}</td>
        </tr>
        <tr v-if="!windowRows.length">
          <td :colspan="windowColumns.length" class="empty-state">暂无检测窗口结论，响应校验形成结论后会出现在这里</td>
        </tr>
      </tbody>
    </table>
    <footer class="page-foot">
      <span>共 {{ windowTotal }} 条检测窗口结果</span>
    </footer>

    <h3 class="section-title">监控指标</h3>
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
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无采集监控数据，可先登记监控指标</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集监控记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, string | number | null>
type WindowRow = Record<string, string | number | null>

const ENDPOINT = '/api/monitor'
const columns = ["指标编号", "关联端点", "成功率阈值", "延迟阈值", "错误率告警", "检测频率", "通知渠道", "静默分钟"]
const windowColumns = ["检测窗口", "窗口状态", "结论", "阻断样本数", "告警样本数", "待判定样本数", "结论版本", "结论说明"]
const actions = ["提交校验", "启用监控", "停用监控"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]

const rows = ref<Row[]>([])
const total = ref(0)
const windowRows = ref<WindowRow[]>([])
const windowTotal = ref(0)
const windowFilters = ref({ keyword: '', status: '', conclusion: '' })
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const stats = computed(() => {
  const blocked = windowRows.value.filter((row) => row['结论'] === '阻断').length
  return [
    { label: '监控指标总数', value: total.value },
    { label: '检测窗口结果', value: windowTotal.value },
    { label: '阻断窗口', value: blocked },
  ]
})

function resetFilters() {
  filters.value = {}
  void reload()
}

function resetWindowFilters() {
  windowFilters.value = { keyword: '', status: '', conclusion: '' }
  void reloadWindows()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '监控指标登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('采集监控动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集监控操作失败'
  }
}

async function reloadWindows() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  Object.entries(windowFilters.value).forEach(([key, value]) => {
    if (value) params.set(key, value)
  })
  try {
    const payload = await fetchJson<{ items: WindowRow[]; total: number }>(
      `${ENDPOINT}/windows?${params.toString()}`,
    )
    windowRows.value = payload.items ?? []
    windowTotal.value = payload.total ?? windowRows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测窗口监控结果读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('监控指标列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集监控列表读取失败'
  }
}

onMounted(async () => {
  await Promise.all([reload(), reloadWindows()])
})
</script>
