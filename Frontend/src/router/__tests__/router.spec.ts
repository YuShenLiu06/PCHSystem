import { describe, expect, it } from 'vitest'
import { router } from '../index'

function routeMeta(path: string): Record<string, unknown> | undefined {
  const r = router.options.routes.find((x) => x.path === path)
  return r?.meta
}

describe('router 守卫', () => {
  // 改动 2：/register 改为需登录路由——未登录直访由 beforeEach 重定向 /auth，
  // 避免空 Authorization 头触发后端 401 "missing authorization"。
  it('/register 不再 public（须先有临时账号会话 JWT）', () => {
    expect(routeMeta('/register')?.public).toBeFalsy()
  })

  it('/login 与 /auth 仍 public（登录/兑换入口）', () => {
    expect(routeMeta('/login')?.public).toBe(true)
    expect(routeMeta('/auth')?.public).toBe(true)
  })
})
