import { test, expect } from '@playwright/test'

for (const outcome of ['saved', 'cancelled', 'error']) {
  test(`desktop export ${outcome} uses native saving without browser fallback`, async ({ page }) => {
    const calls: { url: string; filename: string }[] = []
    await page.exposeFunction('nativeSave', (url: string, filename: string) => {
      calls.push({ url, filename })
      return outcome === 'error' ? { error: '测试：目标文件被占用' } : { saved: outcome === 'saved' }
    })
    await page.addInitScript(() => {
      const host = window as unknown as { nativeSave: (url: string, filename: string) => Promise<{ saved: boolean }>; pywebview: unknown }
      host.pywebview = { api: { save_file: host.nativeSave } }
    })
    let browserDownload = false
    page.on('download', () => { browserDownload = true })
    await page.route('**/api/archive/**', route => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith('/sources')) return route.fulfill({ json: { list: [{ id: 1, label: '测试来源', account_key: 'default' }], current_account_key: 'default' } })
      if (url.pathname.endsWith('/conversations')) return route.fulfill({ json: { total: 1, list: [{ id: 2, title: '测试会话', conversation_type: 3 }] } })
      if (url.pathname.endsWith('/messages')) return route.fulfill({ json: { total: 1, page: 1, list: [{ id: 3, conversation_id: 2, sender_name: '测试', sent_time_readable: '', message_type: 'TextMessage', text_content: '测试消息', content: {} }] } })
      throw new Error('Desktop export must not use browser HTTP download')
    })
    await page.goto('http://127.0.0.1:8765')
    await expect(page.locator('.message-text')).toContainText('测试消息')
    await page.getByRole('button', { name: '导出', exact: true }).click()
    await page.getByText('CSV', { exact: true }).click()
    await expect.poll(() => calls.length).toBe(1)
    expect(calls[0].filename).toBe('聊天归档.csv')
    expect(calls[0].url).toContain('/api/archive/export?')
    expect(calls[0].url).toContain('format=csv')
    if (outcome === 'error') await expect(page.getByText('测试：目标文件被占用')).toBeVisible()
    expect(browserDownload).toBe(false)
  })
}
