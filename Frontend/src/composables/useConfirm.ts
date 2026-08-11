import { ElMessageBox } from 'element-plus'

export interface ConfirmOptions {
  readonly title: string
  /** 说明「会发生什么」，不写"确定吗"这类空话。 */
  readonly message: string
  /** 确认按钮文案 = 实际动作词（"归档"、"删除"），与后续 toast 同词根。 */
  readonly confirmText: string
  /** 高危操作（删除/归档）标 danger。 */
  readonly danger?: boolean
}

/**
 * 危险操作二次确认。收口 `ElMessageBox.confirm` 的样式与文案规范，
 * 覆盖删表 / 归档 / 阶段流转 / 撤销协管员 / 切换施工。
 *
 * 返回 `true` = 用户确认；`false` = 取消（含 Esc / 点遮罩）。**不抛异常**，
 * 调用方 `if (!(await confirmAction(...))) return` 即可。
 */
export async function confirmAction(options: ConfirmOptions): Promise<boolean> {
  try {
    await ElMessageBox.confirm(options.message, options.title, {
      confirmButtonText: options.confirmText,
      cancelButtonText: '取消',
      type: options.danger ? 'warning' : 'info',
      confirmButtonClass: options.danger ? 'el-button--danger' : undefined,
      closeOnClickModal: false,
      autofocus: false,
    })
    return true
  } catch {
    // ElMessageBox 取消走 reject；此处吞掉是刻意的——取消不是错误。
    return false
  }
}

export function useConfirm() {
  return { confirmAction }
}
