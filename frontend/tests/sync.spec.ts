import { test, expect } from '@playwright/test'

for (const width of [1440, 390]) {
  test(`manual sync ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/archive/**', route => route.fulfill({json: {list: [], total: 0, current_account_key: 'default'}}))
    let created = false
    const candidate = { conversation_type: 3, target_id: 'test-group', title: '工程项目协作测试群' }
    await page.route('**/api/discovery/conversations?*', route => route.fulfill({ json: { total: 1, list: [candidate] } }))
    await page.route('**/api/sync/tasks*', async route => {
      if (route.request().method() === 'POST') {
        const data = route.request().postDataJSON()
        expect(data.account_confirmed).toBe(true)
        expect(data.conversations).toEqual([candidate])
        expect(data.end_time - data.start_time).toBe(30 * 86400000)
        created = true
        await route.fulfill({ status: 201, json: { id: 999 } })
      } else {
        await route.fulfill({ json: { total: created ? 1 : 0, list: created ? [{
          id: 999, status: 'warning', source_label: '测试来源', account_key: 'default', start_time: Date.now()-30*86400000,
          end_time: Date.now(), created_at: '', cancel_requested: false, message: '',
          items: [{ ...candidate, id: 1, status: 'warning', pages: 2, fetched: 100, inserted: 75,
                    oldest: Date.now()-86400000, newest: Date.now(), message: '时间游标停滞，可能存在同时间戳边界遗漏' }],
        }] : [] } })
      }
    })
    await page.goto('http://127.0.0.1:8765')
    await page.getByRole('button', {name: '同步管理', exact: true}).click()
    await expect(page.getByRole('heading', {name: '同步管理'})).toBeVisible()
    await expect(page.getByRole('button', {name: '开始同步'})).toBeDisabled()
    await page.getByRole('button', {name: '发现会话'}).click()
    await page.locator('.candidate .el-checkbox').click()
    await expect(page.getByRole('checkbox', {name: `选择 ${candidate.title}`})).toBeChecked()
    await page.locator('.confirm-row .el-checkbox').click()
    await page.getByRole('button', {name: '开始同步'}).click()
    await expect(page.getByText('任务 #999', {exact: true})).toBeVisible()
    await expect(page.getByText('时间游标停滞，可能存在同时间戳边界遗漏')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    expect(errors).toEqual([])
    await expect(page.locator('.el-message')).toHaveCount(0, {timeout: 6000})
    await page.screenshot({ path: `../data/screenshots/sync-${width}.png`, fullPage: true, animations: 'disabled' })
  })
}
