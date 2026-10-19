/**
 * 清洗后端返回的 Markdown 文本，使其在小程序 <text> 组件中以纯文本方式
 * 更可读地展示：去掉标题/加粗/列表/分隔线等标记，并压缩连续空行。
 */
export function cleanMarkdown(text: string): string {
  if (!text) return text

  let s = text

  // 1. 去掉代码块（保留内容，去掉 ``` 和首行语言标记）
  s = s.replace(/```([\s\S]*?)```/g, (_, code) =>
    code.replace(/^[a-zA-Z0-9_+-]+\n/, '').replace(/```/g, '')
  )

  // 2. 去掉行内反引号
  s = s.replace(/`([^`]*)`/g, '$1')

  // 3. 去掉标题标记 #、## 等（行首）
  s = s.replace(/^[ \t]*#{1,6}[ \t]+/gm, '')

  // 4. 去掉加粗/斜体/删除线（按长度优先，避免残留）
  s = s.replace(/\*\*\*(.*?)\*\*\*/g, '$1')   // ***bold+italic***
  s = s.replace(/___(.*?)___/g, '$1')           // ___bold___
  s = s.replace(/\*\*(.*?)\*\*/g, '$1')         // **bold**
  s = s.replace(/__(.*?)__/g, '$1')             // __bold__
  s = s.replace(/\*(.*?)\*/g, '$1')             // *italic*
  s = s.replace(/~~(.*?)~~/g, '$1')             // ~~strike~~

  // 5. 去掉分隔线 ---、***、___（允许中间空白）
  s = s.replace(/^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$/gm, '')

  // 6. 去掉无序列表标记 -、*、+
  s = s.replace(/^[ \t]*[-*+][ \t]+/gm, '')

  // 7. 去掉有序列表标记 1. 2. 等
  s = s.replace(/^[ \t]*\d+\.[ \t]+/gm, '')

  // 8. 去掉引用 >
  s = s.replace(/^[ \t]*>[ \t]?/gm, '')

  // 9. 链接 [text](url) → text；图片 ![alt](url) → alt
  s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
  s = s.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')

  // 10. 压缩连续空白行（>=2 个换行合并为 1 个），并去掉行首多余空格
  s = s.replace(/[ \t]*\n[ \t]*\n[ \t\n]*/g, '\n')
  s = s.replace(/^[ \t]+|[ \t]+$/gm, '')

  return s.trim()
}
