import { create } from 'zustand'
import client from '../api/client'

interface User {
  id: number
  username: string
  email: string
  role: string
  tier: string
}

interface AuthState {
  user: User | null
  loaded: boolean
  setAuth: (authData: { access_token: string; refresh_token: string; user: User }) => void
  fetchUser: () => Promise<void>
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  loaded: false,
  setAuth: (authData) => {
    localStorage.setItem('access_token', authData.access_token)
    localStorage.setItem('refresh_token', authData.refresh_token)
    set({ user: authData.user, loaded: true })
  },
  fetchUser: async () => {
    if (!localStorage.getItem('access_token')) {
      set({ user: null, loaded: true })
      return
    }
    try {
      const resp = await client.get('/auth/me')
      set({ user: resp.data.data, loaded: true })
    } catch {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      set({ user: null, loaded: true })
    }
  },
  logout: () => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    set({ user: null })
    window.location.href = '/login'
  },
}))
