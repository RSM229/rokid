/**
 * 计算字符串 UTF-8 编码后的字节数。
 *
 * 用途：bcrypt 哈希上限 72 字节（后端超限直接 400），前端在提交前
 * 预校验，把「密码 UTF-8 编码后不能超过 72 字节」这类生硬报错
 * 拦在本地并给出可读提示（1 个汉字 = 3 字节，1 个 emoji = 4 字节）。
 */
export function utf8ByteLength(text: string): number {
  let bytes = 0
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i)
    if (code < 0x80) {
      bytes += 1
    } else if (code < 0x800) {
      bytes += 2
    } else if (code >= 0xd800 && code <= 0xdbff) {
      // 代理对（emoji 等）整体算 4 字节
      bytes += 4
      i++
    } else {
      bytes += 3
    }
  }
  return bytes
}
