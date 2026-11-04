import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, ApiError } from '@/api/client'
import type { Me } from '@/api/types'

/** Who is signed in. `checked` turns true once the server has been asked. */
export const useSession = defineStore('session', () => {
  const user = ref<Me | null>(null)
  const checked = ref(false)

  const signedIn = computed(() => user.value !== null)

  function can(permission: string): boolean {
    return user.value?.permissions.includes(permission) ?? false
  }

  async function load(): Promise<void> {
    try {
      user.value = await api.get<Me>('/v1/auth/me')
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) throw error
      user.value = null
    } finally {
      checked.value = true
    }
  }

  async function signIn(email: string, password: string, code: string): Promise<void> {
    user.value = await api.post<Me>('/v1/auth/sign-in', { email, password, code })
    checked.value = true
  }

  async function signOut(): Promise<void> {
    try {
      await api.post('/v1/auth/sign-out')
    } finally {
      forget()
    }
  }

  function forget(): void {
    user.value = null
    checked.value = true
  }

  return { user, checked, signedIn, can, load, signIn, signOut, forget }
})
