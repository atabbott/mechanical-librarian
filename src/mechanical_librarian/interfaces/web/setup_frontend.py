"""Self-contained setup assets; no normal workspace code is loaded before handoff."""

from __future__ import annotations

import html
import json
from importlib.resources import files

from mechanical_librarian.application.configuration import (
    AUDIOBOOK_DISPATCH_ADAPTERS,
    EBOOK_DISPATCH_ADAPTERS,
)

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
      <ol id="setup-ledger"></ol>
    </aside>
    <main id="setup-main" tabindex="-1" aria-busy="true"></main>
  </div>
  <div id="setup-status" class="sr-only" role="status" aria-live="polite"></div>
  <script type="module" src="/assets/setup.js"></script>
</body>
</html>
"""

SETUP_CSS = """:root{color-scheme:light dark;--paper:#f4f2ec;--sheet:#fbfaf6;--ink:#17201e;--quiet:#56615e;--teal:#0f4c42;--wash:#dcebe6;--line:#d9d7cf;--strong:#b9beb9;--ochre:#8a5a16;--red:#8c3434;--focus:#146fba;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:var(--paper);color:var(--ink)}*{box-sizing:border-box}body{margin:0;min-width:320px;background:var(--paper);color:var(--ink)}button,input,select{font:inherit}button,input,select{min-height:44px;border:1px solid var(--strong);border-radius:.5rem;background:var(--sheet);color:var(--ink);padding:.65rem .8rem}button{font-weight:750;cursor:pointer}.primary{background:var(--teal);color:#fbfaf6;border-color:var(--teal)}button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.masthead{min-height:62px;display:flex;align-items:center;gap:.75rem;padding:.7rem clamp(1rem,3vw,2.6rem);border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--sheet) 92%,transparent)}.masthead span:last-child{margin-left:auto;color:var(--quiet);font-size:.78rem}.book-mark{display:grid;place-items:center;min-width:34px;height:40px;padding:0 .25rem;white-space:nowrap;border:1px solid var(--teal);border-radius:2px 5px 5px 2px;background:var(--teal);color:#fff;font:700 .75rem Georgia,serif}.setup-shell{max-width:76rem;margin:0 auto;padding:clamp(1rem,4vw,3rem);display:grid;grid-template-columns:minmax(0,min(21rem,30%)) minmax(0,1fr);gap:clamp(1.5rem,5vw,5rem)}aside{align-self:start;position:sticky;top:1.5rem;max-height:calc(100dvh - 3rem);overflow-y:auto;scrollbar-width:thin;margin:-6px;padding:6px}h1,h2,h3{font-family:Georgia,Times New Roman,serif;line-height:1.17}h1{font-size:clamp(1.8rem,4vw,2.65rem);margin:.25rem 0 1rem}h2{font-size:clamp(1.55rem,3vw,2.1rem);margin:.25rem 0 .75rem}p{line-height:1.55}ol{list-style:none;padding:0;margin:1.5rem 0}.ledger-row{display:grid;grid-template-columns:1.5rem 1fr auto;align-items:center;gap:.6rem;padding:.7rem 0;border-top:1px solid var(--line);font-size:.82rem}.ledger-row[aria-current=step]{color:var(--teal);font-weight:800}.ledger-row button{min-height:2.75rem;padding:.35rem .55rem;font-size:.75rem}.step-card{border:1px solid var(--line);border-radius:1rem;background:var(--sheet);padding:clamp(1rem,4vw,2.2rem);min-height:26rem}.intro{max-width:66ch;color:var(--quiet)}form{display:grid;gap:1rem;margin-top:1.5rem}.field{display:grid;gap:.4rem}.field[hidden]{display:none}.field span{font-size:.75rem;font-weight:800}.actions{display:flex;flex-wrap:wrap;gap:.7rem;margin-top:.6rem}.notice{border-left:3px solid var(--ochre);padding:.8rem 1rem;background:color-mix(in srgb,var(--ochre) 10%,var(--sheet));border-radius:0 .65rem .65rem 0}.error{color:var(--red);font-weight:700}.option{display:grid;gap:.7rem;padding:1rem;border:1px solid var(--line);border-radius:.75rem}.option>label:first-child{display:flex;align-items:center;gap:.7rem;font-family:Georgia,serif;font-weight:700}.option input[type=checkbox]{min-height:auto;width:1.1rem;height:1.1rem}.muted{color:var(--quiet);font-size:.8rem}.mount-state{font-weight:800}.device-code-row{display:flex;flex-wrap:wrap;align-items:center;gap:.7rem}.device-code{padding:.35rem .6rem;border:1px solid var(--line);border-radius:.4rem;background:var(--paper);font:700 1.3rem ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.08em;-webkit-user-select:all;user-select:all}.skip-link{position:fixed;left:1rem;top:1rem;z-index:4;transform:translateY(-200%);padding:.7rem;background:var(--sheet);color:var(--teal)}.skip-link:focus{transform:none}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@media(max-width:47.5rem){.setup-shell{grid-template-columns:1fr;padding:1rem .75rem 4rem}.masthead{padding:.65rem .75rem}aside{position:static;max-height:none;overflow:visible}.step-card{min-height:0}.actions>*{flex:1 1 10rem}}:root[data-theme=light]{color-scheme:light}:root[data-theme=dark]{color-scheme:dark;--paper:#0f1512;--sheet:#151d19;--ink:#edf2ee;--quiet:#a9b6b0;--teal:#8ed2c1;--wash:#203b34;--line:#2d3933;--strong:#4a5a52;--ochre:#efbd70;--red:#f39b9b;--focus:#78bdf2}:root[data-theme=dark] .primary{background:#61b29f;color:#0f1512}:root[data-theme=dark] .book-mark{background:#315c52}@media(prefers-color-scheme:dark){:root:not([data-theme]){--paper:#0f1512;--sheet:#151d19;--ink:#edf2ee;--quiet:#a9b6b0;--teal:#8ed2c1;--wash:#203b34;--line:#2d3933;--strong:#4a5a52;--ochre:#efbd70;--red:#f39b9b;--focus:#78bdf2}:root:not([data-theme]) .primary{background:#61b29f;color:#0f1512}:root:not([data-theme]) .book-mark{background:#315c52}}@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}.provider-links{display:flex;flex-wrap:wrap;justify-content:center;gap:.5rem 1rem;padding:1rem;color:var(--quiet);font-size:.75rem}.provider-links a{min-height:44px;display:inline-flex;align-items:center;color:var(--teal);font-weight:700}"""
SETUP_CSS += (
    "main,.step-card,form,.option,.field,.option>label:first-child,.setup-shell>*,"
    ".ledger-row>*,.masthead>*,.actions>*{min-width:0}"
    "form{grid-template-columns:minmax(0,1fr)}"
    "form>*{min-width:0;max-width:100%}"
    ".option,.option>label:first-child,.ledger-row span,.masthead strong,.masthead span{"
    "overflow-wrap:anywhere}"
    "h1,h2,h3,.notice,.step-card p:not(.sr-only){overflow-wrap:anywhere}"
    # A name too wide beside its checkbox moves under it rather than breaking (#1491).
    ".option>label:first-child{min-height:44px;flex-wrap:wrap;overflow-wrap:break-word}"
    ".field input:not([type=checkbox]),.field select{width:100%;max-width:100%;min-width:0}"
    ".ledger-row{grid-template-columns:minmax(0,1.5rem) minmax(0,1fr) auto}"
    ".ledger-row button{max-width:100%;overflow-wrap:anywhere}"
    "@media(max-width:47.5rem){.masthead{flex-wrap:wrap}.masthead span:last-child{"
    "margin-left:0;width:100%}}"
    ".provider-access{width:fit-content;max-width:100%;min-width:0;min-height:44px;display:inline-flex;"
    "align-items:center;color:var(--teal);font-weight:750;text-decoration-line:underline;"
    "text-decoration-thickness:.1em;text-underline-offset:.2em;overflow-wrap:anywhere}"
    ".provider-access:hover{text-decoration-thickness:.16em}"
    ".masthead{view-transition-name:mechanical-librarian-brand}"
    "#setup-ledger{view-transition-name:works-index}"
    ".step-card{view-transition-name:preparation-detail}"
    "a{color:var(--teal);text-decoration-thickness:.08em;text-underline-offset:.18em}"
    ".confirmation{display:grid;gap:.25rem;margin:.25rem 0 .5rem;padding:.65rem .9rem;"
    "border-left:3px solid var(--teal);background:var(--wash);border-radius:0 .65rem .65rem 0}"
    ".confirmation p{margin:0;overflow-wrap:anywhere}"
    ".provider-group{display:grid;gap:.6rem;min-width:0;margin:0;padding:0;border:0}"
    ".provider-group legend{padding:0;margin-bottom:.4rem;font:700 1.05rem Georgia,Times New Roman,serif}"
    ".provider-cards{display:grid;gap:.6rem;grid-template-columns:repeat(auto-fill,minmax(min(100%,17rem),1fr))}"
    ".provider-cards .option{gap:.35rem;padding:.6rem .8rem;align-content:start}"
    ".provider-cards .muted{margin:0}"
    # The summary stays a list item so it keeps its disclosure triangle.
    ".reviewer-detail summary{display:list-item;min-height:44px;padding:.65rem 0;cursor:pointer;"
    "color:var(--teal);font-weight:750}"
    # Reviewer cards space their lines as the source cards do, and their
    # buttons are sized to their words like the rest of setup.
    ".reviewer-option{gap:.6rem;align-content:start}"
    ".reviewer-option h3,.reviewer-option p,.reviewer-detail p{margin:0}"
    ".reviewer-option button{justify-self:start;max-width:100%;overflow-wrap:break-word}"
    ".reviewer-detail{display:grid;gap:.4rem}"
    ".reviewer-models:not([hidden]){display:grid;gap:.7rem}"
    ".reviewer-models ul{display:grid;gap:.7rem;margin:0;padding:0;list-style:none}"
    "button:disabled,button[aria-disabled=true]{opacity:.55;cursor:not-allowed}"
    ".rules .rule-refused{color:var(--red);font-weight:700}"
    ".reviewer-detail p{color:var(--quiet);font-size:.8rem}"
    ".reveal-row,.summary-row>*,.rules>*{min-width:0}"
    ".field-label{font-size:.75rem;font-weight:800}"
    ".reveal-row{display:flex;flex-wrap:wrap;align-items:stretch;gap:.5rem}"
    ".reveal-row input{flex:1 1 10rem;min-width:0}"
    ".reveal{flex:0 0 auto;padding:.65rem .8rem;font-size:.78rem;background:var(--sheet);color:var(--teal);border-color:var(--strong);overflow-wrap:anywhere}"
    ".reveal[aria-pressed=true]{background:var(--wash)}"
    "input[aria-invalid=true],select[aria-invalid=true]{border-color:var(--red);border-width:2px}"
    ".rules{display:grid;gap:.3rem;margin:.2rem 0 0;padding-left:1.15rem;max-width:66ch;color:var(--quiet);font-size:.82rem;list-style:disc}"
    ".rules li{overflow-wrap:anywhere}"
    ".hint{margin:0}"
    ".command{display:block;width:fit-content;max-width:100%;margin-top:.35rem;padding:.25rem .5rem;"
    "border:1px solid var(--line);border-radius:.4rem;background:var(--paper);color:var(--ink);"
    "font:600 .85rem ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere;"
    "-webkit-user-select:all;user-select:all}"
    ".summary{display:grid;margin:1.25rem 0 0;border:1px solid var(--line);border-radius:.75rem;overflow:hidden}"
    ".summary-row{display:flex;flex-wrap:wrap;gap:.35rem 1rem;align-items:baseline;padding:.8rem 1rem;border-top:1px solid var(--line)}"
    ".summary-row:first-child{border-top:0}"
    ".summary dt{flex:0 0 9rem;font-size:.75rem;font-weight:800;color:var(--quiet)}"
    ".summary dd{flex:1 1 12ch;display:grid;gap:.2rem;margin:0;overflow-wrap:anywhere}"
    ".summary-row dd:last-child{flex:0 0 auto;justify-self:start}"
    ".summary-change{min-height:36px;padding:.35rem .6rem;font-size:.75rem}"
    "#setup-main h2:focus-visible{outline:3px solid var(--focus);outline-offset:3px}"
    ".mount-line{display:grid;gap:.15rem;margin:0;padding-left:.6rem;border-left:3px solid var(--teal)}"
    ".mount-line-attention{border-left-color:var(--red)}"
    ".mount-line-attention .mount-state{color:var(--red)}"
    "@media(max-width:47.5rem){.summary dt,.summary-row dd{flex-basis:100%}}"
    "@media(prefers-reduced-motion:reduce){.masthead,#setup-ledger,.step-card{view-transition-name:none}::view-transition-group(*),::view-transition-image-pair(*),::view-transition-old(*),::view-transition-new(*){animation-duration:0s!important;animation-delay:0s!important}}"
    # A phone with enlarged text, measured in the shell's rem: insets give words
    # their line (#1491).
    ".setup-shell{container:setup-shell/inline-size}"
    "@container setup-shell (max-width:12rem){.step-card{padding:.5rem}"
    ".provider-cards .option,.reviewer-option{padding-inline:.5rem}"
    ".reviewer-option button{padding-inline:.5rem}}"
    # A step list too narrow to hold its longest word beside a Change button, as
    # enlarged text makes it beside the step or on a phone, drops the button
    # under the step's name (#1491, #1508).
    "#setup-ledger{container:setup-ledger/inline-size}"
    "@container setup-ledger (max-width:15rem){"
    ".ledger-row{grid-template-columns:minmax(0,1.5rem) minmax(0,1fr)}.ledger-row button{grid-column:2;justify-self:start}}"
)

SETUP_HANDOFF_CSS = "@view-transition{navigation:auto}"

_VOCABULARY = json.loads(
    files(__package__).joinpath("settings_vocabulary.json").read_text(encoding="utf-8")
)

# One source, served as written: no fragment is patched in afterwards.
_SETUP_JS_SOURCE = r"""const main=document.querySelector('#setup-main');
const ledger=document.querySelector('#setup-ledger');
const status=document.querySelector('#setup-status');
const steps=[['claim','Create your password'],['connect','Connect Audiobookshelf'],['libraries','Choose libraries'],['mounts','Find library folders'],['providers','Choose metadata sources'],['reviewer','Cover reviewer (optional)'],['kick_off','Start']];
const mountState={matched:'Found',missing:'Not found',ambiguous:'Not found',unavailable:'Not found'};
const mountRemedy={missing:'Mechanical Librarian can’t see this library’s folder. Add it to the container as a read-only volume, then check again.',ambiguous:'More than one read-only volume matches this library’s folders. Keep one, then check again.',unavailable:'Audiobookshelf reported no folder for this library. Enter its folder, then check again.'};
const mountGuide='https://github.com/atabbott/mechanical-librarian/blob/main/docs/operations/deployment.md#media-mounts';
const insecureTransport=()=>location.protocol!=='https:';
let csrf=null;
let state=null;
let codexTerminal=null;
let codexChecked=false;
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
    // The refusal follows its field, so bring it into view too: on a short
    // screen or with enlarged text it can fall below the fold. When the field
    // and the refusal cannot both fit, the field leads with its label and the
    // refusal begins below.
    if(node.getBoundingClientRect().bottom-control.getBoundingClientRect().top<=innerHeight)node.scrollIntoView({block:'nearest'});
    else(anchor||control).scrollIntoView({block:'start'});
    form.addEventListener('focusin',event=>{if(event.target!==control&&event.target.matches('input,select,textarea'))cancelAnimationFrame(settle)},{signal:refusalGuard.signal})
  }
  return node
};
// A clear-text warning is for an address outside the owner's own network: the
// private IPv4 and IPv6 ranges, localhost, a single-label name and the local
// suffixes. Only a warning: the address is still used. An address that does
// not parse has no host to warn about.
const privateHost=address=>{
  let host;
  try{host=new URL(address).hostname.toLowerCase()}catch{return true}
  const ipv4=/^(\d+)\.(\d+)\.\d+\.\d+$/.exec(host);
  if(ipv4){
    const [first,second]=[Number(ipv4[1]),Number(ipv4[2])];
    return first===10||first===127||(first===172&&second>=16&&second<=31)||(first===192&&second===168)||(first===169&&second===254)||(first===100&&second>=64&&second<=127)
  }
  if(host.startsWith('[')){
    const ipv6=host.slice(1,-1);
    return ipv6==='::1'||/^f[cd][0-9a-f]{0,2}:/.test(ipv6)||/^fe[89ab][0-9a-f]?:/.test(ipv6)
  }
  return host==='localhost'||!host.includes('.')||/\.(local|lan|home\.arpa|internal)$/.test(host)
};
// A field's hint is said with it, and again with any refusal of it.
const hint=(control,text)=>{
  const note=document.createElement('p');
  note.className='muted hint';
  note.id=`setup-hint-${++fieldSequence}`;
  note.textContent=text;
  control.dataset.hint=`${control.dataset.hint||''} ${note.id}`.trim();
  describe(control,false);
  return note
};
const listed=names=>new Intl.ListFormat('en-GB').format(names);
const reachCopy={name_resolution:origin=>`Mechanical Librarian could not find ${origin}. Check the address for a typing error, or use its IP address.`,refused:origin=>`${origin} refused the connection. Check that Audiobookshelf is running and uses that port.`,tls:origin=>`The certificate for ${origin} could not be verified. Use an address with a trusted certificate, or its http:// address on your own network.`,timeout:origin=>`${origin} did not answer in time. Check that Mechanical Librarian can reach it, then try again.`,unauthorized:origin=>`Audiobookshelf at ${origin} did not accept that API key. Create a key that can read your libraries, then paste it.`,unavailable:origin=>`Audiobookshelf at ${origin} could not be read. Check that it is running, then try again.`};
const recovery=(code,address)=>{
  const origin=address?originOf(address):null;
  return origin&&reachCopy[code]?reachCopy[code](origin):recoveryCopy(code)
};
const recoveryCopy=code=>({reviewer_unconfigured:'The chosen reviewer is not ready any more. Choose it again, or skip for now.',reviewer_unchosen:'Choose Codex or OpenRouter, or skip for now.',not_provisioned:'Codex is not set up on this host. Reload the page to see what to fix.',invalid_key:'OpenRouter rejected this key. Replace it and check it again.',quota_exhausted:'OpenRouter credits are used up. Add credits, then try again.',rate_limited:'OpenRouter is rate limited. Wait, then try again.',model_unavailable:'That model is unavailable. Choose another model.',model_text_only:'That model cannot inspect covers. Choose a model with image input.',malformed_response:'OpenRouter’s answer could not be read. Retry later, or choose another model.',timed_out:'OpenRouter did not answer in time. Check network access, then retry.',response_too_large:'OpenRouter returned an answer too large to read. Retry later, or choose another model.',unreachable:'OpenRouter could not be reached. Check network access, then retry.',credential_clear_failed:'The OpenRouter key was not cleared. Try again.',host_unsupported:'Codex is not supported on this host. Reload the page to see what to fix.',credential_unavailable:'You’re not signed in to ChatGPT. Sign in with ChatGPT, then choose Codex again.',access_changed:'Reviewer settings changed elsewhere. Reload, then try again.',reviewer_access_stale:'Reviewer settings changed elsewhere. Reload, then try again.',reviewer_access_unavailable:'Reviewer settings could not be loaded. Try again.',unauthorized:'Audiobookshelf did not accept that API key. Create a key that can read your libraries, then paste it.',invalid_endpoint:'Enter only the address and port, such as http://192.168.1.10:13378, with no path, user name or password.',tls:'The certificate for that address could not be verified. Use an address with a trusted certificate, or its http:// address on your own network.',timeout:'Audiobookshelf did not answer in time. Check that Mechanical Librarian can reach it, then try again.',redirect:'That address redirects somewhere else. Enter Audiobookshelf’s own address.',oversize:'Audiobookshelf sent more data than setup can read. Check that the address points at Audiobookshelf itself.',malformed:'That address did not answer like Audiobookshelf. Check that it points at Audiobookshelf itself.',unavailable:'That step was not saved. Wait a moment, then try again.',invalid_origin:settingsVocabulary.errors.invalid_origin,session_expired:'Your sign-in expired, so that step was not saved. Reload the page and sign in again.',activation_failed:'Mechanical Librarian could not start. Press Start again. If it fails again, the container log says why.',invalid_mounts:'Those folders could not be checked. Reload the page, then check again.',invalid_providers:'A source could not be saved. Check the email and API key on each ticked source.',invalid_credentials:'That owner password was not accepted. Check it and try again.',throttled:'Too many sign-in attempts from this device. Wait five minutes, then try again.',invalid_password:settingsVocabulary.errors.owner_password,invalid_password_long:settingsVocabulary.errors.owner_password_long,invalid_password_empty:settingsVocabulary.errors.owner_password_empty,invalid_password_mismatch:settingsVocabulary.errors.owner_password_mismatch,claimed:'This workspace was claimed in another tab. Sign in with its owner password.',claim_token:'That setup claim token was not accepted. Copy the newest one from the container log.',csrf:'This setup form expired. Reload it and try again.',invalid_secret:settingsVocabulary.errors.secret,invalid_provider_email:settingsVocabulary.errors.open_library_email,missing_provider_key:settingsVocabulary.errors.provider_key,stale:'Setup changed in another tab. The latest recorded step has been restored.',invalid_libraries:settingsVocabulary.errors.libraries,invalid_library_name:settingsVocabulary.errors.library_name,invalid_library_display_name_duplicate:'Two of these libraries have the same name. Rename one in Audiobookshelf, or untick one.',mounts_unready:'A library’s folder was not found. Find it before you start.'}[code]||'That was not saved. Check the fields, then try again.');
// `control` names the field a refusal is about and `submitted` the button
// pressed; `address` is the Audiobookshelf address a reach failure names;
// `confirm` says what the recorded step found. A field refusal sits after its
// field, above the button; a refusal about no field sits under the pressed
// button, or under its row of actions. Only the buttons this press disables are
// enabled again, so a control that was already unavailable stays so. The pressed
// button keeps focus, marked aria-disabled rather than disabled, and `mutating`
// refuses a repeat press; a submit handler that calls mutate checks `mutating`
// first, so Enter in a field runs no client check mid-request (#1678).
const mutate=async(path,payload,form,{control,submitted,address,confirm}={})=>{
  if(mutating)return;
  mutating=true;
  const actions=[...form.querySelectorAll('button:not(.reveal)')].filter(action=>!action.disabled&&action!==submitted);
  for(const action of actions)action.disabled=true;
  submitted?.setAttribute('aria-disabled','true');
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
    const target=typeof control==='function'?control(code,reached):control;
    const node=error(form,code,target,reached?address:undefined);
    if(!target&&submitted){
      (submitted.closest('.actions')||submitted).after(node);
      // Bring the button in first, then its alert, so both end up on screen together.
      submitted.scrollIntoView?.({block:'nearest'});
      node.scrollIntoView?.({block:'nearest'})
    }
  }finally{
    mutating=false;
    main.setAttribute('aria-busy','false');
    submitted?.removeAttribute('aria-disabled');
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
    // The folder step is asked only while a library's folder is not found.
    if(id==='mounts'&&state?.next_step!=='mounts')continue;
    const row=document.createElement('li');
    row.className='ledger-row';
    const complete=state?.completed_steps?.includes(id);
    row.setAttribute('aria-current',state?.next_step===id?'step':'false');
    const current=state?.next_step===id;
    // The mark is decoration; the row says its state in words.
    const mark=document.createElement('span');
    mark.setAttribute('aria-hidden','true');
    mark.textContent=complete?'✓':current?'→':'·';
    const text=document.createElement('span');
    text.textContent=label;
    if(complete||current){
      const said=document.createElement('span');
      said.className='sr-only';
      said.textContent=complete?' Done':' Current step';
      text.append(said)
    }
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
  const heading=document.createElement('h2');
  heading.tabIndex=-1;
  heading.textContent=title;
  section.append(heading);
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
  if(intro){
    const copy=document.createElement('p');
    copy.className='intro';
    copy.textContent=intro;
    section.append(copy)
  }
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
    const section=card('Setup could not be loaded','Check that the container is running, then retry.');
    const retry=button('Retry',true);
    retry.type='button';
    retry.addEventListener('click',renderClaim);
    section.append(retry);
    return
  }
  state.revision=claim.revision;
  const section=card(claim.claimed?'Sign in to continue':'Create your password',claim.claimed?'Sign in with the owner password to continue setup where you stopped.':'Enter the setup claim token, then choose your password.');
  if(insecureTransport()){
    const transport=document.createElement('p');
    transport.className='notice';
    transport.textContent='This page uses plain HTTP. Use it only on a network you trust.';
    section.append(transport)
  }
  const form=document.createElement('form');
  let claimToken=null;
  if(!claim.claimed){
    claimToken=input('claim_token','password');
    claimToken.maxLength=128;
    const tokenField=secretField('Setup claim token',claimToken,'the setup claim token');
    const source=hint(claimToken,'It’s in the container log. Restarting the container replaces it.');
    const command=document.createElement('code');
    command.className='command';
    command.textContent='docker compose logs mechanical-librarian';
    source.append(command);
    // The hint follows the field rather than sitting inside it, so a refusal
    // lands right under the token it refuses (#1291).
    form.append(tokenField,source)
  }
  const password=input('password','password');
  password.autocomplete=claim.claimed?'current-password':'new-password';
  let lengthRule=null;
  // A short password is refused on the rule it breaks, said once.
  const refuseLength=()=>{
    form.querySelector('.error')?.remove();
    for(const other of form.querySelectorAll('[aria-invalid]'))if(other!==password){
      other.removeAttribute('aria-invalid');
      describe(other,false)
    }
    lengthRule.classList.add('rule-refused');
    password.setAttribute('aria-invalid','true');
    password.addEventListener('input',()=>{
      lengthRule.classList.remove('rule-refused');
      password.removeAttribute('aria-invalid')
    },{once:true});
    password.focus();
    lengthRule.scrollIntoView?.({block:'nearest'});
    announce(lengthRule.textContent)
  };
  form.append(secretField('Owner password',password,'the owner password'));
  if(!claim.claimed){
    const list=rules([`At least ${settingsVocabulary.owner_password.minimum_characters} characters.`,'There’s no password reset, so keep it somewhere safe.']);
    list.id='setup-password-rules';
    lengthRule=list.children[0];
    password.dataset.hint=list.id;
    describe(password,false);
    form.append(list)
  }
  let confirmation=null;
  if(!claim.claimed){
    confirmation=input('confirmation','password');
    confirmation.autocomplete='new-password';
    form.append(secretField('Confirm owner password',confirmation,'the confirmed owner password'))
  }
  form.append(button(claim.claimed?'Sign in':'Create your password',true));
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    lengthRule?.classList.remove('rule-refused');
    if(confirmation){
      const length=[...password.value].length;
      const long=new TextEncoder().encode(password.value).length>settingsVocabulary.owner_password.maximum_bytes;
      if(length>0&&length<settingsVocabulary.owner_password.minimum_characters){
        refuseLength();
        return
      }
      if(length===0||long){
        error(form,length===0?'invalid_password_empty':'invalid_password_long',password);
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
        if(e.message==='invalid_password')refuseLength();
        else error(form,e.message,e.message==='claim_token'?claimToken:password);
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
  const section=card('Connect Audiobookshelf','Mechanical Librarian only reads from Audiobookshelf. Nothing is written until you allow it.');
  const form=document.createElement('form');
  const endpoint=input('endpoint','url');
  endpoint.placeholder='http://192.168.1.10:13378';
  // Its hints follow the field rather than sit inside its label.
  const endpointField=document.createElement('div');
  endpointField.className='field';
  endpoint.id=`setup-field-${++fieldSequence}`;
  const endpointLabel=document.createElement('label');
  endpointLabel.className='field-label';
  endpointLabel.setAttribute('for',endpoint.id);
  endpointLabel.textContent='Audiobookshelf address';
  endpointField.append(endpointLabel,endpoint);
  const shape=hint(endpoint,'Include the port, for example http://192.168.1.10:13378.');
  const warning=document.createElement('p');
  warning.className='notice';
  warning.id='setup-endpoint-warning';
  warning.textContent='This address is outside your private network, so the API key would be sent unencrypted. Use an https:// address.';
  warning.hidden=true;
  endpointField.append(shape,warning);
  // Said only while the typed address is plain http:// outside a private network.
  endpoint.addEventListener('input',()=>{
    const address=endpoint.value.trim();
    warning.hidden=!/^http:\/\//i.test(address)||privateHost(address);
    endpoint.dataset.hint=warning.hidden?shape.id:`${shape.id} ${warning.id}`;
    describe(endpoint,endpoint.getAttribute('aria-invalid')==='true')
  });
  const key=input('key','password');
  key.autocomplete='off';
  const keyField=secretField('Audiobookshelf API key',key,'the Audiobookshelf API key');
  keyField.append(hint(key,'In Audiobookshelf, go to Settings, then API Keys.'));
  const verify=button('Verify and continue',true);
  form.append(endpointField,keyField,verify);
  form.noValidate=true;
  const addressCodes=['invalid_endpoint','invalid_origin','name_resolution','refused','tls','timeout','redirect','malformed','oversize'];
  form.addEventListener('submit',event=>{
    event.preventDefault();
    if(mutating)return;
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
    mutate('/api/setup/connect',{expected_revision:state.revision,name:'primary',endpoint:address,key:key.value},form,{control:(code,reached)=>code==='unauthorized'?key:reached||addressCodes.includes(code)?endpoint:null,address,submitted:verify,confirm:()=>[`Connected to Audiobookshelf at ${originOf(address)||'that address'}.`]})
  });
  section.append(form);
  endpoint.focus()
};
// Said on the step after the folders, once every one was found, in the order
// Audiobookshelf listed the libraries when this visit has seen that list.
let libraryOrder=[];
const foundFiles=()=>{
  if(state.next_step!=='providers')return[];
  const rank=library=>{const at=libraryOrder.indexOf(library.library_id);return at<0?libraryOrder.length:at};
  const ordered=[...state.libraries].sort((left,right)=>rank(left)-rank(right));
  return[`Found the files for ${listed(ordered.map(library=>library.display_name||humanName(library.name)))}.`]
};
// A check that still finds a folder missing says which, once.
const checkedFolders=()=>{
  if(state.next_step!=='mounts')return foundFiles();
  const results=new Map(state.mounts.map(item=>[item.library,item.state]));
  const missing=state.libraries.filter(library=>results.get(library.name)!=='matched');
  return missing.length?[`Still not found: ${listed(missing.map(library=>library.display_name||humanName(library.name)))}.`]:[]
};
const renderLibraries=async()=>{
  const section=card('Choose libraries');
  const form=document.createElement('form');
  // The wizard states its own refusal; a browser bubble would precede it.
  form.noValidate=true;
  const loading=document.createElement('p');
  loading.textContent='Loading libraries…';
  form.append(loading);
  section.append(form);
  try{
    const result=await api(`/api/setup/libraries?expected_revision=${state.revision}`);
    libraryOrder=result.libraries.map(item=>item.library_id);
    loading.remove();
    const usedNames=new Set();
    if(result.libraries.some(item=>item.media_kind==='unsupported')){
      const note=document.createElement('p');
      note.className='muted';
      note.textContent='Podcast libraries aren’t supported, so they’re not shown.';
      form.append(note)
    }
    // Every book library starts ticked and keeps its Audiobookshelf name.
    for(const item of result.libraries.filter(value=>value.media_kind!=='unsupported')){
      const row=document.createElement('div');
      row.className='option';
      const selected=input(`library-${item.library_id}`,'checkbox');
      selected.checked=true;
      const head=document.createElement('label');
      head.append(selected,document.createTextNode(item.name));
      row.append(head);
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
    const next=button('Continue',true);
    form.append(next);
    form.addEventListener('submit',event=>{
      event.preventDefault();
      if(mutating)return;
      const selected=[...form.querySelectorAll('.option')].filter(row=>row.querySelector('input[type=checkbox]').checked);
      if(!selected.length){
        // Under Continue, where the server's refusal of the same press sits too.
        const node=error(form,'invalid_libraries');
        next.after(node);
        next.scrollIntoView?.({block:'nearest'});
        node.scrollIntoView?.({block:'nearest'});
        return
      }
      const libraries=selected.map(row=>({name:row.dataset.name,library_id:row.dataset.id,media_kind:'book'}));
      mutate('/api/setup/libraries',{expected_revision:state.revision,libraries},form,{submitted:next,confirm:foundFiles})
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
  const section=card('Find library folders');
  const form=document.createElement('form');
  let discovered=[];
  try{discovered=(await api(`/api/setup/libraries?expected_revision=${state.revision}`)).libraries}catch{}
  const shown=library=>library.display_name||humanName(library.name);
  // Only the libraries whose folder was not found are asked about.
  const results=new Map(state.mounts.map(item=>[item.library,item.state]));
  const asked=state.libraries.filter(library=>results.get(library.name)!=='matched');
  for(const library of asked){
    const result=results.get(library.name);
    if(result){
      const line=document.createElement('p');
      line.className='mount-line mount-line-attention';
      const lead=document.createElement('span');
      const label=document.createElement('span');
      label.className='mount-state';
      label.textContent=mountState[result]||'Not found';
      lead.append(document.createTextNode(`${shown(library)}: `),label);
      line.append(lead);
      const detail=document.createElement('span');
      detail.className='mount-remedy';
      detail.textContent=mountRemedy[result]||mountRemedy.missing;
      line.append(detail);
      if(result==='missing'){
        const guide=document.createElement('a');
        guide.href=mountGuide;
        guide.rel='noreferrer';
        guide.textContent='Media mounts in the deployment guide';
        line.append(guide)
      }
      form.append(line)
    }
    const path=input(`path-${library.name}`);
    const evidence=discovered.find(item=>item.library_id===library.library_id);
    path.value=mountDraft&&library.name in mountDraft?mountDraft[library.name]:evidence?.service_path||'';
    path.placeholder='/audiobooks';
    form.append(field(`${shown(library)} folder in Audiobookshelf`,path))
  }
  const check=button('Check again',true);
  form.append(check);
  form.addEventListener('submit',event=>{
    event.preventDefault();
    if(mutating)return;
    const service_paths={};
    const draft={};
    for(const library of state.libraries){
      const control=form.elements[`path-${library.name}`];
      // A library whose folder was found is compared as Audiobookshelf reports it.
      if(!control){
        service_paths[library.name]=null;
        continue
      }
      draft[library.name]=control.value;
      service_paths[library.name]=control.value.trim()||null
    }
    mountDraft=draft;
    mutate('/api/setup/mounts',{expected_revision:state.revision,service_paths},form,{submitted:check,confirm:checkedFolders})
  });
  section.append(form)
};
// itunes_search corroborates ebooks and gives audiobooks only a cover (#1779),
// so it is grouped with the book sources (#1458).
const providerGroups=[['books','Books and ebooks',[['inventaire','inventaire'],['open','open_library'],['hardcover','hardcover'],['google','google_books'],['itunes','itunes_search']]],['audiobooks','Audiobooks',[['audible','audible_catalog'],['audnexus','audnexus'],['audiosilo','audiosilo']]]];
const renderProviders=async()=>{
  const section=card('Choose metadata sources','Choose where to look up details for your books. You can change this later in Settings.');
  const form=document.createElement('form');
  form.noValidate=true;
  // The form is drawn at once; the sources that suit the chosen libraries are
  // ticked when the library read returns, unless the owner already changed one.
  const reading=document.createElement('p');
  reading.className='muted';
  reading.textContent='Loading your libraries…';
  form.append(reading);
  const emailInvalid=control=>!control.checkValidity()||!settingsOpenLibraryEmailValid(control.value);
  const rows=[];
  let touched=false;
  let ebooks=false;
  for(const [,title,specs] of providerGroups){
    const fieldset=document.createElement('fieldset');
    fieldset.className='provider-group';
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
      if(adapter==='open_library'){
        const contact=document.createElement('p');
        contact.className='muted';
        contact.textContent='Needs your email address. No account needed.';
        row.append(contact)
      }
      const accessHref={hardcover:'https://hardcover.app/account/api',google_books:'https://console.cloud.google.com/apis/credentials'}[adapter];
      if(accessHref){
        const access=document.createElement('a');
        access.className='provider-access';
        access.href=accessHref;
        access.rel='noreferrer';
        access.textContent=`Get a ${label} key`;
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
      enabled.addEventListener('change',()=>{
        touched=true;
        disclose();
        warn()
      });
      disclose();
      cards.append(row);
      rows.push({name,adapter,label,enabled,email,key,disclose})
    }
    fieldset.append(cards);
    form.append(fieldset)
  }
  // Most ebooks are matched by the two keyed sources; without either they wait
  // for the owner. It only says so: Continue still works.
  const coverage=document.createElement('p');
  coverage.className='muted';
  coverage.setAttribute('role','status');
  const warn=()=>{
    const matched=rows.some(row=>row.enabled.checked&&['hardcover','google_books'].includes(row.adapter));
    const labels=settingsVocabulary.provider_labels;
    coverage.textContent=ebooks&&!matched?`Without ${labels.hardcover} or ${labels.google_books}, most ebooks will wait for your review.`:''
  };
  const actions=document.createElement('div');
  actions.className='actions';
  const next=button('Continue',true);
  // Held until the library read has ticked the suited sources.
  let suggested=false;
  next.setAttribute('aria-disabled','true');
  actions.append(next);
  form.append(coverage,actions);
  form.addEventListener('submit',event=>{
    event.preventDefault();
    if(mutating||!suggested)return;
    const invalidProvider=rows.find(row=>row.enabled.checked&&((row.email&&emailInvalid(row.email))||(row.key&&((settingsProviderKeyRequired(row.adapter)&&!settingsSecretValid(row.key.value))||(row.key.value&&!settingsSecretValid(row.key.value))))));
    if(invalidProvider){
      const control=invalidProvider.email&&emailInvalid(invalidProvider.email)?invalidProvider.email:invalidProvider.key;
      error(form,control===invalidProvider.email?'invalid_provider_email':!control.value&&settingsProviderKeyRequired(invalidProvider.adapter)?'missing_provider_key':'invalid_secret',control);
      return
    }
    const providers=rows.filter(row=>row.enabled.checked).map(row=>({name:row.name,adapter:row.adapter,enabled:true,cover_enabled:row.adapter==='inventaire',contact_email:row.email?.value||null,key:row.key?.value||null}));
    mutate('/api/setup/providers',{expected_revision:state.revision,providers},form,{submitted:next})
  });
  section.append(form);
  // Not awaited, so the step is ready (and focused) before the read returns.
  void suggest();
  async function suggest(){
    // An audiobooks-only library suits the audiobook sources; any other book
    // library may hold both kinds, as may every library when the read fails.
    let libraries=null;
    try{libraries=(await api(`/api/setup/libraries?expected_revision=${state.revision}`)).libraries.filter(item=>item.media_kind==='book'&&state.libraries.some(library=>library.library_id===item.library_id))}catch{}
    if(!section.isConnected)return;
    reading.remove();
    ebooks=!libraries?.length||libraries.some(item=>!item.audiobooks_only);
    const suited=new Set([...sourceRoles.audiobook,...(ebooks?sourceRoles.ebook:[])]);
    if(!touched)for(const row of rows){
      row.enabled.checked=suited.has(row.adapter);
      row.disclose()
    }
    warn();
    suggested=true;
    next.removeAttribute('aria-disabled')
  }
};
const reviewerLabels={'openai/gpt-6-luna':'GPT-6 Luna','xiaomi/mimo-v2.6-flash':'MiMo 2.6 Flash','qwen/qwen3.8-flash':'Qwen 3.8 Flash','mistralai/ministral-3b-2512':'Ministral 3B','openai/gpt-5.6-luna':'GPT-5.6 Luna','mistralai/mistral-small-2603':'Mistral Small 4'};
const reviewerLabel=model=>reviewerLabels[model]||model;
const codexActive=value=>['starting','issued','waiting','validating'].includes(value);
// Only a press moves focus to Cancel; resuming on page load leaves it.
const followCodexSignIn=async(codex,form,failed,focus)=>{
  const cancel=button('Cancel sign-in');
  cancel.type='button';
  cancel.addEventListener('click',async()=>{
    if(cancel.getAttribute('aria-disabled')==='true')return;
    cancel.setAttribute('aria-disabled','true');
    try{
      codexTerminal=await api('/api/setup/reviewer/codex/device/cancel',{method:'POST',body:'{}',mutation:true});
      await render()
    }catch(e){
      cancel.removeAttribute('aria-disabled');
      failed(e,null,cancel)
    }
  });
  codex.append(cancel);
  if(focus)cancel.focus();
  try{
    let device;
    do{
      device=await api('/api/setup/reviewer/codex/device');
      if(!form.isConnected)return;
      let prompt=codex.querySelector('[data-device-prompt]');
      if(device.verification_url&&device.user_code&&prompt?.dataset.code!==device.user_code){
        if(!prompt){
          prompt=document.createElement('div');
          prompt.dataset.devicePrompt='true';
          codex.append(prompt)
        }
        prompt.dataset.code=device.user_code;
        const lead=document.createElement('p');
        const link=document.createElement('a');
        link.href=device.verification_url;
        link.target='_blank';
        link.rel='noreferrer';
        link.textContent='Open the device sign-in page';
        lead.append(link,document.createTextNode(' in a new tab and enter this code:'));
        const row=document.createElement('p');
        row.className='device-code-row';
        const code=document.createElement('code');
        code.className='device-code';
        code.textContent=device.user_code;
        const copy=document.createElement('button');
        copy.type='button';
        copy.textContent='Copy code';
        const copied=document.createElement('span');
        copied.setAttribute('role','status');
        copy.addEventListener('click',async()=>{
          try{
            await navigator.clipboard.writeText(device.user_code);
            copied.textContent='Copied.'
          }catch{
            const range=document.createRange();
            range.selectNodeContents(code);
            getSelection()?.removeAllRanges();
            getSelection()?.addRange(range);
            copied.textContent='Code selected. Press Ctrl+C or Command+C to copy it.'
          }
        });
        row.append(code,copy,copied);
        prompt.replaceChildren(lead,row)
      }
      if(codexActive(device.state))await new Promise(resolve=>setTimeout(resolve,1000))
    }while(form.isConnected&&codexActive(device.state));
    if(!form.isConnected)return;
    codexTerminal=device;
    await render()
  }catch(e){if(form.isConnected)failed(e,null,cancel)}
};
const renderCodexOption=(form,access,failed)=>{
  const codex=document.createElement('div');
  codex.className='option reviewer-option';
  const heading=document.createElement('h3');
  heading.textContent='Codex';
  codex.append(heading);
  if(access.mapping?.engine==='codex'){
    const selected=document.createElement('p');
    selected.textContent='Selected';
    codex.append(selected)
  }
  // The first drawing of each visit is the one that may check a sign-in.
  const firstDrawing=!codexChecked;
  codexChecked=true;
  const blocked=['host_unsupported','not_provisioned'].includes(access.codex.state);
  const codexState=blocked?access.codex:(codexTerminal||access.codex);
  const signedIn=['usable','configured'].includes(codexState.state);
  const line=document.createElement('p');
  line.setAttribute('aria-live','polite');
  line.textContent=signedIn?'Signed in':'Not signed in';
  const allowance=document.createElement('p');
  allowance.textContent='Uses your ChatGPT plan.';
  codex.append(line,allowance);
  // Settings' words for each host reason (#1607).
  const reasons={profile_not_provisioned:'The Codex profile is missing. Restore the image’s profile or correct the profile path, then restart the service.',profile_unreadable:'The Codex profile cannot be read. Fix the file’s permissions or contents, then restart the service.',profile_contract_mismatch:'The Codex profile does not match the model and limits this version needs. Restore the image’s profile or regenerate it, then restart the service.',host_evidence_unavailable:'Mechanical Librarian cannot read the host’s free memory. Fix the host or container memory reporting before using Codex.',cgroup_limit_below_1_gib:'The container memory limit is below 1 GB. Increase it before using Codex.',headroom_below_1_gib:'Less than 1 GB of memory is free. Free some memory or raise the container limit.'};
  if(blocked){
    const remedy=document.createElement('p');
    remedy.textContent=reasons[access.codex.reason]||recoveryCopy(access.codex.state);
    codex.append(remedy)
  }
  // The remedy follows the shown result, and leaves once a new sign-in starts.
  const deviceRemedy={permission_or_profile:'Your ChatGPT sign-in could not be checked. Sign in with ChatGPT again.',permission_or_process:'Codex could not finish signing in. Sign in with ChatGPT again.',protocol_or_process:'Codex returned an unexpected sign-in result. Sign in with ChatGPT again.',restart_interrupted:'The last sign-in was interrupted. Sign in with ChatGPT again.'};
  const codexRemedy=document.createElement('p');
  codexRemedy.textContent=blocked?'':deviceRemedy[codexTerminal?codexTerminal.failure:access.codex.reason]||'';
  if(codexRemedy.textContent)codex.append(codexRemedy);
  if(codexActive(access.codex.state))void followCodexSignIn(codex,form,failed);
  else if(signedIn){
    if(access.mapping?.engine!=='codex'){
      const use=button('Use Codex');
      use.type='button';
      use.addEventListener('click',async()=>{
        if(use.getAttribute('aria-disabled')==='true')return;
        use.setAttribute('aria-disabled','true');
        try{
          await api('/api/setup/reviewer/selection',{method:'POST',body:JSON.stringify({expected_revision:access.revision,engine:'codex'}),mutation:true});
          await render();
          announce('Codex selected.')
        }catch(e){
          // The server recorded the refused sign-in; read it again, then say so
          // above the fresh option's first control (#1617).
          if(refusal(e)==='credential_unavailable'){
            codexTerminal=null;
            await render();
            const fresh=main.querySelector('form');
            const first=fresh?.querySelector('.option button');
            if(first)first.before(error(fresh,'credential_unavailable'));
            return
          }
          use.removeAttribute('aria-disabled');
          failed(e,null,use)
        }
      });
      codex.append(use)
    }
  }else if(!blocked){
    // A blocked host shows only its remedy: no sign-in can start there.
    const signIn=button('Sign in with ChatGPT');
    signIn.type='button';
    signIn.addEventListener('click',async()=>{
      if(signIn.disabled||signIn.getAttribute('aria-disabled')==='true')return;
      signIn.setAttribute('aria-disabled','true');
      try{
        await api('/api/setup/reviewer/codex/device',{method:'POST',body:JSON.stringify({expected_revision:access.revision}),mutation:true});
        signIn.remove();
        codexRemedy.remove();
        codex.querySelector('.error')?.remove();
        await followCodexSignIn(codex,form,failed,true)
      }catch(e){
        signIn.removeAttribute('aria-disabled');
        failed(e,null,signIn)
      }
    });
    codex.append(signIn);
    // An existing sign-in is checked once per visit, without a press.
    if(firstDrawing){
      line.textContent='Checking your ChatGPT sign-in…';
      signIn.disabled=true;
      void (async()=>{
        try{
          codexTerminal=await api('/api/setup/reviewer/codex/recheck',{method:'POST',body:'{}',mutation:true});
          if(!codex.isConnected)return;
          // Only this card is drawn again, so focus and anything typed elsewhere stay.
          renderCodexOption(form,access,failed);
          const fresh=form.lastElementChild;
          codex.replaceWith(fresh);
          announce(fresh.querySelector('[aria-live]')?.textContent||'')
        }catch(e){
          if(!form.isConnected)return;
          line.textContent='Not signed in';
          signIn.disabled=false;
          failed(e,null,signIn)
        }
      })()
    }
  }
  form.append(codex)
};
const renderOpenRouterOption=(form,access,failed)=>{
  const router=document.createElement('div');
  router.className='option reviewer-option';
  const heading=document.createElement('h3');
  heading.textContent='OpenRouter';
  router.append(heading);
  const mapped=access.mapping?.engine==='openrouter'?access.mapping.model:null;
  if(mapped){
    const selected=document.createElement('p');
    selected.textContent=`Selected: ${reviewerLabel(mapped)}`;
    router.append(selected)
  }
  // The recommended model is preselected; the others wait behind a toggle.
  const model=input('reviewer-model');
  model.value=mapped||access.recommendations[0]||'';
  model.placeholder='provider/model';
  model.maxLength=256;
  const cost=document.createElement('p');
  cost.setAttribute('aria-live','polite');
  const prices=document.createElement('p');
  const showCost=()=>{
    const label=reviewerLabel(model.value);
    const measured=access.cost_evidence.models.find(row=>row.model===model.value&&row.task==='cover');
    cost.textContent=!model.value?'':measured?`${label}: about $${Number(measured.per_1000_usd).toLocaleString('en-US',{maximumSignificantDigits:4})} per 1,000 works.`:`${label}: cost not measured.`;
    const observed=access.cover_metadata?.model===model.value?access.cover_metadata:null;
    prices.textContent=observed?`Prices in USD: ${observed.prompt_price??'Unavailable'} per input token, ${observed.completion_price??'Unavailable'} per output token, ${observed.image_price??'Unavailable'} per image, ${observed.request_price??'Unavailable'} per request.`:'Check the key to see this model’s prices.'
  };
  model.addEventListener('input',showCost);
  showCost();
  const others=document.createElement('div');
  others.id='setup-reviewer-models';
  others.className='reviewer-models';
  others.hidden=true;
  const toggle=button('Choose another model');
  toggle.type='button';
  toggle.setAttribute('aria-expanded','false');
  toggle.setAttribute('aria-controls',others.id);
  toggle.addEventListener('click',()=>{
    const expanded=toggle.getAttribute('aria-expanded')!=='true';
    toggle.setAttribute('aria-expanded',String(expanded));
    others.hidden=!expanded
  });
  const list=document.createElement('ul');
  const seen=new Set();
  for(const candidate of [...access.recommendations.map(value=>({model:value,tier:'paid'})),...access.candidates]){
    if(seen.has(candidate.model))continue;
    seen.add(candidate.model);
    const item=document.createElement('li');
    const choose=button(`Use ${reviewerLabel(candidate.model)} (${candidate.tier==='free'?'Free':'Paid'})`);
    choose.type='button';
    choose.addEventListener('click',()=>{
      model.value=candidate.model;
      model.dispatchEvent(new Event('input'));
      model.focus()
    });
    item.append(choose);
    list.append(item)
  }
  others.append(list,field('Model ID',model));
  const key=input('reviewer-key','password');
  key.autocomplete='new-password';
  key.maxLength=2048;
  const keyField=field('OpenRouter API key',key);
  const getKey=document.createElement('p');
  getKey.className='hint';
  const getKeyLink=document.createElement('a');
  getKeyLink.href='https://openrouter.ai/settings/keys';
  getKeyLink.rel='noreferrer';
  getKeyLink.textContent='Get a key';
  getKey.append(getKeyLink);
  const save=button('Check key and use OpenRouter');
  save.type='button';
  save.addEventListener('click',async()=>{
    if(save.getAttribute('aria-disabled')==='true')return;
    save.setAttribute('aria-disabled','true');
    save.setAttribute('aria-live','polite');
    save.textContent='Checking key…';
    try{
      await api('/api/setup/reviewer/openrouter',{method:'POST',body:JSON.stringify({expected_revision:access.revision,api_key:key.value,model:model.value}),mutation:true});
      key.value='';
      await render();
      announce('OpenRouter selected.')
    }catch(e){
      key.value='';
      save.removeAttribute('aria-disabled');
      save.textContent='Check key and use OpenRouter';
      const aboutModel=['model_unavailable','model_text_only'].includes(refusal(e));
      // The refused model is shown, so its field can take focus.
      if(aboutModel){
        toggle.setAttribute('aria-expanded','true');
        others.hidden=false
      }
      failed(e,aboutModel?model:key,save)
    }
  });
  // Rates and data use are reference detail, read on demand.
  const about=document.createElement('details');
  about.className='reviewer-detail';
  const aboutSummary=document.createElement('summary');
  aboutSummary.textContent='Prices and data use';
  const packet=document.createElement('p');
  packet.textContent='One request per work, with every cover it compares.';
  const freeCaveat=document.createElement('p');
  freeCaveat.textContent=access.free_tier_caveat;
  const caveat=document.createElement('p');
  caveat.textContent=access.data_use_caveat;
  about.append(aboutSummary,prices,packet,freeCaveat,caveat);
  router.append(cost,toggle,others,keyField,getKey,save,about);
  form.append(router)
};
const renderReviewer=async()=>{
  const section=card('Cover reviewer (optional)','An AI model can look at cover images and tell you what it sees. It only advises, and you still choose. You can set this up later in Settings.');
  const form=document.createElement('form');
  section.append(form);
  // `control` is the field the refusal is about and `submitted` the button pressed.
  // A field refusal sits just above that button, between the field and it; a
  // button-only refusal sits directly under the button. Without either it opens the
  // form, so it is scrolled into view rather than left above a phone viewport.
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
    if(submitted)control?submitted.before(node):submitted.after(node);
    // Bring the button in first, then its alert, so both end up on screen together.
    submitted?.scrollIntoView?.({block:'nearest'});
    node.scrollIntoView?.({block:'nearest'})
  };
  let access;
  try{access=await api('/api/setup/reviewer-access')}catch(e){
    failed(e);
    return
  }
  renderCodexOption(form,access,failed);
  renderOpenRouterOption(form,access,failed);
  const actions=document.createElement('div');
  actions.className='actions';
  const next=button('Continue',true);
  next.type='button';
  next.addEventListener('click',()=>{
    if(mutating)return;
    if(!access.mapping){
      const node=error(form,'reviewer_unchosen');
      actions.after(node);
      next.scrollIntoView?.({block:'nearest'});
      node.scrollIntoView?.({block:'nearest'});
      return
    }
    mutate('/api/setup/reviewer',{expected_revision:state.revision,outcome:'configured'},form,{submitted:next})
  });
  const skip=button('Skip for now');
  skip.type='button';
  skip.addEventListener('click',()=>mutate('/api/setup/reviewer',{expected_revision:state.revision,outcome:'skipped'},form,{submitted:skip}));
  actions.append(next,skip);
  form.append(actions)
};
// The cross-document motion runs only for the handoff into the run's detail.
const handoffMotion=()=>{
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
};
// Start opens Library with the owner's existing session. The run is read for
// up to 10 seconds so Library can open on its detail; without it, Library
// opens on its own.
const land=async()=>{
  const deadline=Date.now()+10000;
  let run=null;
  while(run===null&&Date.now()<deadline){
    try{
      // A read that hangs is given up at the deadline, so Library still opens.
      const response=await fetch('/api/v1/review-preparation',{credentials:'same-origin',signal:AbortSignal.timeout(Math.max(1,deadline-Date.now()))});
      if(!response.ok)break;
      const value=(await response.json())?.presentation?.run_id;
      if(typeof value==='string'&&/^preparation_run_[0-9a-f]{64}$/.test(value))run=value
    }catch{break}
    if(run===null)await new Promise(resolve=>setTimeout(resolve,500))
  }
  if(run!==null){
    handoffMotion();
    // The motion sheet applies on the next rendered frame; navigating before
    // it would skip the transition. A hidden tab draws no frames, so wait at most 200 ms.
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>resolve()));
    await Promise.race([frame().then(frame),new Promise(resolve=>setTimeout(resolve,200))])
  }
  location.replace(run===null?'/library?lens=all':`/library?lens=all&preparation=${run}`)
};
const renderStart=async()=>{
  const section=card('Start','Check your choices, then start. Mechanical Librarian will read your library and suggest changes for you to review.');
  let reviewerChoice='No reviewer';
  try{
    const access=await api('/api/setup/reviewer-access');
    const mapping=access.mapping;
    reviewerChoice=mapping?.engine==='openrouter'?`${reviewerLabel(mapping.model)} on OpenRouter`:mapping?.engine==='codex'?'Codex':mapping?.engine?humanName(mapping.engine):'No reviewer'
  }catch{reviewerChoice='Not loaded. Reload the page to see it.'}
  const sourceNames=(state.providers||[]).filter(item=>item.enabled).map(item=>settingsVocabulary.provider_labels[item.adapter]||humanName(item.adapter));
  const libraryLines=(state.libraries||[]).map(library=>{
    const result=(state.mounts||[]).find(item=>item.library===library.name)?.state;
    return `${library.display_name||humanName(library.name)}: ${result==='matched'?'files found':mountState[result]||'Not found'}`
  });
  const summary=document.createElement('dl');
  summary.className='summary';
  for(const [label,step,lines,action] of [['Audiobookshelf address','connect',[state.connection?.endpoint||'Not connected'],'Change the Audiobookshelf address'],['Libraries','libraries',libraryLines.length?libraryLines:['None selected'],'Change the selected libraries'],['Metadata sources','providers',[sourceNames.length?sourceNames.join(', '):'None'],'Change the metadata sources'],['Cover reviewer','reviewer',[reviewerChoice],'Change the cover reviewer']]){
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
  const submit=button('Start',true);
  form.append(notice,submit);
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    // Marked rather than natively disabled, so the pressed button keeps focus.
    if(submit.getAttribute('aria-disabled')==='true')return;
    submit.setAttribute('aria-disabled','true');
    announce('Starting…');
    try{
      await api('/api/setup/kick-off',{method:'POST',body:JSON.stringify({expected_revision:state.revision}),mutation:true})
    }catch(e){
      submit.removeAttribute('aria-disabled');
      if(refusal(e)==='stale'){
        await load();
        const fresh=main.querySelector('form');
        if(fresh)error(fresh,'stale');
        return
      }
      // No Start failure is about a field, so it sits under the button.
      const node=error(form,refusal(e));
      submit.after(node);
      submit.scrollIntoView?.({block:'nearest'});
      node.scrollIntoView?.({block:'nearest'});
      return
    }
    await land()
  });
  section.append(form)
};
const render=async()=>{
  drawLedger();
  await ({claim:renderClaim,connect:renderConnect,libraries:renderLibraries,mounts:renderMounts,providers:renderProviders,reviewer:renderReviewer,kick_off:renderStart}[state.next_step]||renderClaim)()
};
const load=async()=>{
  csrf=null;
  const shown=state?.next_step;
  try{
    state=await api('/api/setup/state');
    csrf=state.csrf_token;
    if(state.next_step!=='mounts')mountDraft=null;
    // A new visit to the reviewer step checks an existing sign-in again.
    if(state.next_step!==shown)codexChecked=false;
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
    # The sources each kind of library suits, read from their one owner.
    + ";const sourceRoles="
    + json.dumps(
        {
            "ebook": sorted(EBOOK_DISPATCH_ADAPTERS),
            "audiobook": sorted(AUDIOBOOK_DISPATCH_ADAPTERS),
        },
        separators=(",", ":"),
    )
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
