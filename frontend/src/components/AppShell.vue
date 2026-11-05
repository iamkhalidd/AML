<script setup lang="ts">
import { computed, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { humanize } from '@/format'
import { useApprovals } from '@/stores/approvals'
import { useSession } from '@/stores/session'

const session = useSession()
const router = useRouter()
const route = useRoute()
const approvals = useApprovals()

watch(
  () => route.path,
  () => {
    if (session.can('approvals.view')) approvals.refresh()
  },
  { immediate: true },
)

const initials = computed(() =>
  (session.user?.full_name ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join(''),
)

async function signOut() {
  await session.signOut().catch(() => undefined)
  router.push({ name: 'sign-in' })
}
</script>

<template>
  <div class="shell">
    <aside class="side">
      <div class="brand">
        <span class="mark" aria-hidden="true">A</span>
        <span>AML Platform</span>
      </div>
      <nav aria-label="Main">
        <RouterLink v-if="session.can('alerts.view')" :to="{ name: 'alerts' }" class="nav">
          Alerts
        </RouterLink>
        <RouterLink v-if="session.can('cases.view')" :to="{ name: 'cases' }" class="nav">
          Cases
        </RouterLink>
        <RouterLink v-if="session.can('approvals.view')" :to="{ name: 'approvals' }" class="nav">
          Approvals
          <span v-if="approvals.waiting" class="count">
            {{ approvals.waiting }}<span class="sr-only"> waiting for you</span>
          </span>
        </RouterLink>
      </nav>
      <div v-if="session.user" class="who">
        <span class="avatar" aria-hidden="true">{{ initials }}</span>
        <div class="who-text">
          <div class="who-name">{{ session.user.full_name }}</div>
          <div class="who-role">{{ humanize(session.user.role) }}</div>
        </div>
        <button class="signout" type="button" @click="signOut">Sign out</button>
      </div>
    </aside>
    <main class="main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 216px minmax(0, 1fr);
  min-height: 100vh;
}

.side {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  height: 100vh;
  padding: 16px 12px;
  background: var(--ink);
  color: var(--ink-text);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px 20px;
  color: #fff;
  font-weight: 600;
}

.mark {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: var(--accent);
  color: #fff;
  font-size: 14px;
}

nav {
  display: grid;
  gap: 2px;
}

.nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  color: var(--ink-text);
  text-decoration: none;
  font-weight: 500;
}

.nav:hover {
  background: var(--ink-2);
  color: #fff;
}

.count {
  min-width: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--accent);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
  text-align: center;
}

.nav.router-link-active {
  background: var(--ink-2);
  color: #fff;
  box-shadow: inset 3px 0 0 var(--accent);
}

.who {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 8px 10px;
  align-items: center;
  margin-top: auto;
  padding: 12px 8px 0;
  border-top: 1px solid var(--ink-2);
}

.avatar {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--ink-2);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
}

.who-text {
  min-width: 0;
}

.who-name {
  overflow: hidden;
  color: #fff;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.who-role {
  font-size: 12px;
}

.signout {
  grid-column: 1 / -1;
  padding: 6px 0;
  border: 0;
  background: none;
  color: var(--ink-text);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.signout:hover {
  color: #fff;
  text-decoration: underline;
}

.main {
  min-width: 0;
  padding: 24px 28px 48px;
}

@media (max-width: 860px) {
  .shell {
    grid-template-columns: 1fr;
  }

  .side {
    position: static;
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 16px;
    height: auto;
    padding: 10px 16px;
  }

  .brand {
    padding: 0;
  }

  nav {
    display: flex;
  }

  .who {
    display: flex;
    margin: 0 0 0 auto;
    padding: 0;
    border: 0;
  }

  .who-text {
    display: none;
  }

  .signout {
    padding: 0;
  }

  .main {
    padding: 16px;
  }
}
</style>
