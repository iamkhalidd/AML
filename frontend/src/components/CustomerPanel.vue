<script setup lang="ts">
import { RouterLink } from 'vue-router'

import type { CustomerView } from '@/api/types'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDate, formatMoney, humanize } from '@/format'

defineProps<{ view: CustomerView; currentAlertId?: string }>()
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2>Customer</h2>
      <span v-if="view.customer.is_pep" class="pep">PEP</span>
    </div>
    <div class="panel-body stack">
      <div>
        <div class="name">{{ view.customer.full_name }}</div>
        <div class="muted mono">{{ view.customer.external_id }}</div>
      </div>
      <dl class="facts">
        <dt>Type</dt>
        <dd>{{ humanize(view.customer.customer_type) }}</dd>
        <dt>Risk</dt>
        <dd>
          <StatusBadge :value="view.customer.risk_level ?? 'unrated'" kind="severity" />
          <span
            v-if="view.customer.feed_risk_level && view.customer.feed_risk_level !== view.customer.risk_level"
            class="muted"
          >
            (feed: {{ view.customer.feed_risk_level }})</span
          >
        </dd>
        <dt>KYC</dt>
        <dd>{{ humanize(view.customer.kyc_status) }}</dd>
        <dt v-if="view.customer.occupation">Occupation</dt>
        <dd v-if="view.customer.occupation">{{ view.customer.occupation }}</dd>
        <dt v-if="view.customer.industry">Industry</dt>
        <dd v-if="view.customer.industry">{{ view.customer.industry }}</dd>
        <dt v-if="view.customer.state">State</dt>
        <dd v-if="view.customer.state">{{ view.customer.state }}</dd>
        <dt>Onboarded</dt>
        <dd>{{ formatDate(view.customer.onboarding_date) }}</dd>
      </dl>

      <div>
        <h3>Expected vs actual</h3>
        <dl class="facts compact">
          <dt>Declared monthly credit</dt>
          <dd class="num-left">{{ formatMoney(view.expected_activity.monthly_credit) }}</dd>
          <template v-for="row in view.actual_activity" :key="row.currency">
            <dt>Credits, last {{ view.activity_window_days }} days</dt>
            <dd class="num-left">
              {{ formatMoney(row.credit_total, row.currency) }}
              <span class="muted">({{ row.credit_count }})</span>
            </dd>
          </template>
        </dl>
      </div>

      <div v-if="view.beneficial_owners.length">
        <h3>Beneficial owners</h3>
        <ul class="plain">
          <li v-for="owner in view.beneficial_owners" :key="owner.owner_ref">
            {{ owner.full_name }}
            <span class="muted">
              · {{ owner.ownership_percent ? `${owner.ownership_percent}%` : humanize(owner.control_type) }}
            </span>
            <span v-if="owner.is_pep" class="pep small">PEP</span>
          </li>
        </ul>
      </div>

      <div v-if="view.accounts.length">
        <h3>Accounts</h3>
        <ul class="plain">
          <li v-for="account in view.accounts" :key="account.id">
            <span class="mono">{{ account.external_id }}</span>
            <span class="muted"> · {{ humanize(account.product_type) }} · {{ account.status }}</span>
          </li>
        </ul>
      </div>

      <div v-if="view.earlier_alerts.some((a) => a.id !== currentAlertId)">
        <h3>Other alerts</h3>
        <ul class="plain">
          <template v-for="earlier in view.earlier_alerts" :key="earlier.id">
            <li v-if="earlier.id !== currentAlertId">
              <RouterLink :to="{ name: 'alert', params: { id: earlier.id } }">{{
                earlier.rule_name
              }}</RouterLink>
              <span class="muted"> · {{ formatDate(earlier.created_at) }} · </span>
              <StatusBadge :value="earlier.status" kind="status" />
            </li>
          </template>
        </ul>
      </div>

      <div v-if="view.cases.length">
        <h3>Cases</h3>
        <ul class="plain">
          <li v-for="item in view.cases" :key="item.id">
            <RouterLink :to="{ name: 'case', params: { id: item.id } }" class="mono">{{
              item.reference
            }}</RouterLink>
            <span class="muted"> · {{ item.title }}</span>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stack {
  display: grid;
  gap: 18px;
}

.stack h3 {
  margin-bottom: 8px;
}

.name {
  font-size: 15px;
  font-weight: 600;
}

.pep {
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12px;
  font-weight: 700;
}

.pep.small {
  margin-left: 6px;
  font-size: 11px;
}

.compact {
  grid-template-columns: 1fr auto;
}

.num-left {
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.plain {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
</style>
