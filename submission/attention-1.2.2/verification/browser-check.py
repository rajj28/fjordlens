import json,os,pathlib,subprocess,sys,time,urllib.request
source=pathlib.Path(sys.argv[1]); data=pathlib.Path(sys.argv[2]); output=pathlib.Path(sys.argv[3])
output.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(source))
from fjordlens.synthesis import summarize
from fjordlens.report import write_report
profiles=[json.loads(x) for x in data.read_text(encoding='utf-8').splitlines() if x.strip()]
for p in profiles:p['summary']=summarize(p)
prepared=output/'envelopes.jsonl'
prepared.write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in profiles),encoding='utf-8')
start=time.monotonic(); report=write_report(profiles,output/'report')
timing={'kind':'offline viewer regeneration from existing captures','input_profiles':len(profiles),'seconds':round(time.monotonic()-start,3),'report':report}
(output/'generation.json').write_text(json.dumps(timing,indent=2)+'\n',encoding='utf-8')
env=os.environ.copy();env['NODE_PATH']=env.get('NODE_PATH',str(source/'node_modules'))
server=subprocess.Popen([sys.executable,'-m','fjordlens','serve','--data',str(prepared),'--port','8897'],cwd=source,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env)
results={}
try:
    for _ in range(50):
        try:urllib.request.urlopen('http://127.0.0.1:8897/api/companies',timeout=1);break
        except OSError:time.sleep(.1)
    live=r'''const {chromium}=require('playwright');(async()=>{const b=await chromium.launch({channel:'msedge',headless:true});try{let results=[];for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){const p=await b.newPage({viewport});let errors=[];p.on('pageerror',e=>errors.push(e.message));await p.goto('http://127.0.0.1:8897');await p.waitForSelector('.company-name');await p.locator('.company-name').first().click();await p.waitForSelector('.attention');await p.locator('.tab[data-tab="ask"]').click();await p.waitForTimeout(300);const prompts=await p.locator('#tab-content [data-question]').count();if(prompts){await p.locator('#tab-content [data-question]').first().click();await p.waitForFunction(()=>document.querySelector('#answer')?.textContent.length>0&&!document.querySelector('#answer').textContent.includes('Checking'));}results.push({name,prompts,errors});await p.close();}console.log(JSON.stringify({passed:results.every(x=>x.prompts>0&&!x.errors.length),results},null,2));if(results.some(x=>!x.prompts||x.errors.length))process.exitCode=1;}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1});'''
    jobs=[('live-ui',['node','-e',live]),('browser-tasks',['node',str(source/'scripts/report_tasks.cjs'),str(output/'report'),'http://127.0.0.1:8897']),('screens',['node',str(source/'scripts/report_screens.cjs'),str(output/'report'),str(output/'screens')])]
    for name,command in jobs:
        p=subprocess.run(command,cwd=source,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
        (output/(name+'.log')).write_text(p.stdout+p.stderr,encoding='utf-8')
        results[name]={'exit_code':p.returncode,'output':(p.stdout+p.stderr)[-2200:]}
finally:
    server.terminate();server.wait(timeout=10)
print(json.dumps({'generation':timing,'checks':results},indent=2))
sys.exit(0 if all(r['exit_code']==0 for r in results.values()) else 1)
