<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { useSession } from '@/stores/session'

const session = useSession()
const route = useRoute()
const router = useRouter()

const email = ref('')
const password = ref('')
const code = ref('')
const busy = ref(false)
const error = ref<string | null>(null)

const expired = computed(() => route.query.expired === '1')
const ready = computed(() => email.value.trim() && password.value && /^\d{6}$/.test(code.value))

function nextPath(): string {
  const next = route.query.next
  // Only same-app paths: never send someone to another site after sign-in.
  return typeof next === 'string' && next.startsWith('/') && !next.startsWith('//') ? next : '/alerts'
}

async function submit() {
  if (!ready.value || busy.value) return
  busy.value = true
  error.value = null
  try {
    await session.signIn(email.value.trim(), password.value, code.value)
    await router.replace(nextPath())
  } catch (caught) {
    error.value =
      caught instanceof ApiError && caught.status === 401
        ? 'That email, password or code is not right. Codes change every 30 seconds.'
        : caught instanceof Error
          ? caught.message
          : 'Sign-in failed.'
    code.value = ''
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <form class="card panel" novalidate @submit.prevent="submit">
      <div class="brand">
        <span class="mark" aria-hidden="true">A</span>
        <div>
          <h1>Sign in</h1>
          <p class="muted">AML Platform</p>
        </div>
      </div>

      <p v-if="expired && !error" class="notice notice-info">Your session ended. Sign in again.</p>
      <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p>

      <label class="field">
        <span>Email</span>
        <input v-model="email" class="input" type="email" autocomplete="username" required />
      </label>
      <label class="field">
        <span>Password</span>
        <input
          v-model="password"
          class="input"
          type="password"
          autocomplete="current-password"
          required
        />
      </label>
      <label class="field">
        <span>Authenticator code</span>
        <input
          v-model="code"
          class="input code"
          inputmode="numeric"
          autocomplete="one-time-code"
          maxlength="6"
          pattern="\d{6}"
          placeholder="6 digits"
          required
          @input="code = code.replace(/\D/g, '')"
        />
      </label>

      <button class="btn btn-primary submit" type="submit" :disabled="!ready || busy">
        {{ busy ? 'Signing in…' : 'Sign in' }}
      </button>
      <p class="muted small">
        The first request after a quiet spell can take up to a minute while the server wakes.
      </p>
    </form>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  min-height: 100vh;
  padding: 16px;
  place-items: center;
  background: linear-gradient(160deg, var(--ink) 0 38%, var(--bg) 38%);
}

.card {
  display: grid;
  gap: 16px;
  width: 100%;
  max-width: 380px;
  padding: 28px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand p {
  margin: 0;
}

.mark {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  font-size: 18px;
  font-weight: 700;
}

.notice {
  margin: 0;
}

.code {
  font-family: var(--font-mono);
  letter-spacing: 0.3em;
}

.submit {
  justify-content: center;
  height: 38px;
}

.small {
  margin: 0;
  font-size: 12.5px;
}
</style>
