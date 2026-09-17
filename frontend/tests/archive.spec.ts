import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

for (const width of [1440, 390]) {
  test(`offline archive search export backup ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    const conversation = { id: 999911, title: '工程项目协作测试群', conversation_type: 3, message_count: 65, latest_time_readable: '2026-09-17 09:30', sync_status: 'finished' }
    const message = { id: 15, conversation_id: 999911, conversation_title: conversation.title, conversation_type: 3, source_id: 999991,
                      sender_id: 'test-sender', sender_name: '张三', sent_time_readable: '2026-09-17 09:30', message_type: 'TextMessage',
                      text_content: '预算方案已确认。\n<img src=x onerror="window.__chatUnsafe=true">', content: {} }
    let exportedQuery = ''
    const backup = 'workbench-20260917-000000-' + 'a'.repeat(32) + '.sqlite3'
    await page.context().route('**/api/archive/**', async route => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith('/sources')) await route.fulfill({ json: { list: [{ id: 999991, label: '测试来源', account_key: 'default' }], current_account_key: 'default' } })
      else if (url.pathname.endsWith('/conversations')) await route.fulfill({ json: { total: 1, list: [conversation] } })
      else if (url.pathname.endsWith('/messages')) await route.fulfill({ json: { total: url.searchParams.get('focus_id') ? 65 : url.searchParams.get('q') ? 1 : 2, page: url.searchParams.get('focus_id') ? 2 : 1, list: [message,
        ...(!url.searchParams.get('q') && !url.searchParams.get('focus_id') ? [{ ...message, id: 16, message_type: 'FileMessage', text_content: '', content: { content: { name: '预算评审.xlsx', localPath: 'C:/archive/预算评审.xlsx' } } }] : [])] } })
      else if (url.pathname.endsWith('/export')) {
        exportedQuery = url.searchParams.get('q') || ''
        await route.fulfill({ body: '\ufefftext_content\r\n预算方案已确认\r\n', headers: { 'Content-Type': 'text/csv; charset=utf-8', 'Content-Disposition': 'attachment; filename="chat-export.csv"' } })
      } else if (url.pathname.endsWith('/backups')) await route.fulfill({ json: { filename: backup, size: 1000 } })
      else await route.fulfill({ body: 'SQLite format 3\x00', headers: { 'Content-Type': 'application/octet-stream', 'Content-Disposition': `attachment; filename="${backup}"` } })
    })
    await page.goto('http://127.0.0.1:8765')
    await expect(page.getByRole('heading', { name: '聊天归档' })).toBeVisible()
    await expect(page.locator('.message-text').first()).toContainText('预算方案')
    expect(await page.locator('img[src="x"]').count()).toBe(0)
    expect(await page.evaluate(() => (window as unknown as {__chatUnsafe?: boolean}).__chatUnsafe)).toBeUndefined()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: `../data/screenshots/archive-${width}.png`, fullPage: true, animations: 'disabled' })
    await page.locator('.archive-toolbar .el-radio-button').nth(1).click()
    await page.getByPlaceholder('消息关键词').fill('预算')
    await page.getByRole('button', { name: '查询', exact: true }).click()
    await expect(page.locator('.result-conversation')).toBeVisible()
    await page.getByPlaceholder('消息关键词').fill('未提交的关键词')
    await page.getByRole('button', { name: '导出', exact: true }).click()
    const downloaded = page.waitForEvent('download')
    await page.getByText('CSV', { exact: true }).click()
    const file = await downloaded
    expect(file.suggestedFilename()).toBe('chat-export.csv')
    expect(await readFile((await file.path())!, 'utf8')).toContain('预算方案')
    expect(exportedQuery).toBe('预算')
    await page.locator('.result-conversation').click()
    await expect(page.locator('#message-15')).toHaveClass(/focused/)
    await expect(page.locator('.message-pane .el-pagination .is-active')).toHaveText('2')
    expect(await page.getByPlaceholder('消息关键词').inputValue()).toBe('')
    await page.getByRole('button', { name: '备份数据库', exact: true }).click()
    const backupDownloaded = page.waitForEvent('download')
    await page.getByRole('button', { name: '创建备份', exact: true }).click()
    expect((await backupDownloaded).suggestedFilename()).toBe(backup)
    await expect(page.locator('.backup-file')).toContainText(backup)
    expect(errors).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await expect(page.locator('.el-message')).toHaveCount(0, { timeout: 6000 })
    await page.screenshot({ path: `../data/screenshots/archive-focus-${width}.png`, fullPage: true, animations: 'disabled' })
  })
}
