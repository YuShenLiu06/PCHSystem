import { ElMessage } from 'element-plus'
import { extractApiError } from '../utils/error'

/**
 * 反馈文案收口。替代各视图散落的 `ElMessage.*` + 手写 detail 提取。
 *
 * 不接管 `utils/http.ts` 拦截器里的网络错节流（那层管的是「后端不可达」全局提示，
 * 与业务级反馈职责不同，RS-5）。401 同理不在此处理。
 */

/** 操作成功。文案用动作完成态（"已保存"），与触发按钮同词根。 */
export function notifyOk(message: string): void {
  ElMessage.success(message)
}

/** 前置校验不通过 / 无副作用的拒绝。 */
export function notifyWarn(message: string): void {
  ElMessage.warning(message)
}

/**
 * 操作失败。优先展示后端 `detail`，缺失时用 `fallback`。
 * 失败文案说明「发生了什么」，不道歉、不含糊。
 */
export function notifyErr(e: unknown, fallback: string): void {
  ElMessage.error(extractApiError(e) ?? fallback)
}

export function useNotify() {
  return { notifyOk, notifyWarn, notifyErr }
}
