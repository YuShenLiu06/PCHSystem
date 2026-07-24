import { describe, expect, it } from 'vitest'
import type { AccountBrief, PlayerBrief } from '../../api/identity'
import { resolveDisplayName } from '../identity'

describe('resolveDisplayName', () => {
  const player: PlayerBrief = { uuid: 'uuid-1', name: 'GameName', role: 'user' }

  it('优先返回 display_name（昵称）', () => {
    const account: AccountBrief = {
      id: 1,
      is_temporary: false,
      username: 'steve',
      display_name: '史蒂夫',
      role: 'user',
    }
    expect(resolveDisplayName(account, player)).toBe('史蒂夫')
  })

  it('display_name 为空时回退到 username（#35：密码登录应显示用户名而非游戏名）', () => {
    const account: AccountBrief = {
      id: 1,
      is_temporary: false,
      username: 'steve',
      display_name: null,
      role: 'user',
    }
    expect(resolveDisplayName(account, player)).toBe('steve')
  })

  it('临时账号 username/display_name 均空时回退到游戏名', () => {
    const account: AccountBrief = {
      id: 1,
      is_temporary: true,
      username: null,
      display_name: null,
      role: 'user',
    }
    expect(resolveDisplayName(account, player)).toBe('GameName')
  })

  it('account 与 player 均无可用名时返回空串', () => {
    const account: AccountBrief = {
      id: 1,
      is_temporary: true,
      username: null,
      display_name: null,
      role: 'user',
    }
    expect(resolveDisplayName(account, null)).toBe('')
  })
})
