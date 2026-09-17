import { test, expect } from '@playwright/test'

for (const size of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`connection settings ${size.width}`, async ({ page }) => {
    await page.setViewportSize(size)
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/archive/**', route => route.fulfill({json: {list: [], total: 0, current_account_key: 'default'}}))
    await page.goto('http://127.0.0.1:8765')
    await page.getByRole('button', { name: '连接设置', exact: true }).click()
    await expect(page.getByText('SQLite 已就绪')).toBeVisible()
    await expect(page.getByRole('heading', { name: '连接设置' })).toBeVisible()
    await page.getByRole('button', { name: '检查连接' }).click()
    await expect(page.getByText('连接正常', { exact: true })).toBeVisible({ timeout: 35000 })
    await expect(page.getByText('get_history_messages', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    expect(errors).toEqual([])
    await page.screenshot({ path: `../data/screenshots/connection-${size.width}.png`, fullPage: true })
  })
}
