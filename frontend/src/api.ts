export async function api(path: string, options: RequestInit = {}) {
  const response = await fetch('/api/' + path, options)
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求无效，请检查输入与账户确认')
  return data
}
