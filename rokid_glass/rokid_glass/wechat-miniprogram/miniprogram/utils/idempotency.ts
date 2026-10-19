export function createIdempotencyKey(prefix = 'mp'): string {
  const random = Math.random().toString(36).slice(2)
  return `${prefix}_${Date.now()}_${random}`
}
