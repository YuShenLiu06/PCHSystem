import { describe, expect, it } from 'vitest'
import { detectParseKind } from '../parseKind'

// 构造仅用于判定文件名的 File 占位对象（内容无关）
function file(name: string): File {
  return new File([new Uint8Array([0])], name, { type: 'application/octet-stream' })
}

describe('detectParseKind', () => {
  it('.nbt 文件 → nbt', () => {
    expect(detectParseKind(file('house.nbt'))).toBe('nbt')
  })

  it('.litematic 文件 → litematic', () => {
    expect(detectParseKind(file('base.litematic'))).toBe('litematic')
  })

  it('扩展名大小写不敏感', () => {
    expect(detectParseKind(file('HOUSE.NBT'))).toBe('nbt')
    expect(detectParseKind(file('BASE.LITEMATIC'))).toBe('litematic')
  })

  it('非目标扩展名 → null', () => {
    expect(detectParseKind(file('archive.zip'))).toBeNull()
    expect(detectParseKind(file('noext'))).toBeNull()
  })
})
