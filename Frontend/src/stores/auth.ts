import { defineStore } from 'pinia'
import type { AccountBrief, PlayerBrief } from '../api/identity'

interface AuthState {
  accessToken: string
  refreshToken: string
  player: PlayerBrief | null
  account: AccountBrief | null
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    accessToken: localStorage.getItem('access_token') ?? '',
    refreshToken: localStorage.getItem('refresh_token') ?? '',
    player: JSON.parse(localStorage.getItem('player') ?? 'null') as PlayerBrief | null,
    account: JSON.parse(localStorage.getItem('account') ?? 'null') as AccountBrief | null,
  }),
  getters: {
    isAuthenticated: (s) => !!s.accessToken,
    isTemporaryAccount: (s) => !!s.account?.is_temporary,
  },
  actions: {
    set(
      tokens: { access_token: string; refresh_token: string },
      player: PlayerBrief,
      account: AccountBrief,
    ) {
      this.accessToken = tokens.access_token
      this.refreshToken = tokens.refresh_token
      this.player = player
      this.account = account
      localStorage.setItem('access_token', this.accessToken)
      localStorage.setItem('refresh_token', this.refreshToken)
      localStorage.setItem('player', JSON.stringify(player))
      localStorage.setItem('account', JSON.stringify(account))
    },
    clear() {
      this.accessToken = ''
      this.refreshToken = ''
      this.player = null
      this.account = null
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('player')
      localStorage.removeItem('account')
    },
  },
})
