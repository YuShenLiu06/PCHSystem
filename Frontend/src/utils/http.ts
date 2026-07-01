import axios from 'axios'
import { useAuthStore } from '../stores/auth'

export const http = axios.create({ baseURL: '/api' })

http.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.accessToken) config.headers.Authorization = `Bearer ${auth.accessToken}`
  return config
})

http.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      const auth = useAuthStore()
      auth.clear()
      window.location.hash = '#/auth'   // 兜底
    }
    return Promise.reject(err)
  },
)
