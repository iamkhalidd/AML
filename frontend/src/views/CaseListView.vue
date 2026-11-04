<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { Case, CasePage } from '@/api/types'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDate, humanize } from '@/format'

const route = useRoute()
const router = useRouter()

function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const status = computed(() => one(route.query.status) || 'open')
const assignee = computed(() => one(route.query.assignee))
const search = ref(one(route.query.q))

const items = ref<Case[]>([])
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
    const page = await api.get<CasePage>('/v1/cases', {
      status: status.value === 'all' ? null : [status.value],
      assignee: assignee.value || null,
      q: one(route.query.q) || null,
      cursor: more ? cursor.value : null,
      limit: 50,
    })
    items.value = more ? [...items.value, ...page.items] : page.items
    cursor.value = page.next_cursor
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'Could not load cases.'
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
</script>

<template>
  <div class="page">
    <header>
      <h1>Cases</h1>
    </header>

    <div class="toolbar">
      <div class="tabs" role="tablist">
        <button
          v-for="t in ['open', 'closed', 'all']"
          :key="t"
          type="button"
          role="tab"
          class="tab"
          :aria-selected="status === t"
          @click="setQuery({ status: t === 'open' ? undefined : t })"
        >
          {{ humanize(t) }}
        </button>
      </div>
      <div class="filters">
        <label class="sr-only" for="case-search">Search</label>
        <input
          id="case-search"
          v-model="search"
          class="input search"
          type="search"
          placeholder="Reference, title or customer"
        />
        <label class="sr-only" for="case-assignee">Assignee</label>
        <select
          id="case-assignee"
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
              <th>Reference</th>
              <th>Title</th>
              <th>Customer</th>
              <th>Severity</th>
              <th class="num">Alerts</th>
              <th>Status</th>
              <th>Assignee</th>
              <th>Opened</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in items"
              :key="item.id"
              class="row"
              @click="router.push({ name: 'case', params: { id: item.id } })"
            >
              <td>
                <RouterLink :to="{ name: 'case', params: { id: item.id } }" class="mono" @click.stop>
                  {{ item.reference }}
                </RouterLink>
              </td>
              <td>{{ item.title }}</td>
              <td>{{ item.customer.name }}</td>
              <td><StatusBadge v-if="item.severity" :value="item.severity" kind="severity" /></td>
              <td class="num">{{ item.alert_count }}</td>
              <td>
                <StatusBadge :value="item.closure_pending ? 'pending' : item.status" kind="status" />
                <div v-if="item.outcome" class="muted small">{{ humanize(item.outcome) }}</div>
              </td>
              <td :class="{ muted: !item.assignee }">{{ item.assignee?.name ?? 'Unassigned' }}</td>
              <td class="muted nowrap">{{ formatDate(item.opened_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!loading && !items.length && !error" class="empty">
        No cases yet. Escalate an alert to open one.
      </p>
      <p v-if="loading && !items.length" class="empty">Loading cases…</p>
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

.small {
  font-size: 12px;
}

.nowrap {
  white-space: nowrap;
}

.more {
  display: flex;
  justify-content: center;
}
</style>
