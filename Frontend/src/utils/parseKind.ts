// 解析文件类型判定：依文件名扩展名把上传文件路由到对应后端解析端点。
// 仅做边界 UX 校验，后端（/parsing/litematic、/parsing/nbt）仍按扩展名二次校验为最终权威（RS-2）。

export type ParseKind = 'litematic' | 'nbt'

/** 依文件名扩展名判定解析类型；非 .litematic / .nbt 返回 null。大小写不敏感。 */
export function detectParseKind(file: File): ParseKind | null {
  const name = file.name.toLowerCase()
  if (name.endsWith('.nbt')) return 'nbt'
  if (name.endsWith('.litematic')) return 'litematic'
  return null
}
