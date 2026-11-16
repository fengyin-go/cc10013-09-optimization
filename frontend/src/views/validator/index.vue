<template>
  <section class="page" data-module="validator">
    <header class="page-head">
      <div>
        <h2>响应校验管理</h2>
        <p class="page-desc">
          按样本编号与检测窗口维护响应校验数据集：同一「样本 + 窗口」只保留一次字段判定，
          判定完成后才参与阻断与告警汇总，重复刷新不会再次累计。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="toggleSampleForm">
          {{ sampleFormVisible ? '收起样本提交' : '提交样本判定' }}
        </button>
        <button class="btn" type="button" :disabled="refreshing" @click="refreshWindows">
          {{ refreshing ? '刷新中…' : '刷新检测窗口结论' }}
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form v-if="sampleFormVisible" class="filter-bar" @submit.prevent="submitSample">
      <label class="filter-item">
        <span>样本编号</span>
        <input v-model="sampleForm.样本编号" placeholder="如 SAMP-91" />
      </label>
      <label class="filter-item">
        <span>检测窗口</span>
        <input v-model="sampleForm.检测窗口" placeholder="如 WIN-17" />
      </label>
      <label class="filter-item">
        <span>校验字段</span>
        <input v-model="sampleForm.校验字段" placeholder="如 data.records[*].status" />
      </label>
      <label class="filter-item">
        <span>实际值</span>
        <input v-model="sampleForm.实际值" placeholder="不填表示未判定" />
      </label>
      <label class="filter-item">
        <span>判定结果</span>
        <select v-model="sampleForm.判定结果">
          <option value="">待判定</option>
          <option value="通过">通过</option>
          <option value="告警">告警</option>
          <option value="阻断">阻断</option>
        </select>
      </label>
      <label class="filter-item">
        <span>到达状态</span>
        <select v-model="sampleForm.到达状态">
          <option value="正常">正常</option>
          <option value="迟到">迟到</option>
        </select>
      </label>
      <button class="btn" type="submit">提交判定</button>
    </form>

    <form class="filter-bar" @submit.prevent="reloadSamples">
      <label class="filter-item">
        <span>样本编号</span>
        <input v-model="filters.keyword" placeholder="按样本编号检索" />
      </label>
      <label class="filter-item">
        <span>检测窗口</span>
        <input v-model="filters.window" placeholder="按检测窗口定位" />
      </label>
      <label class="filter-item">
        <span>判定结果</span>
        <select v-model="filters.result">
          <option value="">全部结果</option>
          <option value="通过">通过</option>
          <option value="告警">告警</option>
          <option value="阻断">阻断</option>
          <option value="待判定">待判定</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in sampleColumns" :key="column">{{ column }}</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in sampleRows" :key="String(row.id)">
          <td v-for="column in sampleColumns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openSample(String(row['样本编号']))">查看</button>
          </td>
        </tr>
        <tr v-if="!sampleRows.length">
          <td :colspan="sampleColumns.length + 1" class="empty-state">暂无响应校验样本，可先提交样本判定</td>
        </tr>
      </tbody>
    </table>
    <footer class="page-foot">
      <span>共 {{ sampleTotal }} 条响应校验样本</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section v-if="selectedSample" class="detail-card">
      <header class="detail-head">
        <h3>样本 {{ selectedSample['样本编号'] }} · 检测窗口 {{ selectedSample['检测窗口'] }}</h3>
        <button class="link" type="button" @click="selectedSample = null">关闭详情</button>
      </header>
      <dl class="detail-grid">
        <div><dt>判定结果</dt><dd>{{ selectedSample['判定结果'] ?? '—' }}</dd></div>
        <div><dt>判定状态</dt><dd>{{ selectedSample['判定状态'] ?? '—' }}</dd></div>
        <div><dt>到达状态</dt><dd>{{ selectedSample['到达状态'] ?? '—' }}</dd></div>
        <div><dt>实际值</dt><dd>{{ selectedSample['实际值'] || '—' }}</dd></div>
        <div><dt>窗口状态</dt><dd>{{ selectedSample['窗口状态'] ?? '—' }}</dd></div>
        <div><dt>窗口结论</dt><dd>{{ selectedSample['窗口结论'] ?? '—' }}</dd></div>
        <div><dt>阻断记录编号</dt><dd>{{ selectedSample['阻断记录编号'] ?? '无' }}</dd></div>
      </dl>
      <p class="detail-explain">{{ selectedSample['结论说明'] ?? '该检测窗口暂无结论' }}</p>
      <p v-if="selectedQueue" class="detail-explain">
        窗口异常待处理队列：{{ selectedQueue['队列编号'] }} ·
        阻断样本 {{ selectedQueue['阻断样本'] }} ·
        状态 {{ selectedQueue['状态'] }}
      </p>
    </section>

    <h3 class="section-title">检测窗口结论</h3>
    <p class="page-desc">每个检测窗口只形成一条可解释结论，采集监控读取的就是这里的同一份结果。</p>
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in windowColumns" :key="column">{{ column }}</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in windowRows" :key="String(row['检测窗口'])">
          <td v-for="column in windowColumns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              class="link"
              type="button"
              :disabled="row['窗口状态'] === '已关闭'"
              @click="closeWindow(String(row['检测窗口']))"
            >
              {{ row['窗口状态'] === '已关闭' ? '已关闭' : '关闭窗口' }}
            </button>
          </td>
        </tr>
        <tr v-if="!windowRows.length">
          <td :colspan="windowColumns.length + 1" class="empty-state">暂无检测窗口结论</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">窗口异常待处理队列</h3>
    <p class="page-desc">阻断样本写入队列时按检测窗口归并，每个窗口只保留一条阻断记录。</p>
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in queueColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in queueRows" :key="String(row['队列编号'])">
          <td v-for="column in queueColumns" :key="column">{{ row[column] ?? '—' }}</td>
        </tr>
        <tr v-if="!queueRows.length">
          <td :colspan="queueColumns.length" class="empty-state">暂无阻断样本，待处理队列为空</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, string | number | null>
type QueueRecord = Record<string, unknown>
type SampleRow = Record<string, unknown> & {
  样本编号?: string
  检测窗口?: string
  阻断记录?: QueueRecord | null
}

const ENDPOINT = '/api/validator'
const sampleColumns = ['样本编号', '检测窗口', '校验字段', '判定结果', '判定状态', '到达状态', '阻断记录编号', '窗口结论']
const windowColumns = ['检测窗口', '窗口状态', '结论', '阻断样本数', '告警样本数', '待判定样本数', '结论版本', '结论说明']
const queueColumns = ['队列编号', '检测窗口', '阻断样本', '阻断数量', '状态', '更新时间']

const sampleRows = ref<SampleRow[]>([])
const sampleTotal = ref(0)
const windowRows = ref<Row[]>([])
const queueRows = ref<Row[]>([])
const selectedSample = ref<SampleRow | null>(null)
const selectedQueue = computed<QueueRecord | null>(() => {
  const queue = selectedSample.value?.['阻断记录']
  return queue ? (queue as QueueRecord) : null
})
const errorMessage = ref('')
const refreshing = ref(false)
const sampleFormVisible = ref(false)
const filters = ref({ keyword: '', window: '', result: '' })
const sampleForm = ref({
  样本编号: '',
  检测窗口: '',
  校验字段: '',
  实际值: '',
  判定结果: '',
  到达状态: '正常',
})

const stats = computed(() => {
  const blocked = sampleRows.value.filter((row) => row['判定结果'] === '阻断').length
  const pending = sampleRows.value.filter((row) => row['判定状态'] === '待判定').length
  return [
    { label: '响应校验样本', value: sampleTotal.value },
    { label: '当前页阻断样本', value: blocked },
    { label: '检测窗口数', value: windowRows.value.length },
    { label: '待判定样本', value: pending },
    { label: '异常待处理队列', value: queueRows.value.length },
  ]
})

function queryString(values: Record<string, string>): string {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value) params.set(key, value)
  })
  return params.toString()
}

function toggleSampleForm() {
  sampleFormVisible.value = !sampleFormVisible.value
}

function resetFilters() {
  filters.value = { keyword: '', window: '', result: '' }
  void reloadSamples()
}

async function reloadSamples() {
  errorMessage.value = ''
  try {
    const payload = await fetchJson<{ items: SampleRow[]; total: number }>(
      `${ENDPOINT}/samples?${queryString(filters.value)}`,
    )
    sampleRows.value = payload.items ?? []
    sampleTotal.value = payload.total ?? sampleRows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '响应校验列表读取失败'
  }
}

async function reloadWindows() {
  try {
    const payload = await fetchJson<{ items: Row[] }>(`${ENDPOINT}/windows`)
    windowRows.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测窗口结论读取失败'
  }
}

async function reloadQueue() {
  try {
    const payload = await fetchJson<{ items: Row[] }>(`${ENDPOINT}/queue`)
    queueRows.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '窗口异常待处理队列读取失败'
  }
}

async function reloadAll() {
  await Promise.all([reloadSamples(), reloadWindows(), reloadQueue()])
}

async function refreshWindows() {
  refreshing.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/refresh`, { method: 'POST', body: '{}' })
    if (!response.ok) {
      throw new Error('检测窗口结论刷新失败')
    }
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测窗口结论刷新失败'
  } finally {
    refreshing.value = false
  }
}

async function submitSample() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/samples`, {
      method: 'POST',
      body: JSON.stringify({ values: sampleForm.value }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.detail || payload.message || '样本判定提交失败')
    }
    errorMessage.value = payload.message
    if (payload.entry) {
      selectedSample.value = payload.entry
    }
    sampleForm.value = { 样本编号: '', 检测窗口: '', 校验字段: '', 实际值: '', 判定结果: '', 到达状态: '正常' }
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '样本判定提交失败'
  }
}

async function openSample(sampleNo: string) {
  errorMessage.value = ''
  try {
    selectedSample.value = await fetchJson<SampleRow>(`${ENDPOINT}/samples/${encodeURIComponent(sampleNo)}`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '样本详情读取失败'
  }
}

async function closeWindow(windowNo: string) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/windows/${encodeURIComponent(windowNo)}/close`, {
      method: 'POST',
      body: '{}',
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.detail || payload.message || '检测窗口关闭失败')
    }
    errorMessage.value = payload.message
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测窗口关闭失败'
  }
}

onMounted(reloadAll)
</script>
