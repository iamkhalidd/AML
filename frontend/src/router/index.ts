import { createRouter, createWebHistory } from 'vue-router'

import { setSignedOutHandler } from '@/api/client'
import { useSession } from '@/stores/session'

declare module 'vue-router' {
  interface RouteMeta {
    /** Pages anyone can open, signed in or not. */
    public?: boolean
    title?: string
  }
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/sign-in',
      name: 'sign-in',
      component: () => import('@/views/SignInView.vue'),
      meta: { public: true, title: 'Sign in' },
    },
    {
      path: '/',
      component: () => import('@/components/AppShell.vue'),
      children: [
        { path: '', redirect: { name: 'alerts' } },
        {
          path: 'alerts',
          name: 'alerts',
          component: () => import('@/views/AlertQueueView.vue'),
          meta: { title: 'Alerts' },
        },
        {
          path: 'alerts/:id',
          name: 'alert',
          component: () => import('@/views/AlertDetailView.vue'),
          props: true,
          meta: { title: 'Alert' },
        },
        {
          path: 'cases',
          name: 'cases',
          component: () => import('@/views/CaseListView.vue'),
          meta: { title: 'Cases' },
        },
        {
          path: 'cases/:id',
          name: 'case',
          component: () => import('@/views/CaseDetailView.vue'),
          props: true,
          meta: { title: 'Case' },
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  const session = useSession()
  if (!session.checked) {
    try {
      await session.load()
    } catch {
      // Server unreachable: let the sign-in page say so.
    }
  }
  if (!to.meta.public && !session.signedIn) {
    return { name: 'sign-in', query: to.fullPath === '/' ? {} : { next: to.fullPath } }
  }
  if (to.name === 'sign-in' && session.signedIn) return { name: 'alerts' }
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · AML Platform` : 'AML Platform'
})

setSignedOutHandler(() => {
  const session = useSession()
  if (!session.signedIn) return
  session.forget()
  const here = router.currentRoute.value
  if (!here.meta.public) router.push({ name: 'sign-in', query: { next: here.fullPath, expired: '1' } })
})

export default router
