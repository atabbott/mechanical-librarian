"""Self-contained setup assets; no normal workspace code is loaded before handoff."""

from __future__ import annotations

import html
import json
from importlib.resources import files

SETUP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <script src="/assets/theme.js"></script>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Set up Mechanical Librarian</title>
  <link rel="preload" href="/assets/setup-handoff.css" as="style">
  <link id="setup-handoff-motion" rel="stylesheet" href="/assets/setup-handoff.css" disabled>
  <link rel="stylesheet" href="/assets/setup.css">
</head>
<body>
  <a class="skip-link" href="#setup-main">Skip to setup</a>
  <header class="masthead"><span class="book-mark" aria-hidden="true">ML</span><strong>Mechanical Librarian</strong></header>
  <div class="setup-shell">
    <aside aria-label="Setup progress">
      <h1>Set up your library</h1>
      <p>Connect Audiobookshelf and choose what Mechanical Librarian will review.</p>
      <section class="prerequisites">
        <h2>Before you start</h2>
        <ul>
          <li>A prepared owner-only review-bootstrap.json, mounted by this deployment. Bare Compose without this prepared bootstrap is not supported.</li>
          <li>The browser mode this deployment publishes: plain HTTP on a network you control, direct HTTPS with its own trusted certificate, or HTTPS terminated at one trusted proxy.</li>
          <li>Read-only mounts for your book folders, declared by the deployment. Setup compares them and never creates one.</li>
          <li>An Audiobookshelf API key that can read your libraries.</li>
          <li>Optional metadata provider keys. You can add or change these later in Settings.</li>
        </ul>
      </section>
      <ol id="setup-ledger"></ol>
    </aside>
    <main id="setup-main" tabindex="-1" aria-busy="true"></main>
  </div>
  <div id="setup-status" class="sr-only" role="status" aria-live="polite"></div>
  <script type="module" src="/assets/setup.js"></script>
</body>
</html>
"""

SETUP_CSS = """:root{color-scheme:light dark;--paper:#f4f2ec;--sheet:#fbfaf6;--ink:#17201e;--quiet:#56615e;--teal:#0f4c42;--wash:#dcebe6;--line:#d9d7cf;--strong:#b9beb9;--ochre:#8a5a16;--red:#8c3434;--focus:#146fba;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:var(--paper);color:var(--ink)}*{box-sizing:border-box}body{margin:0;min-width:320px;background:var(--paper);color:var(--ink)}button,input,select{font:inherit}button,input,select{min-height:44px;border:1px solid var(--strong);border-radius:.5rem;background:var(--sheet);color:var(--ink);padding:.65rem .8rem}button{font-weight:750;cursor:pointer}.primary{background:var(--teal);color:#fbfaf6;border-color:var(--teal)}button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.masthead{min-height:62px;display:flex;align-items:center;gap:.75rem;padding:.7rem clamp(1rem,3vw,2.6rem);border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--sheet) 92%,transparent)}.masthead span:last-child{margin-left:auto;color:var(--quiet);font-size:.78rem}.book-mark{display:grid;place-items:center;width:34px;height:40px;border-radius:2px 5px 5px 2px;background:var(--teal);color:#fff;font:700 .75rem Georgia,serif}.setup-shell{max-width:76rem;margin:0 auto;padding:clamp(1rem,4vw,3rem);display:grid;grid-template-columns:minmax(0,min(21rem,30%)) minmax(0,1fr);gap:clamp(1.5rem,5vw,5rem)}aside{align-self:start;position:sticky;top:1.5rem}h1,h2,h3{font-family:Georgia,Times New Roman,serif;line-height:1.17}h1{font-size:clamp(1.8rem,4vw,2.65rem);margin:.25rem 0 1rem}h2{font-size:clamp(1.55rem,3vw,2.1rem);margin:.25rem 0 .75rem}p{line-height:1.55}.eyebrow{text-transform:uppercase;letter-spacing:.08em;font-size:.75rem;font-weight:800;color:var(--teal)}ol{list-style:none;padding:0;margin:1.5rem 0}.ledger-row{display:grid;grid-template-columns:1.5rem 1fr auto;align-items:center;gap:.6rem;padding:.7rem 0;border-top:1px solid var(--line);font-size:.82rem}.ledger-row[aria-current=step]{color:var(--teal);font-weight:800}.ledger-row button{min-height:36px;padding:.35rem .55rem;font-size:.75rem}.step-card{border:1px solid var(--line);border-radius:1rem;background:var(--sheet);padding:clamp(1rem,4vw,2.2rem);min-height:26rem}.intro{max-width:66ch;color:var(--quiet)}form{display:grid;gap:1rem;margin-top:1.5rem}.field{display:grid;gap:.4rem}.field[hidden]{display:none}.field span{font-size:.75rem;font-weight:800}.actions{display:flex;flex-wrap:wrap;gap:.7rem;margin-top:.6rem}.notice{border-left:3px solid var(--ochre);padding:.8rem 1rem;background:color-mix(in srgb,var(--ochre) 10%,var(--sheet));border-radius:0 .65rem .65rem 0}.error{color:var(--red);font-weight:700}.option{display:grid;gap:.7rem;padding:1rem;border:1px solid var(--line);border-radius:.75rem}.option>label:first-child{display:flex;align-items:center;gap:.7rem;font-family:Georgia,serif;font-weight:700}.option input[type=checkbox]{min-height:auto;width:1.1rem;height:1.1rem}.muted{color:var(--quiet);font-size:.8rem}.mount-state{font-weight:800}.skip-link{position:fixed;left:1rem;top:1rem;z-index:4;transform:translateY(-200%);padding:.7rem;background:var(--sheet);color:var(--teal)}.skip-link:focus{transform:none}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@media(max-width:47.5rem){.setup-shell{grid-template-columns:1fr;padding:1rem .75rem 4rem}.masthead{padding:.65rem .75rem}aside{position:static}.step-card{min-height:0}.actions>*{flex:1 1 10rem}}:root[data-theme=light]{color-scheme:light}:root[data-theme=dark]{color-scheme:dark;--paper:#0f1512;--sheet:#151d19;--ink:#edf2ee;--quiet:#a9b6b0;--teal:#8ed2c1;--wash:#203b34;--line:#2d3933;--strong:#4a5a52;--ochre:#efbd70;--red:#f39b9b;--focus:#78bdf2}:root[data-theme=dark] .primary{background:#61b29f;color:#0f1512}@media(prefers-color-scheme:dark){:root:not([data-theme]){--paper:#0f1512;--sheet:#151d19;--ink:#edf2ee;--quiet:#a9b6b0;--teal:#8ed2c1;--wash:#203b34;--line:#2d3933;--strong:#4a5a52;--ochre:#efbd70;--red:#f39b9b;--focus:#78bdf2}:root:not([data-theme]) .primary{background:#61b29f;color:#0f1512}}@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}.provider-links{display:flex;flex-wrap:wrap;justify-content:center;gap:.5rem 1rem;padding:1rem;color:var(--quiet);font-size:.75rem}.provider-links a{min-height:44px;display:inline-flex;align-items:center;color:var(--teal);font-weight:700}"""
SETUP_CSS += (
    "main,.step-card,form,.option,.field,.option>label:first-child,.setup-shell>*,"
    ".ledger-row>*,.masthead>*,.actions>*{min-width:0}"
    "form{grid-template-columns:minmax(0,1fr)}"
    "form>*{min-width:0;max-width:100%}"
    ".option,.option>label:first-child,.ledger-row span,.masthead strong,.masthead span{"
    "overflow-wrap:anywhere}"
    "h1,h2,h3,.notice,.step-card p:not(.sr-only){overflow-wrap:anywhere}"
    ".option>label:first-child{min-height:44px}"
    ".field input:not([type=checkbox]),.field select{width:100%;max-width:100%;min-width:0}"
    ".ledger-row{grid-template-columns:minmax(0,1.5rem) minmax(0,1fr) auto}"
    ".ledger-row button{max-width:100%;overflow-wrap:anywhere}"
    "@media(max-width:47.5rem){.masthead{flex-wrap:wrap}.masthead span:last-child{"
    "margin-left:0;width:100%}}"
    ".workspace-handoff{display:inline-flex;align-items:center;justify-content:center;"
    "min-height:44px;width:max-content;max-width:100%;margin-top:1rem;padding:.65rem .9rem;"
    "border-radius:.5rem;text-decoration:none;transition:transform 160ms ease,filter 160ms ease}"
    ".workspace-handoff:hover{transform:translateY(-2px);filter:brightness(1.08)}"
    ".provider-access{width:fit-content;max-width:100%;min-width:0;min-height:44px;display:inline-flex;"
    "align-items:center;color:var(--teal);font-weight:750;text-decoration-line:underline;"
    "text-decoration-thickness:.1em;text-underline-offset:.2em;overflow-wrap:anywhere}"
    ".provider-access:hover{text-decoration-thickness:.16em}"
    ".masthead{view-transition-name:mechanical-librarian-brand}"
    "#setup-ledger{view-transition-name:works-index}"
    ".step-card{view-transition-name:preparation-detail}"
    ".setup-preparation{display:grid;gap:1rem;margin-top:1rem}"
    ".setup-preparation section{display:grid;gap:.55rem;padding-top:.8rem;border-top:1px solid var(--line)}"
    ".setup-preparation h3{margin:0}.setup-active{display:flex;gap:.55rem;overflow-x:auto;margin:0;padding:0;list-style:none}"
    ".setup-active li{flex:0 0 min(15rem,80%);display:grid;gap:.15rem;padding:.65rem;border:1px solid var(--line);border-radius:.6rem}"
    ".setup-active small{color:var(--quiet)}.setup-passage{display:grid;gap:.35rem;margin:0}"
    ".setup-passage div{display:flex;justify-content:space-between;gap:1rem}.setup-passage dd{margin:0;font-weight:800;white-space:nowrap}"
    ".setup-completions{display:grid;gap:.3rem;margin:0;padding-left:1.2rem}"
    ".setup-active li>*,.setup-completions li{min-width:0;overflow-wrap:anywhere}"
    "a{color:var(--teal);text-decoration-thickness:.08em;text-underline-offset:.18em}"
    ".confirmation{display:grid;gap:.25rem;margin:.25rem 0 .5rem;padding:.65rem .9rem;"
    "border-left:3px solid var(--teal);background:var(--wash);border-radius:0 .65rem .65rem 0}"
    ".confirmation p{margin:0;overflow-wrap:anywhere}"
    ".provider-group{display:grid;gap:.6rem;min-width:0;margin:0;padding:0;border:0}"
    ".provider-group legend{padding:0;margin-bottom:.4rem;font:700 1.05rem Georgia,Times New Roman,serif}"
    ".suggestion{margin:0;color:var(--teal);font-size:.8rem;font-weight:750}"
    ".provider-cards{display:grid;gap:.6rem;grid-template-columns:repeat(auto-fill,minmax(min(100%,17rem),1fr))}"
    ".provider-cards .option{gap:.35rem;padding:.6rem .8rem;align-content:start}"
    ".provider-cards .muted{margin:0}"
    ".reviewer-detail summary{min-height:44px;display:flex;align-items:center;cursor:pointer;"
    "color:var(--teal);font-weight:750}"
    ".reviewer-detail p{color:var(--quiet);font-size:.8rem}"
    ".reveal-row,.summary-row>*,.prerequisites>*,.rules>*{min-width:0}"
    ".field-label{font-size:.75rem;font-weight:800}"
    ".reveal-row{display:flex;align-items:stretch;gap:.5rem}"
    ".reveal-row input{flex:1 1 auto}"
    ".reveal{flex:0 0 auto;padding:.65rem .8rem;font-size:.78rem;background:var(--sheet);color:var(--teal);border-color:var(--strong);overflow-wrap:anywhere}"
    ".reveal[aria-pressed=true]{background:var(--wash)}"
    "input[aria-invalid=true],select[aria-invalid=true]{border-color:var(--red);border-width:2px}"
    ".rules{display:grid;gap:.3rem;margin:.2rem 0 0;padding-left:1.15rem;max-width:66ch;color:var(--quiet);font-size:.82rem;list-style:disc}"
    ".rules li,.prerequisites li{overflow-wrap:anywhere}"
    ".prerequisites{margin:1rem 0 0;padding-top:1rem;border-top:1px solid var(--line)}"
    ".prerequisites h2{font-size:1.05rem;margin:0 0 .5rem}"
    ".prerequisites ul{display:grid;gap:.45rem;margin:0;padding-left:1.15rem;color:var(--quiet);font-size:.8rem;list-style:disc}"
    ".summary{display:grid;margin:1.25rem 0 0;border:1px solid var(--line);border-radius:.75rem;overflow:hidden}"
    ".summary-row{display:flex;flex-wrap:wrap;gap:.35rem 1rem;align-items:baseline;padding:.8rem 1rem;border-top:1px solid var(--line)}"
    ".summary-row:first-child{border-top:0}"
    ".summary dt{flex:0 0 9rem;font-size:.75rem;font-weight:800;color:var(--quiet)}"
    ".summary dd{flex:1 1 12ch;display:grid;gap:.2rem;margin:0;overflow-wrap:anywhere}"
    ".summary-row dd:last-child{flex:0 0 auto;justify-self:start}"
    ".summary-change{min-height:36px;padding:.35rem .6rem;font-size:.75rem}"
    "#setup-main h2:focus-visible{outline:3px solid var(--focus);outline-offset:3px}"
    ".prerequisites.complete{display:none}"
    ".mount-results{display:grid;gap:.7rem}"
    ".mount-line{display:grid;gap:.15rem;margin:0;padding-left:.6rem;border-left:3px solid var(--teal)}"
    ".mount-line-attention{border-left-color:var(--red)}"
    ".mount-line-attention .mount-state{color:var(--red)}"
    "@media(max-width:47.5rem){.summary dt,.summary-row dd{flex-basis:100%}}"
    "@media(prefers-reduced-motion:reduce){.masthead,#setup-ledger,.step-card{view-transition-name:none}::view-transition-group(*),::view-transition-image-pair(*),::view-transition-old(*),::view-transition-new(*){animation-duration:0s!important;animation-delay:0s!important}}"
)

SETUP_HANDOFF_CSS = "@view-transition{navigation:auto}"

_VOCABULARY = json.loads(
    files(__package__).joinpath("settings_vocabulary.json").read_text(encoding="utf-8")
)

# One source, served as written: no fragment is patched in afterwards.
_SETUP_JS_SOURCE = r"""const main=document.querySelector('#setup-main');
const ledger=document.querySelector('#setup-ledger');
const status=document.querySelector('#setup-status');
const steps=[['claim','Create owner access'],['connect','Connect Audiobookshelf'],['providers','Choose metadata providers'],['libraries','Choose libraries'],['mounts','Check read-only mounts'],['reviewer','Choose reviewer'],['kick_off','Kick off preparation']];
const mountState={matched:'Matched',missing:'No mapping',ambiguous:'More than one mapping',unavailable:'No folder reported'};
const mountRemedy={missing:'Correct the Audiobookshelf service path and compare again, or have the deployment declare a read-only mapping for it; Mechanical Librarian never creates or changes mounts.',ambiguous:'Correct the service path so exactly one deployment-owned read-only mapping matches.',unavailable:'Audiobookshelf reported no folder path to compare. Enter the service path yourself and compare again; Mechanical Librarian never creates or changes mounts.'};
const mountSummary={matched:'read-only mount matched',missing:'no read-only mount declares this folder yet',ambiguous:'more than one read-only mount matches',unavailable:'no service path was reported to compare'};
const insecureTransport=()=>location.protocol!=='https:';
let csrf=null;
let state=null;
let codexTerminal=null;
let mountDraft=null;
let fieldSequence=0;
let mutating=false;
let refusalGuard=null;
let confirmation=null;
const api=async(path,options={})=>{
  const headers={'content-type':'application/json',...(options.mutation?{'x-csrf-token':csrf}:{})};
  const response=await fetch(path,{credentials:'same-origin',...options,headers:{...headers,...options.headers}});
  let body={};
  try{body=await response.json()}catch{}
  if(!response.ok){
    const failure=new Error(body?.detail?.error||body?.error||'unavailable');
    failure.status=response.status;
    throw failure
  }
  return body
};
const announce=value=>{
  status.textContent='';
  requestAnimationFrame(()=>{status.textContent=value})
};
const originOf=address=>{
  try{
    const value=new URL(address).origin;
    return value==='null'?null:value
  }catch{return null}
};
// Only scheme, host and port: the same rule the server applies before probing.
const bareOrigin=address=>{
  try{
    const value=new URL(address);
    return ['http:','https:'].includes(value.protocol)&&!value.username&&!value.password&&value.pathname==='/'&&!/[?#]/.test(address)
  }catch{return false}
};
const refusal=e=>e.status===401?'session_expired':e.status===403&&e.message==='csrf'?'stale':e.message;
const escapeName=value=>String(value).replace(/[^a-z0-9_-]+/gi,'-').replace(/^-+|-+$/g,'').toLowerCase().slice(0,64)||'library';
const humanName=value=>String(value).split(/[-_]+/u).filter(Boolean).map(word=>word.charAt(0).toUpperCase()+word.slice(1)).join(' ');
const field=(label,input)=>{
  const wrap=document.createElement('label');
  wrap.className='field';
  const text=document.createElement('span');
  text.textContent=label;
  wrap.append(text,input);
  return wrap
};
const secretField=(label,control,subject)=>{
  const wrap=document.createElement('div');
  wrap.className='field';
  const id=`setup-field-${++fieldSequence}`;
  control.id=id;
  const text=document.createElement('label');
  text.className='field-label';
  text.setAttribute('for',id);
  text.textContent=label;
  const row=document.createElement('div');
  row.className='reveal-row';
  const toggle=document.createElement('button');
  toggle.type='button';
  toggle.className='reveal';
  toggle.textContent='Show';
  toggle.setAttribute('aria-pressed','false');
  toggle.setAttribute('aria-controls',id);
  toggle.setAttribute('aria-label',`Show ${subject}`);
  toggle.addEventListener('click',()=>{
    const reveal=control.type==='password';
    control.type=reveal?'text':'password';
    toggle.setAttribute('aria-pressed',reveal?'true':'false');
    toggle.textContent=reveal?'Hide':'Show';
    toggle.setAttribute('aria-label',`${reveal?'Hide':'Show'} ${subject}`)
  });
  row.append(control,toggle);
  wrap.append(text,row);
  return wrap
};
const rules=items=>{
  const list=document.createElement('ul');
  list.className='rules';
  for(const item of items){
    const row=document.createElement('li');
    row.textContent=item;
    list.append(row)
  }
  return list
};
const input=(name,type='text')=>{
  const value=document.createElement('input');
  value.name=name;
  value.type=type;
  value.autocomplete='off';
  return value
};
const button=(label,primary=false)=>{
  const value=document.createElement('button');
  value.type='submit';
  value.textContent=label;
  if(primary)value.className='primary';
  return value
};
const describe=(control,refused)=>{
  const ids=`${refused?'setup-error ':''}${control.dataset.hint||''}`.trim();
  if(ids)control.setAttribute('aria-describedby',ids);else control.removeAttribute('aria-describedby')
};
const error=(form,message,control,address)=>{
  if(refusalGuard)refusalGuard.abort();
  refusalGuard=new AbortController();
  let node=form.querySelector('.error');
  if(!node){
    node=document.createElement('p');
    node.className='error';
    node.id='setup-error';
    node.setAttribute('role','alert')
  }
  const anchor=control?.closest('.field');
  if(anchor)anchor.after(node);else form.prepend(node);
  node.textContent=recovery(message,address);
  for(const other of form.querySelectorAll('[aria-invalid]'))if(other!==control){
    other.removeAttribute('aria-invalid');
    describe(other,false)
  }
  if(control){
    control.setAttribute('aria-invalid','true');
    describe(control,true);
    control.addEventListener('input',()=>{
      control.removeAttribute('aria-invalid');
      describe(control,false);
      node.textContent=''
    },{once:true,signal:refusalGuard.signal});
    control.focus();
    const settle=requestAnimationFrame(()=>{if(control.isConnected)control.focus({preventScroll:true})});
    form.addEventListener('focusin',event=>{if(event.target!==control&&event.target.matches('input,select,textarea'))cancelAnimationFrame(settle)},{signal:refusalGuard.signal})
  }
  return node
};
const reachCopy={name_resolution:origin=>`This deployment could not find ${origin}: that name does not resolve here. Check the host name for a typing error, or use the name or IP address this deployment reaches Audiobookshelf by.`,refused:origin=>`${origin} refused the connection. Check that Audiobookshelf is running and serves on that port.`,tls:origin=>`The certificate for ${origin} could not be verified against that address. Use an address whose certificate this deployment already trusts, or the service http:// address across a network you control. Mechanical Librarian never skips verification.`,timeout:origin=>`${origin} did not answer in time. Check that this deployment can reach that address, then retry.`,unauthorized:origin=>`Audiobookshelf at ${origin} did not accept that API key. Issue a key that can read your libraries, then paste it again.`,unavailable:origin=>`Audiobookshelf at ${origin} could not be read, so nothing was recorded. Check that it is running and reachable from this deployment, then retry.`};
const recovery=(code,address)=>{
  const origin=address?originOf(address):null;
  return origin&&reachCopy[code]?reachCopy[code](origin):recoveryCopy(code)
};
const recoveryCopy=code=>({reviewer_unconfigured:'The selected reviewer is no longer ready. Check its access and select it again, or continue without a reviewer.',not_provisioned:'The contained reviewer profile is unavailable. Reload to see its deployment remedy, or choose OpenRouter or no reviewer.',invalid_key:'OpenRouter rejected this key. Replace it and validate again.',quota_exhausted:'OpenRouter credits are exhausted. Add credits, then retry.',rate_limited:'OpenRouter is rate limited. Wait, then retry.',model_unavailable:'That model is unavailable. Choose another vision model.',model_text_only:'That model cannot inspect covers. Choose an image-capable model.',malformed_response:'OpenRouter returned an invalid response. Retry later or choose another model.',timed_out:'OpenRouter did not answer in time. Check network access, then retry.',response_too_large:'OpenRouter returned an answer too large to read. Retry later, or choose another model.',unreachable:'OpenRouter could not be reached. Check network access, then retry.',credential_clear_failed:'The credential could not be removed. Retry from Settings.',host_unsupported:'This host cannot safely run the contained Codex reviewer.',credential_unavailable:'Codex sign-in is unavailable. Test or replace it, then select Codex.',access_changed:'Reviewer access changed in another session. Reload before trying again.',reviewer_access_stale:'Reviewer access changed in another session. Reload before trying again.',reviewer_access_unavailable:'Reviewer access is temporarily unavailable. Retry this step.',unauthorized:'Audiobookshelf did not accept that API key. Issue a key that can read your libraries, then paste it again.',invalid_endpoint:'That address could not be used. Enter only the scheme, host and port Audiobookshelf serves on, such as https://audiobookshelf.example:13378, with no path, user name or password.',tls:'The certificate for that https:// address could not be verified against the address you entered. Use an address whose certificate this deployment already trusts, or use the service http:// address across a network you control. Mechanical Librarian never skips verification.',timeout:'Audiobookshelf did not answer in time. Check that the address is reachable from this deployment, then retry.',redirect:'That address redirected elsewhere. Enter the direct Audiobookshelf address; setup never follows a redirect with your key.',oversize:'Audiobookshelf returned more data than setup can safely read. Nothing was recorded.',malformed:'Audiobookshelf answered, but not with the supported libraries response. Check that the address points at Audiobookshelf itself.',unavailable:'Setup could not finish that step, so nothing was recorded. Wait a moment and try again.',invalid_origin:settingsVocabulary.errors.invalid_origin,session_expired:'Your setup sign-in expired, so nothing was recorded. Reload this page and sign in with the owner password; every recorded step is kept.',activation_failed:'Preparation could not be started, so nothing changed. Try kicking off again; if it keeps failing, the deployment log holds the reason.',invalid_mounts:'One of those service paths could not be used. Enter the absolute folder path the Audiobookshelf service itself reports, or leave it empty.',invalid_providers:'One of those provider entries could not be recorded. Check the contact email and API key on each provider you enabled.',invalid_credentials:'That owner password was not accepted. Check it and try again.',throttled:'Too many sign-in attempts from this device. Wait five minutes, then try again.',invalid_password:settingsVocabulary.errors.owner_password,invalid_password_long:settingsVocabulary.errors.owner_password_long,invalid_password_empty:settingsVocabulary.errors.owner_password_empty,invalid_password_mismatch:settingsVocabulary.errors.owner_password_mismatch,claimed:'This workspace was claimed in another tab. Sign in with its owner password.',claim_token:'That setup claim token was not accepted. Copy the newest token from the container log, or from private/setup-claim-token on the data volume; a restart replaces it.',csrf:'This setup form expired. Reload it and try again.',invalid_secret:settingsVocabulary.errors.secret,invalid_provider_email:settingsVocabulary.errors.open_library_email,missing_provider_key:settingsVocabulary.errors.provider_key,stale:'Setup changed in another tab. The latest recorded step has been restored.',invalid_libraries:settingsVocabulary.errors.libraries,invalid_library_name:settingsVocabulary.errors.library_name,invalid_library_display_name:'Use a library name from 1 to 256 characters without leading or trailing spaces.',invalid_library_display_name_duplicate:'That name is already used by another library. Choose a different name for this one.',mounts_unready:'Every selected book library needs one matching read-only mount before kickoff.'}[code]||'That could not be recorded. Check the fields and try again.');
// `control` names the field a refusal is about; `address` is the Audiobookshelf
// address a reach failure names; `confirm` says what the recorded step found.
const mutate=async(path,payload,form,{control,address,confirm}={})=>{
  if(mutating)return;
  mutating=true;
  const actions=[...form.querySelectorAll('button:not(.reveal)')];
  for(const action of actions)action.disabled=true;
  main.setAttribute('aria-busy','true');
  announce('Working on that step\u2026');
  try{
    await api(path,{method:'POST',body:JSON.stringify(payload),mutation:true});
    confirmation=confirm||null;
    await load();
    const recorded=[...main.querySelectorAll('.confirmation p')].map(line=>line.textContent.trim()).filter(Boolean);
    announce(recorded.length?recorded.join(' '):'Setup progress recorded')
  }catch(e){
    confirmation=null;
    // A form token rotated by another tab is the same event as a stale step.
    const code=refusal(e);
    if(code==='stale'){
      await load();
      announce(recovery('stale'));
      const fresh=main.querySelector('form');
      if(fresh){
        error(fresh,'stale');
        return
      }
    }
    const reached=e.status===502;
    error(form,code,typeof control==='function'?control(code,reached):control,reached?address:undefined)
  }finally{
    mutating=false;
    main.setAttribute('aria-busy','false');
    for(const action of actions)if(action.isConnected)action.disabled=false
  }
};
const reopen=async step=>{
  const label=steps.find(([id])=>id===step)?.[1]||step;
  try{
    await api('/api/setup/change',{method:'POST',body:JSON.stringify({expected_revision:state.revision,step}),mutation:true});
    mountDraft=null;
    confirmation=null;
    await load();
    announce(`${label} reopened`)
  }catch(e){
    const code=refusal(e);
    // Another tab rotated this form's token; reading the state again issues a fresh one.
    if(e.status===403&&e.message==='csrf')await load();
    announce(recovery(code))
  }
};
const drawLedger=()=>{
  ledger.replaceChildren();
  for(const [id,label] of steps){
    const row=document.createElement('li');
    row.className='ledger-row';
    const complete=state?.completed_steps?.includes(id);
    row.setAttribute('aria-current',state?.next_step===id?'step':'false');
    const mark=document.createElement('span');
    mark.textContent=complete?'✓':state?.next_step===id?'→':'·';
    const text=document.createElement('span');
    text.textContent=label;
    row.append(mark,text);
    if(complete&&id!=='claim'){
      const change=document.createElement('button');
      change.type='button';
      change.textContent='Change';
      change.setAttribute('aria-label',`Change ${label}`);
      change.addEventListener('click',()=>reopen(id));
      row.append(change)
    }
    ledger.append(row)
  }
};
const card=(title,intro)=>{
  main.replaceChildren();
  main.setAttribute('aria-busy','false');
  const section=document.createElement('section');
  section.className='step-card';
  const eyebrow=document.createElement('p');
  eyebrow.className='eyebrow';
  eyebrow.textContent=`Step ${steps.findIndex(([id])=>id===state.next_step)+1} of 7`;
  const heading=document.createElement('h2');
  heading.tabIndex=-1;
  heading.textContent=title;
  const copy=document.createElement('p');
  copy.className='intro';
  copy.textContent=intro;
  section.append(heading,eyebrow);
  // What the step just recorded, said once on the card that follows it.
  const lines=confirmation?confirmation():[];
  confirmation=null;
  if(lines.length){
    const recorded=document.createElement('div');
    recorded.className='confirmation';
    for(const line of lines){
      const item=document.createElement('p');
      item.textContent=line;
      recorded.append(item)
    }
    section.append(recorded)
  }
  section.append(copy);
  main.append(section);
  requestAnimationFrame(()=>{
    heading.focus({preventScroll:true});
    section.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})
  });
  return section
};
const renderClaim=async()=>{
  if(!state)state={next_step:'claim',completed_steps:[]};
  drawLedger();
  let claim;
  try{claim=await api('/api/setup/claim')}catch(e){
    const section=card('Setup is unavailable','Mechanical Librarian could not open owner access safely.');
    const retry=button('Retry',true);
    retry.type='button';
    retry.addEventListener('click',renderClaim);
    section.append(retry);
    return
  }
  state.revision=claim.revision;
  const section=card(claim.claimed?'Sign in to continue':'Create your owner password',claim.claimed?'Owner access already exists. Sign in with the owner password to resume exactly where you stopped.':'Only someone who can read this deployment’s own output can create the owner password. Enter the setup claim token, then choose the password you will use to sign in. Mechanical Librarian keeps a verifier, never the password itself.');
  const transport=document.createElement('p');
  transport.className='notice';
  transport.textContent=insecureTransport()?'This page is served over plain http, so the owner password you type and the session cookie it returns travel in clear text across this network. Use this address only on a network you control or through a VPN.':'This page is served over https that your browser already trusts, so the owner password you type and the session cookie it returns are encrypted between this browser and Mechanical Librarian.';
  section.append(transport);
  const form=document.createElement('form');
  let claimToken=null;
  if(!claim.claimed){
    claimToken=input('claim_token','password');
    claimToken.maxLength=128;
    const source=document.createElement('p');
    source.textContent='Copy the setup claim token from the container log, or from private/setup-claim-token on the data volume. A restart replaces the token, so use the newest one.';
    form.append(source,secretField('Setup claim token',claimToken,'the setup claim token'))
  }
  const password=input('password','password');
  password.autocomplete=claim.claimed?'current-password':'new-password';
  form.append(secretField('Owner password',password,'the owner password'));
  if(!claim.claimed){
    const list=rules([`At least ${settingsVocabulary.owner_password.minimum_characters} characters. Any characters are allowed, including spaces.`,'Both fields must match exactly.','Only a verifier is kept, so this password cannot be read back or reset from inside the application.']);
    list.id='setup-password-rules';
    list.children[1].id='setup-password-match';
    password.dataset.hint=list.id;
    describe(password,false);
    form.append(list)
  }
  let confirmation=null;
  if(!claim.claimed){
    confirmation=input('confirmation','password');
    confirmation.autocomplete='new-password';
    confirmation.dataset.hint='setup-password-match';
    describe(confirmation,false);
    form.append(secretField('Confirm owner password',confirmation,'the confirmed owner password'))
  }
  form.append(button(claim.claimed?'Sign in':'Create owner access',true));
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(confirmation){
      const length=[...password.value].length;
      const long=new TextEncoder().encode(password.value).length>settingsVocabulary.owner_password.maximum_bytes;
      if(length<settingsVocabulary.owner_password.minimum_characters||long){
        error(form,length===0?'invalid_password_empty':long?'invalid_password_long':'invalid_password',password);
        return
      }
      if(password.value!==confirmation.value){
        error(form,'invalid_password_mismatch',confirmation);
        return
      }
    }
    try{
      const payload={password:password.value,...(claim.claimed?{}:{confirmation:confirmation.value,expected_revision:claim.revision,claim_token:claimToken.value.trim()})};
      const result=await api('/api/setup/session',{method:'POST',body:JSON.stringify(payload),headers:claim.claimed?{}:{'x-setup-nonce':claim.nonce}});
      csrf=result.csrf_token;
      await load()
    }catch(e){
      if(e.message==='claimed'){
        state=null;
        await renderClaim();
        return
      }
      if(claimToken&&['claim_token','invalid_password'].includes(e.message)){
        try{
          const fresh=await api('/api/setup/claim');
          if(fresh.claimed){
            state=null;
            await renderClaim();
            return
          }
          claim.nonce=fresh.nonce;
          claim.revision=fresh.revision;
          state.revision=fresh.revision
        }catch{}
        error(form,e.message,e.message==='claim_token'?claimToken:password);
        return
      }
      if(confirmation&&(e.status===401||e.status===429)){
        let already=false;
        try{already=(await api('/api/setup/claim')).claimed}catch{}
        if(already){
          state=null;
          await renderClaim();
          const fresh=main.querySelector('form');
          if(fresh)error(fresh,'claimed',fresh.querySelector('input[type=password]'));
          return
        }
      }
      error(form,e.message,password)
    }
  });
  section.append(form);
  (claimToken||password).focus()
};
const renderConnect=()=>{
  const section=card('Connect Audiobookshelf','Setup makes one bounded read of the supported libraries API. Nothing is written to Audiobookshelf.');
  const scheme=document.createElement('p');
  scheme.className='muted';
  scheme.textContent='Enter the address exactly as Audiobookshelf serves it, including its port. An http:// address is read in clear text, so use one only across a network you control. An https:// address is always verified against its certificate and hostname; Mechanical Librarian never skips that check, never follows a redirect with your key, and offers no way to trust an unverified certificate.';
  section.append(scheme);
  const form=document.createElement('form');
  const endpoint=input('endpoint','url');
  endpoint.placeholder='https://audiobookshelf.example:443';
  const key=input('key','password');
  key.autocomplete='off';
  form.append(field('Audiobookshelf address',endpoint),secretField('Audiobookshelf API key',key,'the Audiobookshelf API key'),button('Verify and continue',true));
  form.noValidate=true;
  const addressCodes=['invalid_endpoint','invalid_origin','name_resolution','refused','tls','timeout','redirect','malformed','oversize'];
  form.addEventListener('submit',event=>{
    event.preventDefault();
    const address=endpoint.value.trim();
    if(address!==endpoint.value)endpoint.value=address;
    if(!address||!endpoint.checkValidity()||!bareOrigin(address)){
      error(form,'invalid_endpoint',endpoint);
      return
    }
    if(!settingsSecretValid(key.value)){
      error(form,'invalid_secret',key);
      return
    }
    mutate('/api/setup/connect',{expected_revision:state.revision,name:'primary',endpoint:address,key:key.value},form,{control:(code,reached)=>code==='unauthorized'?key:reached||addressCodes.includes(code)?endpoint:null,address,confirm:()=>[`Connected to Audiobookshelf at ${originOf(address)||'that address'}.`]})
  });
  section.append(form);
  endpoint.focus()
};
const providerGroups=[['books','Books and ebooks',[['inventaire','inventaire'],['open','open_library'],['hardcover','hardcover'],['google','google_books']]],['audiobooks','Audiobooks',[['audible','audible_catalog'],['audnexus','audnexus'],['audiosilo','audiosilo'],['itunes','itunes_search']]]];
const renderProviders=async()=>{
  const section=card('Choose metadata providers','Each provider you tick adds a source to compare. Tick the ones that fit your libraries; you can add or change them later in Settings.');
  const form=document.createElement('form');
  form.noValidate=true;
  // The form is drawn at once; suggestions follow the library read. They only
  // suggest: nothing is ticked for the owner, and a failed read suggests nothing.
  const reading=document.createElement('p');
  reading.className='muted';
  reading.textContent='Reading your libraries to suggest providers…';
  form.append(reading);
  const fieldsets={};
  const emailInvalid=control=>!control.checkValidity()||!settingsOpenLibraryEmailValid(control.value);
  const rows=[];
  for(const [group,title,specs] of providerGroups){
    const fieldset=document.createElement('fieldset');
    fieldset.className='provider-group';
    fieldsets[group]=fieldset;
    const legend=document.createElement('legend');
    legend.textContent=title;
    fieldset.append(legend);
    const cards=document.createElement('div');
    cards.className='provider-cards';
    for(const [name,adapter] of specs){
      const label=settingsVocabulary.provider_labels[adapter];
      const row=document.createElement('div');
      row.className='option';
      const enabled=input(`${name}-enabled`,'checkbox');
      const head=document.createElement('label');
      head.append(enabled,document.createTextNode(label));
      row.append(head);
      const contribution=document.createElement('p');
      contribution.className='muted';
      contribution.textContent=settingsVocabulary.provider_contributions[adapter];
      row.append(contribution);
      if(settingsVocabulary.cover_switch_provider_adapters.includes(adapter)){
        const covers=document.createElement('p');
        covers.className='muted';
        covers.textContent=`Cover evidence from ${label} stays off until you turn it on in Settings.`;
        row.append(covers)
      }
      if(adapter==='open_library'){
        const contact=document.createElement('p');
        contact.className='muted';
        contact.textContent='Needs only a contact email, sent with each request. No account or key.';
        row.append(contact)
      }
      const accessHref={hardcover:'https://hardcover.app/account/api',google_books:'https://console.cloud.google.com/apis/credentials'}[adapter];
      if(accessHref){
        const access=document.createElement('a');
        access.className='provider-access';
        access.href=accessHref;
        access.rel='noreferrer';
        access.textContent=`Get ${label} access`;
        row.append(access)
      }
      const email=adapter==='open_library'?input(`${name}-email`,'email'):null;
      const key=adapter==='open_library'||settingsProviderKeyless(adapter)?null:input(`${name}-key`,'password');
      const emailField=email?field(`${label} contact email`,email):null;
      const keyField=key?secretField(`${label} API key`,key,`the ${label} API key`):null;
      if(emailField)row.append(emailField);
      if(keyField)row.append(keyField);
      const disclose=()=>{for(const control of [email,key]){if(control){
        control.disabled=!enabled.checked;
        control.closest('.field').hidden=!enabled.checked
      }}};
      enabled.addEventListener('change',disclose);
      disclose();
      cards.append(row);
      rows.push({name,adapter,label,enabled,email,key})
    }
    fieldset.append(cards);
    form.append(fieldset)
  }
  const recorded=chosen=>()=>[chosen.length?`Providers recorded: ${chosen.join(', ')}.`:'No providers recorded. You can add them later in Settings.'];
  const actions=document.createElement('div');
  actions.className='actions';
  actions.append(button('Continue',true));
  const skip=button('Skip providers');
  skip.type='button';
  skip.addEventListener('click',()=>mutate('/api/setup/providers',{expected_revision:state.revision,providers:[]},form,{confirm:recorded([])}));
  actions.append(skip);
  form.append(actions);
  form.addEventListener('submit',event=>{
    event.preventDefault();
    const invalidProvider=rows.find(row=>row.enabled.checked&&((row.email&&emailInvalid(row.email))||(row.key&&((settingsProviderKeyRequired(row.adapter)&&!settingsSecretValid(row.key.value))||(row.key.value&&!settingsSecretValid(row.key.value))))));
    if(invalidProvider){
      const control=invalidProvider.email&&emailInvalid(invalidProvider.email)?invalidProvider.email:invalidProvider.key;
      error(form,control===invalidProvider.email?'invalid_provider_email':!control.value&&settingsProviderKeyRequired(invalidProvider.adapter)?'missing_provider_key':'invalid_secret',control);
      return
    }
    const chosen=rows.filter(row=>row.enabled.checked);
    const providers=chosen.map(row=>({name:row.name,adapter:row.adapter,enabled:true,cover_enabled:row.adapter==='inventaire',contact_email:row.email?.value||null,key:row.key?.value||null}));
    mutate('/api/setup/providers',{expected_revision:state.revision,providers},form,{confirm:recorded(chosen.map(row=>row.label))})
  });
  section.append(form);
  // Not awaited, so the step is ready (and focused) before the read returns.
  void suggest();
  async function suggest(){
    let libraries=[];
    try{libraries=(await api(`/api/setup/libraries?expected_revision=${state.revision}`)).libraries.filter(item=>item.media_kind==='book')}catch{}
    if(!section.isConnected)return;
    reading.remove();
    const suggested={books:libraries.filter(item=>!item.audiobooks_only).map(item=>item.name),audiobooks:libraries.map(item=>item.name)};
    // Audiobook-only libraries lead with Audiobooks, unless the owner is already in the form.
    if(!suggested.books.length&&suggested.audiobooks.length&&!form.contains(document.activeElement))fieldsets.books.before(fieldsets.audiobooks);
    for(const group of ['books','audiobooks']){
      if(!suggested[group].length)continue;
      const note=document.createElement('p');
      note.className='suggestion';
      note.id=`suggested-${group}`;
      note.textContent=`Suggested for ${suggested[group].join(', ')}`;
      fieldsets[group].setAttribute('aria-describedby',note.id);
      fieldsets[group].querySelector('legend').after(note)
    }
  }
};
const renderLibraries=async()=>{
  const section=card('Choose libraries','Select one or more book libraries.');
  const form=document.createElement('form');
  // The wizard states its own refusal; a browser bubble would precede it.
  form.noValidate=true;
  const loading=document.createElement('p');
  loading.textContent='Reading libraries…';
  form.append(loading);
  section.append(form);
  try{
    const result=await api(`/api/setup/libraries?expected_revision=${state.revision}`);
    loading.remove();
    const usedNames=new Set();
    const unsupported=result.libraries.filter(item=>item.media_kind==='unsupported');
    if(unsupported.length){
      const note=document.createElement('p');
      note.className='notice';
      note.textContent=`${unsupported.length} unsupported podcast ${unsupported.length===1?'library':'libraries'} excluded.`;
      form.append(note)
    }
    for(const item of result.libraries.filter(value=>value.media_kind!=='unsupported')){
      const row=document.createElement('div');
      row.className='option';
      const selected=input(`library-${item.library_id}`,'checkbox');
      const head=document.createElement('label');
      head.append(selected,document.createTextNode(item.name));
      row.append(head);
      const displayName=input(`display-${item.library_id}`);
      displayName.value=item.name;
      displayName.required=true;
      const displayNameField=field(`New name for ${item.name}`,displayName);
      displayNameField.hidden=true;
      const changeName=button('Change name');
      changeName.type='button';
      changeName.setAttribute('aria-label',`Change name of ${item.name}`);
      changeName.addEventListener('click',()=>{
        displayNameField.hidden=false;
        changeName.hidden=true;
        displayName.focus()
      });
      row.append(changeName,displayNameField);
      let stableName=escapeName(item.name);
      let suffix=2;
      while(usedNames.has(stableName)){
        const tail=`-${suffix++}`;
        stableName=`${escapeName(item.name).slice(0,64-tail.length)}${tail}`
      }
      usedNames.add(stableName);
      row.dataset.id=item.library_id;
      row.dataset.name=stableName;
      form.append(row)
    }
    form.append(button('Continue',true));
    form.addEventListener('submit',event=>{
      event.preventDefault();
      const selected=[...form.querySelectorAll('.option')].filter(row=>row.querySelector('input[type=checkbox]').checked);
      if(!selected.length){
        error(form,'invalid_libraries');
        return
      }
      const invalid=selected.map(row=>row.querySelector('input[name^=display-]')).find(control=>{
        const value=control.value;
        return Array.from(value).length<1||Array.from(value).length>256||value.includes('\0')||value.trim()!==value
      });
      if(invalid){
        invalid.focus();
        error(form,'invalid_library_display_name');
        return
      }
      // One library carries one name everywhere: two may not share one, in any case.
      const seen=new Map();
      for(const control of selected.map(row=>row.querySelector('input[name^=display-]'))){
        const key=control.value.toLowerCase();
        const earlier=seen.get(key);
        if(earlier){
          // Point at the name the owner is editing; otherwise at the later library.
          const duplicate=control.closest('.field').hidden&&!earlier.closest('.field').hidden?earlier:control;
          const holder=duplicate.closest('.option');
          duplicate.closest('.field').hidden=false;
          holder.querySelector('button').hidden=true;
          error(form,'invalid_library_display_name_duplicate',duplicate);
          return
        }
        seen.set(key,control)
      }
      const libraries=selected.map(row=>({display_name:row.querySelector('input[name^=display-]').value,name:row.dataset.name,library_id:row.dataset.id,media_kind:'book',provider_connections:state.providers.filter(value=>value.enabled).map(value=>value.name)}));
      mutate('/api/setup/libraries',{expected_revision:state.revision,libraries},form,{confirm:()=>[`Libraries recorded: ${libraries.map(value=>value.display_name).join(', ')}.`]})
    })
  }catch(e){
    loading.textContent=recovery(refusal(e),e.status===502?state.connection?.endpoint:undefined);
    loading.className='error';
    const retry=button('Retry');
    retry.type='button';
    retry.addEventListener('click',render);
    form.append(retry)
  }
};
const renderMounts=async()=>{
  const section=card('Check read-only mounts','Check that each selected library can reach its files. Mechanical Librarian only reads these folders and never creates or changes a mapping.');
  const guidance=document.createElement('p');
  guidance.className='muted';
  guidance.textContent='Enter the folder path the Audiobookshelf service itself reports, such as /audiobooks or /srv/media/books. That is the path inside that service, which is usually not the path you would type on your own computer. Setup compares it against the read-only mappings this deployment declares.';
  section.append(guidance);
  const form=document.createElement('form');
  let discovered=[];
  try{discovered=(await api(`/api/setup/libraries?expected_revision=${state.revision}`)).libraries}catch{}
  const shown=library=>{
    const evidence=discovered.find(item=>item.library_id===library?.library_id);
    return library?.display_name||evidence?.name||humanName(library?.name||'library')
  };
  if(state.mounts.length){
    const results=document.createElement('div');
    results.className='mount-results notice';
    results.setAttribute('role','status');
    for(const item of state.mounts){
      const line=document.createElement('p');
      const selectedLibrary=state.libraries.find(library=>library.name===item.library);
      const remedy=mountRemedy[item.state];
      line.className=remedy?'mount-line mount-line-attention':'mount-line';
      const lead=document.createElement('span');
      const label=document.createElement('span');
      label.className='mount-state';
      label.textContent=mountState[item.state]||'Compared';
      lead.append(document.createTextNode(`${selectedLibrary?shown(selectedLibrary):humanName(item.library)}: `),label);
      line.append(lead);
      if(remedy){
        const detail=document.createElement('span');
        detail.className='mount-remedy';
        detail.textContent=remedy;
        line.append(detail)
      }
      results.append(line)
    }
    form.append(results)
  }
  for(const library of state.libraries){
    const path=input(`path-${library.name}`);
    const evidence=discovered.find(item=>item.library_id===library.library_id);
    path.value=mountDraft&&library.name in mountDraft?mountDraft[library.name]:evidence?.service_path||'';
    path.placeholder=library.media_kind==='audiobook'?'Optional for audiobook libraries':'/service/path/to/books';
    form.append(field(`${shown(library)} service path`,path))
  }
  form.append(button(state.mounts.length?'Retest mounts':'Check mounts',true));
  form.addEventListener('submit',event=>{
    event.preventDefault();
    const service_paths={};
    const draft={};
    for(const library of state.libraries){
      const entered=form.elements[`path-${library.name}`].value;
      draft[library.name]=entered;
      const value=entered.trim();
      service_paths[library.name]=value||null
    }
    mountDraft=draft;
    // Said on the next card only once every mount matched; a retest stays here.
    const found=()=>state.next_step==='mounts'?[]:state.libraries.filter(library=>library.mount).map(library=>`${shown(library)}: found ${service_paths[library.name]||discovered.find(item=>item.library_id===library.library_id)?.service_path||'its reported folders'} on read-only mount ${library.mount}.`);
    mutate('/api/setup/mounts',{expected_revision:state.revision,service_paths},form,{confirm:found})
  });
  section.append(form)
};
const counted=new Intl.NumberFormat('en-US');
const renderPreparation=(host,visible)=>{
  host.replaceChildren();
  host.className='setup-preparation';
  const stateLine=document.createElement('p');
  stateLine.setAttribute('role','status');
  const stateCopy={starting:'Waiting for the first book to begin',running:'Preparation continues',blocked:'Paused until the reported issue is resolved',completed_clean:'Finished without a reported failure',completed_with_failures:'Finished with reported failures',dismissed:'Preparation dismissed'};
  stateLine.textContent=visible?stateCopy[visible.state]||'Preparation continues':'Waiting for the first progress report';
  host.append(stateLine);
  if(!visible)return stateLine.textContent;
  const active=document.createElement('section');
  const activeHeading=document.createElement('h3');
  activeHeading.textContent='Now preparing';
  active.append(activeHeading);
  const books=Array.isArray(visible.active_books)?visible.active_books.slice(0,5):[];
  if(books.length){
    const list=document.createElement('ol');
    list.className='setup-active';
    for(const book of books){
      const item=document.createElement('li');
      const name=document.createElement('strong');
      name.textContent=book.name;
      const stage=document.createElement('small');
      stage.textContent=book.stage;
      item.append(name,stage);
      list.append(item)
    }
    active.append(list)
  }else{
    const empty=document.createElement('p');
    empty.textContent='No book is active at the last confirmed refresh.';
    active.append(empty)
  }
  if(visible.active_overflow){
    const overflow=document.createElement('p');
    overflow.textContent=`${visible.active_overflow} more active books follow in this run.`;
    active.append(overflow)
  }
  const passage=document.createElement('section');
  const passageHeading=document.createElement('h3');
  passageHeading.textContent='How many books made it this far?';
  const measures=document.createElement('dl');
  measures.className='setup-passage';
  for(const [field,label] of [['read','Read from library'],['searches_finished','Searches finished'],['compared','Candidates compared'],['checked','Metadata checked'],['ready','Prepared'],['failed','Stopped before finishing']]){
    const row=document.createElement('div');
    const term=document.createElement('dt');
    term.textContent=label;
    const value=document.createElement('dd');
    const raw=visible.passage?.[field];
    const count=typeof raw==='number'?counted.format(raw):'…';
    value.textContent=typeof visible.total==='number'?`${count} of ${counted.format(visible.total)}`:count;
    row.append(term,value);
    measures.append(row)
  }
  passage.append(passageHeading,measures);
  if((visible.state==='starting'||visible.state==='running')&&typeof visible.throughput_per_minute==='number'){
    const throughput=document.createElement('p');
    throughput.textContent=`${visible.throughput_per_minute.toFixed(1)} books/min`;
    passage.append(throughput)
  }
  host.append(active,passage);
  const recent=Array.isArray(visible.recent_completions)?visible.recent_completions.slice(0,8):[];
  if(recent.length){
    const recentSection=document.createElement('section');
    const recentHeading=document.createElement('h3');
    recentHeading.textContent='Recent completions';
    const recentList=document.createElement('ul');
    recentList.className='setup-completions';
    for(const completion of recent){
      const item=document.createElement('li');
      item.textContent=`${completion.name} · ${completion.outcome==='ready'?'Prepared':'Stopped before finishing'}`;
      recentList.append(item)
    }
    recentSection.append(recentHeading,recentList);
    host.append(recentSection)
  }
  return stateLine.textContent
};const renderReviewer=async()=>{
  const section=card('Choose an optional cover reviewer','Cover observations are advice only. Choose one engine or continue without a reviewer. No choice enables automatic application.');
  const form=document.createElement('form');section.append(form);
  const later=document.createElement('p');later.textContent='Optional description advice is configured separately in Settings → Reviewer after setup.';form.append(later);
  // `control` is the field the refusal is about and `submitted` the button pressed:
  // the alert sits just above that button. Without either it opens the form, so it
  // is scrolled into view rather than left above a phone viewport.
  const failed=async(e,control,submitted)=>{
    const code=refusal(e);
    // Another tab rotated this form's token; read the state again before saying so.
    if(e.status===403&&e.message==='csrf'){
      await load();
      const fresh=main.querySelector('form');
      if(fresh)error(fresh,code);
      return
    }
    const node=error(form,code,control);
    if(submitted)submitted.before(node);
    // Bring the button in first, then its alert, so both end up on screen together.
    submitted?.scrollIntoView?.({block:'nearest'});
    node.scrollIntoView?.({block:'nearest'})
  };
  let access;try{access=await api('/api/setup/reviewer-access')}catch(e){failed(e);return}
  const current=document.createElement('p');current.textContent=`Current cover selection: ${access.mapping?.engine==='openrouter'?`OpenRouter — ${access.mapping.model==='openai/gpt-6-luna'?'GPT-6 Luna':access.mapping.model}`:access.mapping?.engine==='codex'?`Contained Codex — ${access.codex.model||'model unavailable'}`:'No reviewer'}`;form.append(current);
  const skip=button('Continue without a reviewer');skip.type='button';skip.addEventListener('click',()=>mutate('/api/setup/reviewer',{expected_revision:state.revision,outcome:'skipped'},form,{confirm:()=>['Cover reviewer recorded: none. You can choose one later in Settings.']}));form.append(skip);
  const codex=document.createElement('div');codex.className='option';
  const heading=document.createElement('h3');heading.textContent='Contained Codex';
  const modelName=document.createElement('p');modelName.textContent=`Model: ${access.codex.model||'Unavailable until provisioning is corrected'}`;
  const blocked=['host_unsupported','not_provisioned'].includes(access.codex.state);
  const codexState=blocked?access.codex:(codexTerminal||access.codex);
  const line=document.createElement('p');line.setAttribute('aria-live','polite');line.textContent=`Status: ${codexState.state.replaceAll('_',' ')}`;
  const allowance=document.createElement('p');allowance.textContent='Uses your signed-in plan allowance. No per-request monetary estimate is available.';codex.append(heading,modelName,line,allowance);
  const reasons={profile_not_provisioned:'The contained profile is missing. A deployment owner must restore the supported image profile or explicit override. OpenRouter and no reviewer remain available.',profile_unreadable:'The contained profile cannot be read safely. A deployment owner must correct its ownership, permissions or contents, then restart.',profile_contract_mismatch:'The contained profile does not match the required model and resource limits. A deployment owner must replace it with a supported profile, then restart.',host_evidence_unavailable:'Host memory evidence is unavailable. A deployment owner must correct host or container memory reporting.',cgroup_limit_below_1_gib:'The container memory limit is below 1 GiB. A deployment owner must increase it.',headroom_below_1_gib:'Less than 1 GiB of reviewer headroom is available. Free memory, then retry.'};
  if(access.codex.reason&&reasons[access.codex.reason]){const remedy=document.createElement('p');remedy.textContent=reasons[access.codex.reason];codex.append(remedy)}
  const active=value=>['starting','issued','waiting','validating'].includes(value);
  const follow=async()=>{
    const cancel=button('Cancel sign-in');cancel.type='button';
    cancel.addEventListener('click',async()=>{if(cancel.disabled)return;cancel.disabled=true;try{codexTerminal=await api('/api/setup/reviewer/codex/device/cancel',{method:'POST',body:'{}',mutation:true});await render()}catch(e){cancel.disabled=false;failed(e)}});codex.append(cancel);
    try{let device;do{device=await api('/api/setup/reviewer/codex/device');if(!section.isConnected)return;line.textContent=`Status: ${device.state.replaceAll('_',' ')}`;
      let prompt=codex.querySelector('[data-device-prompt]');if(device.verification_url&&device.user_code){if(!prompt){prompt=document.createElement('p');prompt.dataset.devicePrompt='true';codex.append(prompt)}prompt.replaceChildren();const link=document.createElement('a');link.href=device.verification_url;link.rel='noreferrer';link.textContent='Open the device sign-in page';prompt.append(link,document.createTextNode(` and enter ${device.user_code}.`))}
      if(active(device.state))await new Promise(resolve=>setTimeout(resolve,1000));
    }while(section.isConnected&&active(device.state));if(!section.isConnected)return;codexTerminal=device;await render()}catch(e){if(section.isConnected)failed(e)}
  };
  if(active(access.codex.state)){void follow()}
  else if(codexState.state==='usable'||codexState.state==='configured'){
    const use=button('Use Codex');use.type='button';use.addEventListener('click',async()=>{if(use.disabled)return;use.disabled=true;try{await api('/api/setup/reviewer/selection',{method:'POST',body:JSON.stringify({expected_revision:access.revision,engine:'codex'}),mutation:true});await render();announce('Codex selected. Review the selection, then continue.')}catch(e){use.disabled=false;failed(e)}});codex.append(use);
  }else{
    const check=button('Check existing sign-in');check.type='button';check.disabled=blocked;
    check.addEventListener('click',async()=>{if(check.disabled)return;check.disabled=true;try{codexTerminal=await api('/api/setup/reviewer/codex/recheck',{method:'POST',body:'{}',mutation:true});await render()}catch(e){check.disabled=blocked;failed(e)}});
    const start=button(['expired','failed','cancelled','interrupted'].includes(codexState.state)?'Restart device sign-in':'Start device sign-in');start.type='button';start.disabled=blocked;
    start.addEventListener('click',async()=>{if(start.disabled)return;start.disabled=true;check.disabled=true;try{await api('/api/setup/reviewer/codex/device',{method:'POST',body:JSON.stringify({expected_revision:access.revision}),mutation:true});start.remove();check.remove();await follow()}catch(e){start.disabled=blocked;check.disabled=blocked;failed(e)}});codex.append(check,start);
  }
  if(blocked){
    const cancel=button('Cancel any active sign-in');cancel.type='button';
    const feedback=document.createElement('p');feedback.setAttribute('role','status');
    cancel.addEventListener('click',async()=>{if(cancel.disabled)return;cancel.disabled=true;try{const result=await api('/api/setup/reviewer/codex/device/cancel',{method:'POST',body:'{}',mutation:true});feedback.textContent=result.state==='cancelled'?'Any active sign-in was cancelled.':['ready','usable'].includes(result.state)?'No active sign-in remains.':'Cancellation could not be confirmed. Retry or ask the deployment owner to inspect the reviewer.'}catch(e){failed(e)}finally{cancel.disabled=false}});
    codex.append(cancel,feedback);
  }
  form.append(codex);
  const router=document.createElement('div');router.className='option';
  const routerHeading=document.createElement('h3');routerHeading.textContent='OpenRouter';
  const freeCaveat=document.createElement('p');freeCaveat.textContent=access.free_tier_caveat;
  const caveat=document.createElement('p');caveat.textContent=access.data_use_caveat;
  const shared=document.createElement('p');shared.textContent='One OpenRouter key serves cover and description advice. Replacing or clearing it invalidates both tasks’ captured access. Each task is selected separately.';
  const model=input('reviewer-model');model.placeholder=access.mapping?.engine==='openrouter'?access.mapping.model:'provider/model';model.value=access.mapping?.engine==='openrouter'?access.mapping.model:'';model.maxLength=256;
  const reviewerLabels={'openai/gpt-6-luna':'GPT-6 Luna','xiaomi/mimo-v2.6-flash':'MiMo 2.6 Flash','qwen/qwen3.8-flash':'Qwen 3.8 Flash','mistralai/ministral-3b-2512':'Ministral 3B','openai/gpt-5.6-luna':'GPT-5.6 Luna','mistralai/mistral-small-2603':'Mistral Small 4'};
  const recommendation=document.createElement('div');
  for(const [label,entries] of [['Recommended advisory cover reviewer:',access.recommendations.map(model=>({model,tier:'paid',suggested:true}))],['Other OpenRouter models:',access.candidates]]){
    const title=document.createElement('p');title.textContent=label;const list=document.createElement('ul');
    for(const candidate of entries){const item=document.createElement('li');const choose=button(`Use ${reviewerLabels[candidate.model]?`${reviewerLabels[candidate.model]} (${candidate.model})`:candidate.model}`);choose.type='button';choose.addEventListener('click',()=>{model.value=candidate.model;model.dispatchEvent(new Event('input'));model.focus()});item.append(choose,document.createTextNode(candidate.suggested?' (recommended; paid; observations remain advice)':` (${candidate.tier}; listed by OpenRouter as capable, not yet qualified here)`));list.append(item)}recommendation.append(title,list);
  }
  const key=input('reviewer-key','password');key.autocomplete='new-password';key.maxLength=2048;
  const save=button('Validate and use OpenRouter');save.type='button';
  save.addEventListener('click',async()=>{if(save.disabled)return;save.disabled=true;save.setAttribute('aria-live','polite');save.textContent='Validating OpenRouter…';try{await api('/api/setup/reviewer/openrouter',{method:'POST',body:JSON.stringify({expected_revision:access.revision,api_key:key.value,model:model.value}),mutation:true});key.value='';await render();announce('OpenRouter validated and saved. Review the prices, then continue.')}catch(e){key.value='';save.disabled=false;save.textContent='Validate and use OpenRouter';failed(e,['model_unavailable','model_text_only'].includes(refusal(e))?model:key,save)}});
  const disclosure=document.createElement('div');disclosure.setAttribute('aria-live','polite');
  const showPrices=()=>{disclosure.replaceChildren();const observed=document.createElement('p');const prices=access.cover_metadata?.model===model.value?access.cover_metadata:null;observed.textContent=prices?`Observed USD rates: ${prices.prompt_price??'Unavailable'} per input token; ${prices.completion_price??'Unavailable'} per output token; ${prices.image_price??'Unavailable'} per image; ${prices.request_price??'Unavailable'} per request.`:'Validate and save this model to observe its prices.';const estimate=document.createElement('p');const measured=access.cost_evidence.models.find(row=>row.model===model.value&&row.task==='cover');estimate.textContent=measured?`About $${Number(measured.per_1000_usd).toLocaleString('en-US',{maximumSignificantDigits:4})} per 1,000 cover review requests (estimate).`:'Request cost: not measured for this model.';const packet=document.createElement('p');packet.textContent='One cover request is one work packet, which may contain several images.';disclosure.append(observed,estimate,packet)};
  model.addEventListener('input',showPrices);showPrices();
  // Limits, data use and the shared key are reference detail, read on demand.
  const about=document.createElement('details');about.className='reviewer-detail';const aboutSummary=document.createElement('summary');aboutSummary.textContent='OpenRouter limits, data use and key sharing';about.append(aboutSummary,freeCaveat,caveat,shared);
  router.append(routerHeading,recommendation,field('Model ID',model),field('API key (write-only)',key),save,disclosure,about);form.append(router);
  if(access.mapping){const next=button('Continue with this reviewer',true);next.type='button';next.addEventListener('click',()=>mutate('/api/setup/reviewer',{expected_revision:state.revision,outcome:'configured'},form,{confirm:()=>[`Cover reviewer recorded: ${current.textContent.replace('Current cover selection: ','')}.`]}));form.append(next)}
};
const renderKickoff=async()=>{
  const section=card('Kick off preparation','Check what preparation will use, then start it. Preparation will begin in the workspace.');
  let reviewerChoice='No reviewer';
  try{
    const access=await api('/api/setup/reviewer-access');
    const engine=access.mapping?.engine;
    reviewerChoice=engine?{codex:'Codex',openrouter:'OpenRouter'}[engine]||humanName(engine):'No reviewer'
  }catch{reviewerChoice='The reviewer choice could not be read just now'}
  const providerNames=(state.providers||[]).filter(item=>item.enabled).map(item=>settingsVocabulary.provider_labels[item.adapter]||humanName(item.adapter));
  const libraryLines=(state.libraries||[]).map(library=>{
    const record=(state.mounts||[]).find(item=>item.library===library.name);
    const mount=record?mountSummary[record.state]||'compared':'not compared yet';
    return `${library.display_name||humanName(library.name)} — ${mount}`
  });
  const summary=document.createElement('dl');
  summary.className='summary';
  for(const [label,step,lines,action] of [['Audiobookshelf address','connect',[state.connection?.endpoint||'Not connected'],'Change the Audiobookshelf address'],['Metadata providers','providers',[providerNames.length?providerNames.join(', '):'None'],'Change the metadata providers'],['Libraries','libraries',libraryLines.length?libraryLines:['None selected'],'Change the selected libraries'],['Cover reviewer','reviewer',[reviewerChoice],'Change the cover reviewer']]){
    const row=document.createElement('div');
    row.className='summary-row';
    const term=document.createElement('dt');
    term.textContent=label;
    const value=document.createElement('dd');
    for(const line of lines){
      const item=document.createElement('span');
      item.textContent=line;
      value.append(item)
    }
    const actions=document.createElement('dd');
    const change=document.createElement('button');
    change.type='button';
    change.className='summary-change';
    change.textContent='Change';
    change.setAttribute('aria-label',action);
    change.addEventListener('click',()=>reopen(step));
    actions.append(change);
    row.append(term,value,actions);
    summary.append(row)
  }
  section.append(summary);
  const notice=document.createElement('p');
  notice.className='notice';
  notice.textContent='Nothing in your library changes. Writes stay off until you turn them on in Activity.';
  const form=document.createElement('form');
  const submit=button('Kick off preparation',true);
  form.append(notice,submit);
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(submit.disabled)return;
    submit.disabled=true;
    try{
      const result=await api('/api/setup/kick-off',{method:'POST',body:JSON.stringify({expected_revision:state.revision}),mutation:true});
      section.replaceChildren();
      document.querySelector('.prerequisites')?.classList.add('complete');
      const heading=document.createElement('h2');
      heading.tabIndex=-1;
      heading.textContent='Preparation is starting';
      const copy=document.createElement('p');
      copy.textContent='It runs safely in the normal workspace, which you can open at any point.';
      const safety=document.createElement('p');
      safety.className='notice';
      safety.textContent=result.message;
      const progress=document.createElement('div');
      renderPreparation(progress,null);
      const descriptions=document.createElement('a');
      descriptions.href='/settings?group=reviewer';
      descriptions.textContent='Configure optional description advice in Settings';
      section.append(heading,copy,safety,progress,descriptions);
      heading.focus({preventScroll:true});
      let open=null;
      let observed=true;
      let announcedState=null;
      let stopped=false;
      let timer=null;
      let pollDelay=1500;
      const finishLedger=()=>{for(const row of ledger.querySelectorAll('.ledger-row')){
        row.removeAttribute('aria-current');
        row.querySelector('span').textContent='✓';
        row.querySelector('button')?.remove()
      }};
      const stop=()=>{
        stopped=true;
        if(timer!==null)clearTimeout(timer)
      };
      const retire=dismissed=>{
        stop();
        renderPreparation(progress,null);
        finishLedger();
        heading.textContent=dismissed?'Preparation was dismissed':'Preparation is no longer available';
        copy.textContent=dismissed?'Open the ordinary workspace.':'The preparation record is no longer available. Open the ordinary workspace.';
        progress.textContent='No preparation run is available.';
        if(!open){
          open=document.createElement('a');
          open.className='primary';
          open.textContent='Open workspace';
          section.append(open)
        }
        open.href='/library?lens=all';
        announce(heading.textContent)
      };
      const poll=async()=>{try{
        // A card no longer on the page has nothing to report to.
        if(!section.isConnected){
          stop();
          return
        }
        const response=await fetch('/api/v1/review-preparation',{credentials:'same-origin'});
        if(response.status===401){
          stop();
          finishLedger();
          heading.textContent='Sign in to continue';
          copy.textContent='Your setup sign-in expired while preparation continued safely. Sign in to the workspace to see live progress.';
          progress.textContent='Preparation continues in the normal workspace.';
          const signIn=document.createElement('a');
          signIn.href='/library?lens=all';
          signIn.textContent='Sign in to workspace';
          section.append(signIn);
          heading.focus({preventScroll:true});
          return
        }
        if(!response.ok)throw new Error('unavailable');
        const preparation=await response.json();
        const visible=preparation.presentation;
        const retired=visible?.state==='dismissed';
        if(!visible||retired){
          if(observed||retired)retire(retired);else renderPreparation(progress,null);
          return
        }
        observed=true;
        const confirmed=renderPreparation(progress,visible);
        if(!open){
          open=document.createElement('a');
          open.className='primary workspace-handoff';
          open.textContent='Open workspace';
          open.addEventListener('click',event=>{
            if(event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
            const target=new URL(open.href,location.href);
            const run=target.searchParams.get('preparation')??'';
            if(target.origin!==location.origin||target.pathname!=='/library'||!/^preparation_run_[0-9a-f]{64}$/.test(run))return;
            const motion=document.querySelector('#setup-handoff-motion');
            if(!(motion instanceof HTMLLinkElement))return;
            let fallback=null;
            const disable=()=>{
              motion.disabled=true;
              if(fallback!==null)clearTimeout(fallback)
            };
            motion.disabled=false;
            addEventListener('pageshow',disable,{once:true});
            addEventListener('pageswap',handoff=>{if(handoff.viewTransition)void handoff.viewTransition.finished.then(disable,disable);else disable()},{once:true});
            fallback=setTimeout(disable,2000)
          });
          section.append(open)
        }
        open.href=`/library?lens=all&preparation=${encodeURIComponent(visible.run_id)}`;
        finishLedger();
        const terminal=['completed_clean','completed_with_failures'].includes(visible.state);
        pollDelay=terminal?5000:1500;
        // The state line below says how the run stands; the heading and copy do not repeat it.
        heading.textContent=visible.state==='blocked'?'Preparation needs attention':terminal?'Preparation is complete':'Preparing your library';
        copy.textContent=visible.state==='blocked'?'Open the workspace to review recovery while preparation remains safely paused.':terminal?'Open the workspace to review the completed preparation record.':'Open the workspace to browse works as they are prepared.';
        if(confirmed!==announcedState){
          announcedState=confirmed;
          announce(confirmed)
        }
      }catch{
        const confirmed=progress.querySelector('[role=status]');
        if(confirmed){
          confirmed.textContent='Reconnecting · showing the last confirmed state';
          announce('Reconnecting to preparation progress')
        }
      }finally{if(!stopped)timer=setTimeout(poll,pollDelay)}};
      timer=setTimeout(poll,1500)
    }catch(e){
      submit.disabled=false;
      if(e.status===403&&e.message==='csrf'){
        await load();
        const fresh=main.querySelector('form');
        if(fresh)error(fresh,'stale');
        return
      }
      error(form,refusal(e))
    }
  });
  section.append(form)
};
const render=async()=>{
  drawLedger();
  await ({claim:renderClaim,connect:renderConnect,providers:renderProviders,libraries:renderLibraries,mounts:renderMounts,reviewer:renderReviewer,kick_off:renderKickoff}[state.next_step]||renderClaim)()
};
const load=async()=>{
  csrf=null;
  try{
    state=await api('/api/setup/state');
    csrf=state.csrf_token;
    if(state.next_step!=='mounts')mountDraft=null;
    await render()
  }catch{
    state=null;
    mountDraft=null;
    confirmation=null;
    await renderClaim()
  }
};
load();"""

SETUP_JS = (
    "const settingsVocabulary="
    + json.dumps(_VOCABULARY, separators=(",", ":"), ensure_ascii=True)
    + ";const settingsTextValid=(value,rule)=>Array.from(value).length>=rule.minimum&&Array.from(value).length<=rule.maximum;"
    + "const settingsSecretValid=value=>settingsTextValid(value,settingsVocabulary.secret)&&new RegExp(settingsVocabulary.secret.printable_ascii,'u').test(value)&&!value.toLowerCase().startsWith(settingsVocabulary.secret.forbidden_prefix);"
    + "const settingsOpenLibraryEmailValid=value=>settingsTextValid(value,settingsVocabulary.open_library_email)&&new RegExp(settingsVocabulary.open_library_email.pattern,'u').test(value);"
    + "const settingsLocalLibraryNameValid=value=>settingsTextValid(value,settingsVocabulary.local_library_name)&&new RegExp(settingsVocabulary.local_library_name.pattern,'u').test(value);"
    + "const settingsProviderKeyRequired=adapter=>settingsVocabulary.required_provider_keys.includes(adapter);"
    + "const settingsProviderKeyless=adapter=>settingsVocabulary.keyless_provider_adapters.includes(adapter);"
    + _SETUP_JS_SOURCE
)

SETUP_ORIGIN_REFUSAL_HTML = (
    "<!doctype html>\n"
    '<html lang="en">\n'
    "<head>\n"
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
    # The refusal carries no stylesheet of its own, so this is what keeps it
    # from being stark white for an owner reading in a dark theme.
    '<meta name="color-scheme" content="light dark">\n'
    "<title>Set up Mechanical Librarian</title>\n"
    "</head>\n"
    "<body>\n"
    "<h1>This address cannot be used</h1>\n"
    # The wizard's own assets are refused on this origin too, so the refusal
    # carries no stylesheet, script, or state of its own; the sentence is the
    # one the loaded wizard would have shown.
    f"<p>{html.escape(_VOCABULARY['errors']['invalid_origin'])}</p>\n"
    "</body>\n"
    "</html>\n"
)

__all__ = ["SETUP_CSS", "SETUP_HTML", "SETUP_JS", "SETUP_ORIGIN_REFUSAL_HTML"]
