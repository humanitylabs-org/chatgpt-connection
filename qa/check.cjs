"use strict";
// Run on Thales with Playwright installed under qa/node_modules; uses system Chrome.
const {chromium,devices}=require("playwright"), fs=require("fs"),path=require("path"),cp=require("child_process");
const ROOT=path.resolve(__dirname,".."), OUT=path.join(__dirname,"output");
fs.mkdirSync(OUT,{recursive:true});
const child=cp.spawn(process.env.QA_PYTHON||"/usr/bin/python3",[path.join(__dirname,"host.py")],{cwd:ROOT,env:process.env,stdio:["ignore","pipe","pipe"]});
let logs=""; child.stdout.on("data",x=>logs+=x);child.stderr.on("data",x=>logs+=x);
const assert=(v,m)=>{if(!v)throw Error(m)};
(async()=>{
 let browser;
 try{
  for(let i=0;i<60;i++){try{if((await fetch("http://127.0.0.1:18841/health")).ok)break;}catch{} await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true,executablePath:process.env.QA_CHROME||"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"});
  const results=[];
  for(const mode of ["desktop","mobile"]){
   const context=await browser.newContext(mode==="mobile"?{...devices["Pixel 7"],viewport:{width:412,height:860},colorScheme:"dark"}:{viewport:{width:1100,height:900},colorScheme:"dark"});
   const page=await context.newPage(),errors=[];page.on("pageerror",e=>errors.push(e.message));
   page.on("dialog",d=>d.accept());
   await page.goto("http://127.0.0.1:18841/qa/host.html");
   const frame=page.frames().find(f=>f.url()==="http://127.0.0.1:18841/");assert(frame,"app iframe");
   await frame.getByRole("button",{name:"Connect",exact:true}).waitFor();
   await frame.waitForFunction(()=>document.documentElement.dataset.appearanceSource==="transport");
   const identity=await frame.evaluate(()=>window.__qaIdentity ||= crypto.randomUUID());
   await frame.getByRole("button",{name:"Connect",exact:true}).click();
   await frame.locator("#code").filter({hasText:"ABCD-1234"}).waitFor();
   for(const skin of ["nebula","signal"]){
    await page.evaluate(s=>setSkin(s),skin);
    await frame.waitForFunction(s=>document.documentElement.dataset.skin===s,skin);
    assert(await frame.locator("#code").textContent()==="ABCD-1234","login code survives skin");
    const computed=await frame.evaluate(()=>({canvas:getComputedStyle(document.body).backgroundColor,accent:getComputedStyle(document.documentElement).getPropertyValue("--wz-accent").trim(),width:innerWidth,radius:getComputedStyle(document.querySelector("button")).borderRadius,identity:window.__qaIdentity}));
    assert(computed.canvas==="rgba(0, 0, 0, 0)","transparent hosted canvas");assert(computed.identity===identity,"same document");
    if(skin==="signal")assert(computed.radius==="2px","Signal square buttons");
    await page.screenshot({path:path.join(OUT,`${mode}-${skin}-login.png`),fullPage:true});
    results.push({mode,skin,...computed});
   }
   await frame.evaluate(()=>history.back());
   await frame.locator("#login").waitFor({state:"hidden"});
   assert(await frame.evaluate(()=>window.__qaIdentity)===identity,"Back keeps app open");
   await frame.getByRole("button",{name:"Connect",exact:true}).click();
   await frame.getByRole("button",{name:"Test",exact:true}).waitFor({timeout:12000});
   await frame.getByRole("button",{name:"Test",exact:true}).click();
   await frame.locator("#message").filter({hasText:"connection verified"}).waitFor();
   for(const skin of ["signal","nebula"]){
    await page.evaluate(s=>setSkin(s),skin);await frame.waitForFunction(s=>document.documentElement.dataset.skin===s,skin);
    await page.screenshot({path:path.join(OUT,`${mode}-${skin}-connected.png`),fullPage:true});
   }
   await frame.getByRole("button",{name:"Disconnect",exact:true}).click();
   await frame.getByRole("button",{name:"Connect",exact:true}).waitFor();
   await frame.evaluate(()=>history.back());
   await frame.locator("#message").filter({hasText:"Press back again"}).waitFor();
   assert(errors.length===0,JSON.stringify(errors));
   results.push({mode,controls:"Connect→Back→Connect→approved→Test→Disconnect",back:"nested and root stay open",pageerrors:errors});
   await context.close();
  }
  const context=await browser.newContext({viewport:{width:800,height:800},colorScheme:"dark"});const page=await context.newPage();
  await page.goto("http://127.0.0.1:18841/");await page.getByRole("button",{name:"Connect",exact:true}).waitFor();
  await page.screenshot({path:path.join(OUT,"standalone-dark.png"),fullPage:true});await context.close();
  fs.writeFileSync(path.join(OUT,"results.json"),JSON.stringify(results,null,2));console.log(JSON.stringify({pass:true,results}));
 }finally{if(browser)await browser.close();child.kill("SIGTERM");await new Promise(r=>child.once("exit",r));fs.writeFileSync(path.join(OUT,"server.log"),logs);}
})().catch(e=>{console.error(e);process.exitCode=1});
