import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

for (const width of [1440, 390]) {
  test(`requirement extraction preview ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    const conversation = { id: 999911, title: '需求提取测试群', conversation_type: 3, message_count: 80 }
    const message = { id: 999901, conversation_id: conversation.id, conversation_title: conversation.title, conversation_type: 3, source_id: 999991,
      sender_id: 'test', sender_name: '测试人员', sent_time_readable: '2026-09-17 10:11', message_type: 'TextMessage', text_content: '问答增加时间显示', content: {} }
    let queriedTimes: number[] = []
    await page.context().route('**/api/archive/**', async route => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith('/sources')) await route.fulfill({ json: { list: [{ id: 999991, label: '测试来源', account_key: 'default' }], current_account_key: 'default' } })
      else if (url.pathname.endsWith('/conversations')) await route.fulfill({ json: { total: 1, list: [conversation] } })
      else {
        if (url.searchParams.has('start_time')) queriedTimes = [Number(url.searchParams.get('start_time')), Number(url.searchParams.get('end_time'))]
        await route.fulfill({ json: { total: 80, page: 1, list: [message] } })
      }
    })
    let preparedScope: Record<string, number> = {}
    let started = false
    let savedSummary = ''
    let registrations = 0
    let task = { id: 999999, source_id: 999991, conversation_id: conversation.id, start_time: 0, end_time: 0, status: 'draft',
      conversation_title: conversation.title, source_label: '测试来源', base_url: 'http://example.com/v1', model: 'test-model', total_messages: 80,
      text_messages: 70, skipped_messages: 10, total_chunks: 2, completed_chunks: 0, error: '', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), registration: {} as {added?: number; skipped?: number; items?: {candidate_id: number; requirement_id: string; duplicate: boolean}[]}, candidates: [] as {id: number; summary: string; uncertainties: string; selected: boolean; evidence_ids: number[]}[], evidence: [] as {id: number; time: string; sender: string; direction: string; text: string}[] }
    await page.context().route('**/api/extraction/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (path.endsWith('/prepare')) {
        preparedScope = route.request().postDataJSON()
        task = { ...task, ...preparedScope }
        await route.fulfill({ json: task })
      } else if (path.endsWith('/start')) {
        expect(route.request().postDataJSON()).toEqual({ confirmed: true })
        started = true
        task = { ...task, status: 'finished', completed_chunks: 2,
          candidates: [{ id: 1, summary: '问题与回答下方增加时间显示，当年省略年份、往年包含年份', uncertainties: '往年示例年份需确认', selected: true, evidence_ids: [message.id] }],
          evidence: [{ id: message.id, time: message.sent_time_readable, sender: '测试人员', direction: '发出', text: message.text_content }] }
        await route.fulfill({ json: task })
      } else if (path.endsWith('/review')) {
        const rows = route.request().postDataJSON().rows
        savedSummary = rows[0].summary
        task.candidates = rows.map((row: object) => ({ ...task.candidates[0], ...row }))
        await route.fulfill({ json: task })
      } else if (path.endsWith('/registration-preview')) {
        await route.fulfill({ json: { task_id: task.id, expected_updated_at: task.updated_at, added: 1, skipped: 0,
          rows: task.candidates.map(row => ({ ...row, duplicate_id: '', duplicate_candidate_id: null, similar: [{ id: 'REQ-20260916-000001', summary: '历史时间显示需求' }] })) } })
      } else if (path.endsWith('/register')) {
        expect(route.request().postDataJSON()).toEqual({ confirmed: true, expected_updated_at: task.updated_at })
        registrations++
        task.registration = { added: 1, skipped: 0, items: [{ candidate_id: 1, requirement_id: 'REQ-20260917-000002', duplicate: false }] }
        await route.fulfill({ json: { receipt: task.registration, file_ready: true, error: '' } })
      } else if (path.endsWith('/requirements/rebuild')) {
        await route.fulfill({ json: { file_ready: true, filename: '需求清单.csv' } })
      } else if (path.endsWith('/requirements/file')) {
        await route.fulfill({ body: '\ufeff需求编号,需求概述\r\nREQ-20260917-000002,' + savedSummary + '\r\n', contentType: 'text/csv; charset=utf-8' })
      } else if (path.endsWith('/requirements')) {
        await route.fulfill({ json: { total: registrations, file_state: registrations ? 'ready' : 'missing', list: registrations ? [{ id: 'REQ-20260917-000002', source_label: '测试来源', account_key: 'default', conversation_title: task.conversation_title, task_id: task.id, evidence_ids: [message.id], summary: savedSummary, uncertainties: '往年示例年份需确认', status: '待评审' }] : [] } })
      } else if (path.endsWith('/tasks')) await route.fulfill({ json: { list: started ? [task] : [] } })
      else await route.fulfill({ json: task })
    })
    await page.goto('http://127.0.0.1:8765')
    const extract = page.getByRole('button', { name: '提取需求', exact: true })
    await expect(extract).toBeDisabled()
    await page.getByPlaceholder('开始日期').fill('2026-09-17')
    await page.getByPlaceholder('截止日期').fill('2026-09-17')
    await page.getByPlaceholder('截止日期').press('Tab')
    await page.getByPlaceholder('消息关键词').fill('显示')
    await page.getByPlaceholder('发送者姓名或 ID').fill('测试人员')
    await page.getByRole('button', { name: '查询', exact: true }).click()
    await expect(extract).toBeEnabled()
    await page.getByPlaceholder('消息关键词').fill('未提交的关键词')
    await extract.click()
    await expect(page.getByRole('dialog', { name: '确认提取需求' })).toBeVisible()
    expect(preparedScope.source_id).toBe(999991)
    expect(preparedScope.conversation_id).toBe(conversation.id)
    expect(Object.keys(preparedScope).sort()).toEqual(['conversation_id', 'end_time', 'source_id', 'start_time'])
    expect(preparedScope.start_time).toBeLessThan(preparedScope.end_time)
    expect([preparedScope.start_time, preparedScope.end_time]).toEqual(queriedTimes)
    expect(await page.evaluate(times => times.map(value => {
      const date = new Date(value)
      return [date.getFullYear(), date.getMonth() + 1, date.getDate(), date.getHours(), date.getMinutes(), date.getSeconds(), date.getMilliseconds()]
    }), queriedTimes)).toEqual([[2026, 9, 17, 0, 0, 0, 0], [2026, 9, 17, 23, 59, 59, 999]])
    await expect(page.getByText('80 条消息，70 条文本，10 条无文本消息未分析')).toBeVisible()
    await expect(page.getByRole('button', { name: '开始分析', exact: true })).toBeDisabled()
    expect(started).toBeFalsy()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: `../data/screenshots/extraction-confirm-${width}.png`, fullPage: true, animations: 'disabled' })
    await page.getByText('确认将以上范围的聊天文本发送至该模型服务，可能产生调用费用', { exact: true }).click()
    await page.getByRole('button', { name: '开始分析', exact: true }).click()
    const preview = page.getByRole('dialog', { name: '需求候选预览' })
    await expect(preview).toBeVisible()
    await preview.getByText('来源消息（1 条）').click()
    await expect(preview.locator('.extraction-evidence')).toContainText('发出')
    await expect(preview.locator('.extraction-evidence')).toContainText('问答增加时间显示')
    await preview.locator('textarea').first().fill('问题和回答的快捷按钮栏增加时间显示')
    await page.getByRole('button', { name: '保存候选草稿', exact: true }).click()
    expect(savedSummary).toBe('问题和回答的快捷按钮栏增加时间显示')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: `../data/screenshots/extraction-preview-${width}.png`, fullPage: true, animations: 'disabled' })
    await preview.getByRole('button', { name: '关闭', exact: true }).click()
    await page.getByRole('button', { name: '查看结果', exact: true }).click()
    await expect(preview.locator('textarea').first()).toHaveValue(savedSummary)
    await preview.getByRole('button', { name: '登记需求', exact: true }).click()
    const registration = page.getByRole('dialog', { name: '确认登记需求' })
    await expect(registration).toBeVisible()
    await expect(registration.getByText('预计新增 1 项，重复跳过 0 项')).toBeVisible()
    await expect(registration.getByText('REQ-20260916-000001：历史时间显示需求')).toBeVisible()
    await expect(registration.getByRole('button', { name: '确认登记', exact: true })).toBeDisabled()
    expect(registrations).toBe(0)
    await registration.getByRole('button', { name: '返回修改', exact: true }).click()
    expect(registrations).toBe(0)
    await preview.getByRole('button', { name: '登记需求', exact: true }).click()
    await expect(page.locator('.el-message')).toHaveCount(0, { timeout: 6000 })
    await page.screenshot({ path: `../data/screenshots/registration-confirm-${width}.png`, fullPage: true, animations: 'disabled' })
    await registration.getByText('已核对选中需求，确认备案；登记后本任务锁定，未勾选项不登记', { exact: true }).click()
    await registration.getByRole('button', { name: '确认登记', exact: true }).click()
    await expect(preview.getByText('备案编号：REQ-20260917-000002')).toBeVisible()
    await expect(preview.locator('textarea').first()).toBeDisabled()
    await expect(preview.getByRole('button', { name: '登记需求', exact: true })).toHaveCount(0)
    expect(registrations).toBe(1)
    await preview.getByRole('button', { name: '关闭', exact: true }).click()
    await page.getByRole('button', { name: '需求清单', exact: true }).click()
    const ledger = page.getByRole('dialog', { name: '需求清单', exact: true })
    await expect(ledger.locator('.ledger-summary')).toHaveText(savedSummary)
    await ledger.getByRole('button', { name: '重新生成 CSV', exact: true }).click()
    await expect(ledger.getByText('CSV 已就绪')).toBeVisible()
    const downloaded = page.waitForEvent('download')
    await ledger.getByRole('button', { name: '下载 CSV', exact: true }).click()
    const file = await downloaded
    expect(file.suggestedFilename()).toBe('需求清单.csv')
    expect(await readFile((await file.path())!, 'utf8')).toContain(savedSummary)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await expect(page.locator('.el-message')).toHaveCount(0, { timeout: 6000 })
    await page.screenshot({ path: `../data/screenshots/requirement-ledger-${width}.png`, fullPage: true, animations: 'disabled' })
    await ledger.getByRole('button', { name: '关闭', exact: true }).click()
    await page.locator('.archive-toolbar .el-radio-button').nth(1).click()
    await expect(extract).toBeDisabled()
    expect(errors).toEqual([])
  })
}
