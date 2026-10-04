// Exercise the static report's screening panel: a supported query must show an inspectable plan and
// results that open a company; an unsupported query must abstain. Fails on page errors.
// Usage: node scripts/screen_check.cjs <report_dir> <screens_dir>
const {chromium} = require('playwright');
const path = require('node:path');
const fs = require('node:fs');
const url = require('node:url');

(async () => {
  const [reportDir, outDir] = process.argv.slice(2);
  const index = url.pathToFileURL(path.resolve(reportDir, 'index.html')).href;
  fs.mkdirSync(outDir, {recursive: true});
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const errors = [];
  const failures = [];
  for (const [name, viewport] of [['desktop', {width: 1440, height: 1000}], ['mobile', {width: 390, height: 844}]]) {
    const page = await browser.newPage({viewport});
    page.on('pageerror', e => errors.push(`${name}: ${e.message}`));
    page.on('console', m => { if (m.type() === 'error') errors.push(`${name} console: ${m.text()}`); });
    await page.goto(index);
    await page.waitForTimeout(800);
    await page.fill('#screen-query', 'companies in Oslo with more than 5 employees top 10 by revenue');
    await page.click('#screen-form button[type=submit]');
    await page.waitForTimeout(300);
    const plan = await page.$$eval('#screen-results .plan-chip', els => els.map(e => e.textContent));
    const rows = await page.$$eval('#screen-results .screen-row .row-title', els => els.map(e => e.textContent));
    console.log(name, 'plan:', JSON.stringify(plan), 'rows:', rows.length, JSON.stringify(rows.slice(0, 3)));
    if (plan.length !== 3 || rows.length !== 10) failures.push(`${name}: expected 3 plan chips and 10 rows`);
    await page.screenshot({path: path.join(outDir, `${name}-screen.png`), fullPage: false});
    await page.click('#screen-results .screen-row');
    await page.waitForTimeout(1200);
    const title = await page.$eval('h2.company-name', e => e.textContent).catch(() => null);
    console.log(name, 'opened:', title);
    if (!title || title !== rows[0]) failures.push(`${name}: clicking the first result did not open ${rows[0]} (got ${title})`);
    await page.fill('#screen-query', 'find negative social sentiment');
    await page.click('#screen-form button[type=submit]');
    await page.waitForTimeout(300);
    const notice = await page.$eval('#screen-results .notice', e => e.textContent).catch(() => null);
    console.log(name, 'unsupported:', notice);
    if (!notice || !notice.startsWith('Not answered')) failures.push(`${name}: unsupported query did not abstain`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    if (overflow) failures.push(`${name}: horizontal overflow`);
    await page.close();
  }
  await browser.close();
  if (errors.length || failures.length) {
    console.error(JSON.stringify({errors, failures}, null, 2));
    process.exit(1);
  }
  console.log('screen check passed');
})();
