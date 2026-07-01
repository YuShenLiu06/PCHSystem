import { defineStore } from 'pinia'

interface PlayerBrief { uuid: string; name: string; role: string }

export const useAuthStore = defineStore('auth', {
  state: () => ({
    accessToken: localStorage.getItem('access_token') ?? '',
    refreshToken: localStorage.getItem('refresh_token') ?? '',
    player: JSON.parse(localStorage.getItem('player') ?? 'null') as PlayerBrief | null,
  }),
  getters: {
    isAuthenticated: (s) => !!s.accessToken,
  },
  actions: {
    set(tokens: { access_token: string; refresh_token: string }, player: PlayerBrief) {
      this.accessToken = tokens.access_token
      this.refreshToken = tokens.refresh_token
      this.player = player
      localStorage.setItem('access_token', this.accessToken)
      localStorage.setItem('refresh_token', this.refreshToken)
      localStorage.setItem('player', JSON.stringify(player))
    },
    clear() {
      this.accessToken = ''
      this.refreshToken = ''
      this.player = null
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('player')
    },
  },
})
