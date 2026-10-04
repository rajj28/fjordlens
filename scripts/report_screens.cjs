// Screenshot a generated report (desktop + mobile, list + company + compare) and fail on page errors.
// Usage: node scripts/report_screens.cjs <report_dir> <screens_dir>
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
  for (const [name, viewport] of [['desktop', {width: 1440, height: 1000}], ['mobile', {width: 390, height: 844}]]) {
    const page = await browser.newPage({viewport});
    page.on('pageerror', e => errors.push(`${name}: ${e.message}`));
    page.on('console', m => { if (m.type() === 'error') errors.push(`${name} console: ${m.text()}`); });
    await page.goto(index);
    await page.waitForTimeout(800);
    await page.screenshot({path: path.join(outDir, `${name}-list.png`), fullPage: false});
    const org = await page.evaluate(() => (window.FJ_INDEX && window.FJ_INDEX[0] && window.FJ_INDEX[0].organisation_number) || null);
    if (org) {
      await page.goto(index + '#org=' + org);
      await page.waitForTimeout(1200);
      await page.screenshot({path: path.join(outDir, `${name}-company.png`), fullPage: false});
      const orgs = await page.evaluate(() => window.FJ_INDEX.slice(0, 3).map(r => r.organisation_number).join(','));
      await page.goto(index + '#compare=' + orgs);
      await page.waitForTimeout(1200);
      await page.screenshot({path: path.join(outDir, `${name}-compare.png`), fullPage: false});
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
    if (overflow) errors.push(`${name}: horizontal page overflow`);
    await page.close();
  }
  await browser.close();
  console.log(JSON.stringify({errors}, null, 2));
  process.exit(errors.length ? 1 : 0);
})();
