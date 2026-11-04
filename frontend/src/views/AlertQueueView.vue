<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { Alert, AlertPage } from '@/api/types'
import StatusBadge from '@/components/StatusBadge.vue'
import { age, formatMoney, humanize } from '@/format'

const route = useRoute()
const router = useRouter()

const tabs = [
  { key: 'active', label: 'Open', status: ['open', 'in_review'] },
  { key: 'escalated', label: 'Escalated', status: ['escalated'] },
  { key: 'closed', label: 'Closed', status: ['closed'] },
  { key: 'all', label: 'All', status: [] },
] as const

function one(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

const tab = computed(() => tabs.find((t) => t.key === route.query.tab) ?? tabs[0])
const severity = computed(() => one(route.query.severity))
const assignee = computed(() => one(route.query.assignee))
const search = ref(one(route.query.q))

const items = ref<Alert[]>([])
const counts = ref<AlertPage['counts'] | null>(null)
const cursor = ref<string | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

function setQuery(patch: Record<string, string | undefined>) {
  const query = { ...route.query, ...patch }
  for (const key of Object.keys(query)) if (!query[key]) delete query[key]
  router.replace({ query })
}

async function load(more = false) {
  loading.value = true
  error.value = null
  try {
    const page = await api.get<AlertPage>('/v1/alerts', {
      status: [...tab.value.status],
      severity: severity.value ? [severity.value] : null,
      assignee: assignee.value || null,
      q: one(route.query.q) || null,
      cursor: more ? cursor.value : null,
      limit: 50,
    })
    items.value = more ? [...items.value, ...page.items] : page.items
    counts.value = page.counts
    cursor.value = page.next_cursor
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'Could not load alerts.'
  } finally {
    loading.value = false
  }
}

watch(() => route.query, () => load(), { immediate: true, deep: true })

let typing: ReturnType<typeof setTimeout> | undefined
watch(search, (value) => {
  clearTimeout(typing)
  typing = setTimeout(() => setQuery({ q: value.trim() || undefined }), 300)
})

const now = ref(new Date())
setInterval(() => (now.value = new Date()), 60_000)
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h1>Alerts</h1>
        <p v-if="counts" class="muted summary">
          {{ counts.open }} open · {{ counts.mine }} assigned to you<template
            v-if="counts.overdue !== null"
          >
            · <span :class="{ overdue: counts.overdue > 0 }">{{ counts.overdue }} overdue</span></template
          >
        </p>
      </div>
    </header>

    <div class="toolbar">
      <div class="tabs" role="tablist">
        <button
          v-for="t in tabs"
          :key="t.key"
          type="button"
          role="tab"
          class="tab"
          :aria-selected="tab.key === t.key"
          @click="setQuery({ tab: t.key === 'active' ? undefined : t.key })"
        >
          {{ t.label }}
        </button>
      </div>
      <div class="filters">
        <label class="sr-only" for="alert-search">Search</label>
        <input
          id="alert-search"
          v-model="search"
          class="input search"
          type="search"
          placeholder="Customer, ID or explanation"
        />
        <label class="sr-only" for="alert-severity">Severity</label>
        <select
          id="alert-severity"
          class="select"
          :value="severity"
          @change="setQuery({ severity: ($event.target as HTMLSelectElement).value || undefined })"
        >
          <option value="">Any severity</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <label class="sr-only" for="alert-assignee">Assignee</label>
        <select
          id="alert-assignee"
          class="select"
          :value="assignee"
          @change="setQuery({ assignee: ($event.target as HTMLSelectElement).value || undefined })"
        >
          <option value="">Anyone</option>
          <option value="me">Assigned to me</option>
          <option value="none">Unassigned</option>
        </select>
      </div>
    </div>

    <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p>

    <section class="panel">
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>Severity</th>
              <th>Customer</th>
              <th>Rule</th>
              <th class="num">Amount</th>
              <th>Status</th>
              <th>Assignee</th>
              <th class="num">Age</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="alert in items"
              :key="alert.id"
              class="row"
              @click="router.push({ name: 'alert', params: { id: alert.id } })"
            >
              <td><StatusBadge :value="alert.severity" kind="severity" /></td>
              <td>
                <RouterLink :to="{ name: 'alert', params: { id: alert.id } }" class="who" @click.stop>
                  {{ alert.customer.name }}
                </RouterLink>
                <div class="muted mono">{{ alert.customer.external_id }}</div>
              </td>
              <td>
                <div>{{ alert.rule.name }}</div>
                <div class="muted explain">{{ alert.explanation }}</div>
              </td>
              <td class="num">
                {{ alert.amount ? formatMoney(alert.amount.value, alert.amount.currency) : '—' }}
                <div v-if="alert.hits > 1" class="muted">{{ alert.hits }} txns</div>
              </td>
              <td><StatusBadge :value="alert.status" kind="status" /></td>
              <td :class="{ muted: !alert.assignee }">{{ alert.assignee?.name ?? 'Unassigned' }}</td>
              <td class="num muted" :title="alert.created_at">{{ age(alert.created_at, now) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!loading && !items.length && !error" class="empty">
        No {{ tab.key === 'all' ? '' : humanize(tab.label).toLowerCase() }} alerts match.
      </p>
      <p v-if="loading && !items.length" class="empty">Loading alerts…</p>
    </section>

    <div v-if="cursor" class="more">
      <button class="btn" type="button" :disabled="loading" @click="load(true)">
        {{ loading ? 'Loading…' : 'Load more' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  gap: 16px;
}

.summary {
  margin: 4px 0 0;
}

.overdue {
  color: var(--danger);
  font-weight: 600;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.tabs {
  display: inline-flex;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
}

.tab {
  padding: 5px 14px;
  border: 0;
  border-radius: 6px;
  background: none;
  color: var(--muted);
  font: inherit;
  font-weight: 500;
  cursor: pointer;
}

.tab[aria-selected='true'] {
  background: var(--ink);
  color: #fff;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.filters .select {
  width: auto;
}

.search {
  width: 240px;
}

.row {
  cursor: pointer;
}

.row:hover td {
  background: #f8fafb;
}

.who {
  color: var(--text);
  font-weight: 600;
  text-decoration: none;
}

.who:hover {
  text-decoration: underline;
}

.explain {
  display: -webkit-box;
  max-width: 460px;
  overflow: hidden;
  font-size: 13px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.more {
  display: flex;
  justify-content: center;
}
</style>
