// Verify real researcher tasks and adversarial evidence cases in the generated report.
// Usage: node scripts/report_tasks.cjs <report_dir> [server_url]
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');

(async()=>{
  const index=pathToFileURL(path.resolve(process.argv[2],'index.html')).href;
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const errors=[],results=[];
  try{
    for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
      const page=await browser.newPage({viewport});
      page.on('pageerror',e=>errors.push(`${name}: ${e.message}`));
      await page.goto(index);
      await page.waitForSelector('.company-name');
      const orgs=await page.evaluate(()=>window.FJ_INDEX.slice(0,2).map(p=>p.organisation_number));
      await page.fill('#search',orgs[1]);
      await page.click(`#company-list [data-company="${orgs[1]}"]`);
      await page.waitForFunction(org=>current?.organisation_number===org,orgs[1]);
      const selectedTop=await page.locator('#profile').evaluate(e=>e.getBoundingClientRect().top);
      assert(selectedTop>=0&&selectedTop<40,`${name}: selected profile not brought into view (${selectedTop})`);
      assert.equal(await page.evaluate(()=>document.activeElement.id),'profile');
      await page.click('[data-tab="financials"]');
      await page.locator('#tab-content [data-claim]').first().click();
      assert(await page.locator('#evidence-content').textContent().then(t=>t.includes('CONTENT SHA-256')));
      await page.click('#close-evidence');
      await page.click('#compare-button');
      assert(await page.locator('#compare-panel').isVisible());
      assert((await page.locator('#compare-panel').boundingBox()).y<40,`${name}: comparison not revealed`);
      await page.click('#add-compare');
      await page.fill('#search',orgs[0]);
      await page.click(`#company-list [data-company="${orgs[0]}"]`);
      await page.waitForFunction(org=>current?.organisation_number===org,orgs[0]);
      await page.click('#compare-button');
      await page.waitForSelector('#compare-panel thead th:nth-child(3)');
      await page.locator(`#compare-panel [data-evidence-org="${orgs[1]}"]`).first().click();
      assert((await page.locator('#evidence-content').innerText()).includes(orgs[1]),`${name}: comparison opened wrong company's source`);
      await page.click('#close-evidence');
      // A same-document permalink must open and focus the requested profile.
      await page.goto(index+'#org='+orgs[0]);
      await page.waitForFunction(()=>compare.length===0&&activeTab==='overview');
      await page.click('[data-tab="ask"]');
      const firstPrompt=page.locator('#tab-content [data-question]').first();
      assert(await firstPrompt.count()>0,`${name}: no answerable guided prompt is shown`);
      const selectedQuestion=await firstPrompt.getAttribute('data-question');
      await firstPrompt.click();
      await page.waitForFunction(()=>!document.querySelector('#answer').textContent.includes('Checking'));
      const answer=await page.locator('#answer').innerText();
      assert(!answer.startsWith('Found '),`${name}: ${selectedQuestion} answer is still a claim count`);
      assert(answer.length>30,`${name}: ${selectedQuestion} answer missing`);
      await page.click('[data-tab="overview"]');
      assert(await page.locator('.attention').isVisible(),`${name}: attention brief missing`);
      assert.equal(await page.locator('.attention-card').count(),4,`${name}: expected four attention categories`);
      const askAction=page.getByRole('button',{name:'Ask about this company'});
      assert(await askAction.isVisible(),`${name}: overview Ask action missing`);
      await askAction.click();
      assert(await page.locator('.ask-box').isVisible(),`${name}: guided Ask panel did not open`);
      assert(await page.locator('.ask-box [data-question]').count()>0,`${name}: answerable prompt suggestions missing`);
      await page.locator('#question').fill('predict next year revenue');
      await page.locator('#ask-form button[type="submit"]').click();
      await page.waitForFunction(()=>!document.querySelector('#answer').textContent.includes('Checking'));
      assert((await page.locator('#answer').innerText()).includes('does not forecast future results'),`${name}: actionable unsupported explanation missing`);
      assert(await page.locator('#answer [data-question]').count()>0,`${name}: unsupported answer omitted suggested questions`);
      if(process.argv[3]){
        const queries=await page.evaluate(()=>[...window.FJ_ANSWER_CONFIG.questions,window.FJ_ANSWER_CONFIG.online,window.FJ_ANSWER_CONFIG.overview,'Who leads it?','revenue and employees','financial distress','leadership and online presence','news and changes','predict revenue','is it fraudulent?','unrecognised topic']);
        for(const query of queries){
          const staticResult=await page.evaluate(q=>localAnswer(current,q),query);
          const response=await fetch(process.argv[3]+`/api/ask?org=${orgs[0]}&q=${encodeURIComponent(query)}`);
          const serverResult=await response.json();
          assert.equal(staticResult.answer,serverResult.answer,`static/server answer mismatch: ${query}`);
          assert.equal(staticResult.answerable,serverResult.answerable,`static/server answerability mismatch: ${query}`);
          assert.deepEqual([...staticResult.claim_ids].sort(),[...serverResult.claim_ids].sort(),`static/server cited claims mismatch: ${query}`);
          assert.deepEqual([...staticResult.evidence_ids].sort(),[...serverResult.evidence_ids].sort(),`static/server cited evidence mismatch: ${query}`);
          assert.deepEqual(staticResult.suggested_questions||[],serverResult.suggested_questions||[],`static/server suggested questions mismatch: ${query}`);
          assert.deepEqual(staticResult.unsupported_or_uncertain||[],serverResult.unsupported_or_uncertain||[],`static/server coverage notes mismatch: ${query}`);
        }
      }
      // Distinct periods/currencies must never produce an apparently comparable ratio.
      const guard=await page.evaluate(()=>{
        const p={organisation_number:'test',claims:[
          {id:'eq',family:'financials',field:'financial_equity',scope:'legal_entity',availability:'available',value:{amount:'50',currency:'NOK'},reporting_period:{fraDato:'2025-01-01',tilDato:'2025-12-31'}},
          {id:'assets',family:'financials',field:'financial_assets',scope:'legal_entity',availability:'available',value:{amount:'100',currency:'NOK'},reporting_period:{fraDato:'2024-01-01',tilDato:'2024-12-31'}},
          {id:'stale',family:'financials',field:'financial_revenue',scope:'legal_entity',availability:'available',stale:true,value:{amount:'100',currency:'NOK'},reporting_period:{fraDato:'2026-01-01',tilDato:'2026-12-31'}},
          {id:'group',family:'financials',field:'financial_assets',scope:'group',availability:'available',value:{amount:'999',currency:'NOK'},reporting_period:{fraDato:'2025-01-01',tilDato:'2025-12-31'}}
        ]};
        return companyFinancials(p).map(c=>c.id);
      });
      assert.deepEqual(guard,['eq']);
      // A removed/changed claim must expose both saved observations, even if the old claim no longer exists.
      await page.evaluate(()=>{
        current={...current,refresh:{...current.refresh,previous_run_id:'previous-test',stale_claim_ids:[]},changes:[{id:'change-test',field:'employees',type:'changed_employee_count',material:true,previous_value:4,current_value:8,previous_evidence_ids:['ev-before'],current_evidence_ids:['ev-after'],observed_at:'2026-10-17'}],evidence:[...current.evidence,{id:'ev-before',source_url:'https://example.com/before',claim_span:'previous count 4',content_sha256:'a'.repeat(64)},{id:'ev-after',source_url:'https://example.com/after',claim_span:'current count 8',content_sha256:'b'.repeat(64)}]};
      });
      await page.click('[data-tab="changes"]');
      await page.click('[data-change="change-test"][data-side="previous"]');
      assert((await page.locator('#evidence-content').innerText()).includes('previous count 4'));
      await page.click('#close-evidence');
      await page.click('[data-change="change-test"][data-side="current"]');
      assert((await page.locator('#evidence-content').innerText()).includes('current count 8'));
      await page.click('#close-evidence');
      assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)),`${name}: horizontal page overflow`);
      results.push({viewport:name,selectedProfileTop:selectedTop,tasks:['find company','inspect citation','compare with citations','same-document permalink','direct leadership answer','period/scope guards','previous/current change evidence']});
      await page.close();
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,results},null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
