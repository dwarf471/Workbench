import { test, expect } from '@playwright/test'

for (const width of [1440, 390]) {
  test(`workspace design ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname
      let json: unknown = { list: [], total: 0 }
      if (path.endsWith('/settings')) json = { account_key: 'default', account_label: '本地账户', mcp_url: '', settings_path: '' }
      if (path.endsWith('/health')) json = { database: { ready: true, revision: '0004', path: 'D:/workspace/data/workbench.sqlite' } }
      if (path.endsWith('/sources')) json = { list: [{ id: 1, label: '本地账户', account_key: 'default' }], current_account_key: 'default' }
      if (path.endsWith('/conversations')) json = { list: [{ id: 1, title: '工作台设计讨论', conversation_type: 3, message_count: 2, sync_status: 'finished', latest_time_readable: '2026-09-17 14:30' }], total: 1 }
      if (path.endsWith('/messages')) json = { list: [{ id: 1, sender_name: '设计讨论', sent_time_readable: '2026-09-17 14:30', message_type: 'TextMessage', text_content: '白色画布、清晰导航，保留足够的阅读空间。\n' + 'Long-message-path-'.repeat(15), content: {} }], total: 1, page: 1 }
      return route.fulfill({ json })
    })
    await page.goto('http://127.0.0.1:8765')
    if (width < 1024) await page.getByRole('button', { name: '工作空间菜单', exact: true }).click()
    await page.getByRole('button', { name: /工作台设计讨论/ }).click()
    await expect(page.locator('.message-text')).toBeVisible()
    await page.evaluate(async () => {
      await document.fonts.load('400 16px "Inter Variable"', 'Workbench')
      await document.fonts.load('600 36px "Noto Sans SC Variable"', '聊天归档')
      await document.fonts.ready
    })
    expect(await page.evaluate(() => document.fonts.check('400 16px "Inter Variable"', 'Workbench'))).toBeTruthy()
    expect(await page.evaluate(() => document.fonts.check('600 36px "Noto Sans SC Variable"', '聊天归档'))).toBeTruthy()
    expect(await page.locator('h1').evaluate(node => getComputedStyle(node).fontFamily)).toContain('Noto Sans SC Variable')
    expect(await page.locator('main').evaluate(node => getComputedStyle(node).maxWidth)).toBe('1280px')
    expect(await page.getByRole('button', { name: '查询', exact: true }).evaluate(node => getComputedStyle(node).borderRadius)).toBe('8px')
    expect(await page.getByRole('button', { name: '查询', exact: true }).evaluate(node => getComputedStyle(node).backgroundColor)).toBe('rgb(86, 69, 212)')
    expect(await page.locator('.message-text').evaluate(node => getComputedStyle(node).fontSize)).toBe('16px')
    expect(await page.locator('h1').evaluate(node => getComputedStyle(node).fontSize)).toBe(width < 480 ? '36px' : '48px')
    await expect(page.getByRole('button', { name: '聊天归档', exact: true })).toHaveAttribute('aria-current', 'page')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: `../data/screenshots/design-archive-${width}.png`, fullPage: true })
    await page.getByRole('button', { name: '连接设置', exact: true }).click()
    await expect(page.getByRole('heading', { name: '连接设置', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: `../data/screenshots/design-settings-${width}.png`, fullPage: true })
    expect(errors).toEqual([])
  })
}
