import axios from 'axios'

const API_URL = '/api'

const client = axios.create({
  baseURL: API_URL,
  timeout: 15000,
})

// request interceptor — attach JWT
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// response interceptor — auto refresh on 401
let refreshing = false
client.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config
    if (error.response?.status === 401 && !original._retry && !original.url?.includes('/auth/')) {
      original._retry = true
      const refreshToken = localStorage.getItem('refresh_token')
      if (!refreshToken || refreshing) {
        window.location.href = '/login'
        return Promise.reject(error)
      }
      refreshing = true
      try {
        const resp = await axios.post(`${API_URL}/auth/refresh`, { refresh_token: refreshToken })
        const data = resp.data.data
        localStorage.setItem('access_token', data.access_token)
        localStorage.setItem('refresh_token', data.refresh_token)
        client.defaults.headers.common.Authorization = `Bearer ${data.access_token}`
        original.headers.Authorization = `Bearer ${data.access_token}`
        return client(original)
      } catch {
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        window.location.href = '/login'
        return Promise.reject(error)
      } finally {
        refreshing = false
      }
    }
    const detail = error.response?.data?.detail
    if (error.response?.status === 403 && detail && typeof detail === 'object'
        && (detail.code === 'quota_exceeded' || detail.code === 'feature_locked')) {
      window.dispatchEvent(new CustomEvent('membership:upgrade', { detail }))
    }
    return Promise.reject(error)
  },
)

export default client
