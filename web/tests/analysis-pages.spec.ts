import { expect, test } from '@playwright/test'
import { countWords } from '../src/lib/analysis'

test('review decisions update the profile and persist with notes after reload', async ({
  page,
}) => {
  await page.goto('/')
  const navigation = page.getByRole('navigation', { name: 'Main navigation' })
  await navigation.getByRole('button', { name: /^Passage review/ }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Passage review' })).toBeVisible()
  await page.locator('.passage-list-item').first().click()
  const passageWords = countWords(await page.locator('.passage-prose').innerText())
  await page
    .getByRole('textbox', { name: 'Passage review note' })
    .fill('Reviewed in browser: omit this passage from this profile.')
  await page.getByRole('button', { name: 'Exclude', exact: true }).click()
  await navigation.getByRole('button', { name: 'Style atlas', exact: true }).click()
  await expect(page.locator('.style-stats .stat-card').first().locator('.stat-number')).toHaveText(
    String(432 - passageWords),
  )
  await page.reload()
  await navigation.getByRole('button', { name: /^Passage review/ }).click()
  await page.locator('.passage-list-item').first().click()
  await expect(page.getByRole('textbox', { name: 'Passage review note' })).toHaveValue(
    'Reviewed in browser: omit this passage from this profile.',
  )
  await expect(
    page
      .getByRole('region', { name: 'Selected passage' })
      .getByRole('button', { name: 'Excluded', exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Approve passage', exact: true }).click()
  await navigation.getByRole('button', { name: 'Style atlas', exact: true }).click()
  await expect(page.locator('.style-stats .stat-card').first().locator('.stat-number')).toHaveText(
    '432',
  )
})

test('profile scope, review search, and source details are usable', async ({ page }) => {
  await page.goto('/')
  const navigation = page.getByRole('navigation', { name: 'Main navigation' })
  await navigation.getByRole('button', { name: 'Style atlas', exact: true }).click()
  await expect(page.locator('.style-stats .stat-card').first().locator('.stat-number')).toHaveText(
    '432',
  )
  await page
    .locator('.style-scope')
    .getByRole('button', { name: /^Conclusion/ })
    .click()
  await expect(
    page.getByRole('heading', { name: 'No approved conclusion passages yet' }),
  ).toBeVisible()
  await page.locator('.style-scope').getByRole('button', { name: 'Whole corpus' }).click()
  await expect(page.getByRole('img', { name: /^Sentence length histogram/ })).toBeVisible()
  await navigation.getByRole('button', { name: /^Passage review/ }).click()
  await page.getByRole('textbox', { name: 'Search passages' }).fill('methods_duplicate.docx')
  await expect(page.locator('.passage-list-item')).toHaveCount(1)
  await expect(page.getByText('Identical text appears elsewhere')).toBeVisible()
  await page.getByText('Source & integrity details', { exact: true }).click()
  await expect(page.getByText('Passage SHA-256', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: 'Search passages' }).fill('not-a-real-passage-537381')
  await expect(page.getByText('No matching passages.')).toBeVisible()
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click()
  await expect(page.locator('.passage-list-item')).toHaveCount(7)
})
