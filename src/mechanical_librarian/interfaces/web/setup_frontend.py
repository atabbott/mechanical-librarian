"""Self-contained setup assets; no normal workspace code is loaded before handoff."""

from __future__ import annotations

import json
from importlib.resources import files

SETUP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Set up Mechanical Librarian</title>
  <link rel="stylesheet" href="/assets/setup.css">
</head>
<body>
  <a class="skip-link" href="#setup-main">Skip to setup</a>
  <header class="masthead"><span class="book-mark" aria-hidden="true">ML</span><strong>Mechanical Librarian</strong></header>
  <div class="setup-shell">
    <aside aria-label="Setup progress">
      <h1>Set up your library</h1>
      <p>Connect Audiobookshelf and choose what Mechanical Librarian will review.</p>
      <ol id="setup-ledger"></ol>
    </aside>
    <main id="setup-main" tabindex="-1" aria-busy="true"></main>
  </div>
  <div id="setup-status" class="sr-only" role="status" aria-live="polite"></div>
  <script type="module" src="/assets/setup.js"></script>
</body>
</html>
"""

SETUP_CSS = """:root{color-scheme:light dark;--paper:#f4f2ec;--sheet:#fbfaf6;--ink:#17201e;--quiet:#56615e;--teal:#0f4c42;--wash:#dcebe6;--line:#d9d7cf;--strong:#b9beb9;--ochre:#8a5a16;--red:#8c3434;--focus:#146fba;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:var(--paper);color:var(--ink)}*{box-sizing:border-box}body{margin:0;min-width:320px;background:var(--paper);color:var(--ink)}button,input,select{font:inherit}button,input,select{min-height:44px;border:1px solid var(--strong);border-radius:.5rem;background:var(--sheet);color:var(--ink);padding:.65rem .8rem}button{font-weight:750;cursor:pointer}.primary{background:var(--teal);color:#fbfaf6;border-color:var(--teal)}button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.masthead{min-height:62px;display:flex;align-items:center;gap:.75rem;padding:.7rem clamp(1rem,3vw,2.6rem);border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--sheet) 92%,transparent)}.masthead span:last-child{margin-left:auto;color:var(--quiet);font-size:.78rem}.book-mark{display:grid;place-items:center;width:34px;height:40px;border-radius:2px 5px 5px 2px;background:var(--teal);color:#fff;font:700 .72rem Georgia,serif}.setup-shell{max-width:76rem;margin:0 auto;padding:clamp(1rem,4vw,3rem);display:grid;grid-template-columns:minmax(15rem,21rem) minmax(0,1fr);gap:clamp(1.5rem,5vw,5rem)}aside{align-self:start;position:sticky;top:1.5rem}h1,h2,h3{font-family:Georgia,Times New Roman,serif;line-height:1.17}h1{font-size:clamp(1.8rem,4vw,2.65rem);margin:.25rem 0 1rem}h2{font-size:clamp(1.55rem,3vw,2.1rem);margin:.25rem 0 .75rem}p{line-height:1.55}.eyebrow{text-transform:uppercase;letter-spacing:.08em;font-size:.7rem;font-weight:800;color:var(--teal)}ol{list-style:none;padding:0;margin:1.5rem 0}.ledger-row{display:grid;grid-template-columns:1.5rem 1fr auto;align-items:center;gap:.6rem;padding:.7rem 0;border-top:1px solid var(--line);font-size:.82rem}.ledger-row[aria-current=step]{color:var(--teal);font-weight:800}.ledger-row button{min-height:36px;padding:.35rem .55rem;font-size:.72rem}.step-card{border:1px solid var(--line);border-radius:1rem;background:var(--sheet);padding:clamp(1rem,4vw,2.2rem);min-height:26rem}.intro{max-width:66ch;color:var(--quiet)}form{display:grid;gap:1rem;margin-top:1.5rem}.field{display:grid;gap:.4rem}.field[hidden]{display:none}.field span{font-size:.74rem;font-weight:800}.actions{display:flex;flex-wrap:wrap;gap:.7rem;margin-top:.6rem}.notice{border-left:3px solid var(--ochre);padding:.8rem 1rem;background:color-mix(in srgb,var(--ochre) 10%,var(--sheet));border-radius:0 .65rem .65rem 0}.error{color:var(--red);font-weight:700}.option{display:grid;gap:.7rem;padding:1rem;border:1px solid var(--line);border-radius:.75rem}.option>label:first-child{display:flex;align-items:center;gap:.7rem;font-family:Georgia,serif;font-weight:700}.option input[type=checkbox]{min-height:auto;width:1.1rem;height:1.1rem}.muted{color:var(--quiet);font-size:.8rem}.mount-state{font-weight:800}.skip-link{position:fixed;left:1rem;top:1rem;z-index:4;transform:translateY(-200%);padding:.7rem;background:var(--sheet);color:var(--teal)}.skip-link:focus{transform:none}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@media(max-width:760px){.setup-shell{grid-template-columns:1fr;padding:1rem .75rem 4rem}.masthead{padding:.65rem .75rem}aside{position:static}.step-card{min-height:0}.actions>*{flex:1 1 10rem}}@media(prefers-color-scheme:dark){:root{--paper:#0f1512;--sheet:#151d19;--ink:#edf2ee;--quiet:#a9b6b0;--teal:#8ed2c1;--wash:#203b34;--line:#2d3933;--strong:#4a5a52;--ochre:#efbd70;--red:#f39b9b;--focus:#78bdf2}.primary{background:#61b29f;color:#0f1512}}@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}.provider-links{display:flex;flex-wrap:wrap;justify-content:center;gap:.5rem 1rem;padding:1rem;color:var(--quiet);font-size:.75rem}.provider-links a{min-height:44px;display:inline-flex;align-items:center;color:var(--teal);font-weight:700}"""
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
    "@media(max-width:760px){.masthead{flex-wrap:wrap}.masthead span:last-child{"
    "margin-left:0;width:100%}}"
    ".workspace-handoff{display:inline-flex;align-items:center;justify-content:center;"
    "min-height:44px;width:max-content;max-width:100%;margin-top:1rem;padding:.65rem .9rem;"
    "border-radius:.5rem;text-decoration:none;transition:transform 160ms ease,filter 160ms ease}"
    ".workspace-handoff:hover{transform:translateY(-2px);filter:brightness(1.08)}"
    ".provider-access{width:max-content;max-width:100%;min-height:44px;display:inline-flex;"
    "align-items:center;color:var(--teal);font-weight:750;text-decoration-line:underline;"
    "text-decoration-thickness:.1em;text-underline-offset:.2em;overflow-wrap:anywhere}"
    ".provider-access:hover{text-decoration-thickness:.16em}"
)

SETUP_JS = r"""const main=document.querySelector('#setup-main');const ledger=document.querySelector('#setup-ledger');const status=document.querySelector('#setup-status');const steps=[['claim','Set owner password'],['connect','Connect Audiobookshelf'],['providers','Choose metadata providers'],['libraries','Choose libraries'],['mounts','Check read-only mounts'],['kick_off','Kick off preparation']];let csrf=null;let state=null;const api=async(path,options={})=>{const headers={'content-type':'application/json',...(options.mutation?{'x-csrf-token':csrf,'origin':location.origin}:{})};const response=await fetch(path,{credentials:'same-origin',...options,headers:{...headers,...options.headers}});let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body?.detail?.error||body?.error||'unavailable');return body};const announce=value=>{status.textContent='';requestAnimationFrame(()=>{status.textContent=value})};const escapeName=value=>String(value).replace(/[^a-z0-9_-]+/gi,'-').replace(/^-+|-+$/g,'').toLowerCase().slice(0,64)||'library';const field=(label,input)=>{const wrap=document.createElement('label');wrap.className='field';const text=document.createElement('span');text.textContent=label;wrap.append(text,input);return wrap};const input=(name,type='text')=>{const value=document.createElement('input');value.name=name;value.type=type;value.autocomplete='off';return value};const button=(label,primary=false)=>{const value=document.createElement('button');value.type='submit';value.textContent=label;if(primary)value.className='primary';return value};const error=(form,message)=>{let node=form.querySelector('.error');if(!node){node=document.createElement('p');node.className='error';node.setAttribute('role','alert');form.prepend(node)}node.textContent=recovery(message)};const recovery=code=>({unauthorized:'Audiobookshelf did not accept that key. Check its access and try again.',tls:'The secure connection could not be verified. Check the certificate and address.',timeout:'Audiobookshelf did not answer in time. Check the address and retry.',redirect:'That address redirected elsewhere. Use the direct Audiobookshelf address.',oversize:'Audiobookshelf returned more data than setup can safely read.',malformed:'Audiobookshelf returned an unexpected response.',invalid_secret:settingsVocabulary.errors.secret,invalid_provider_email:settingsVocabulary.errors.open_library_email,missing_provider_key:settingsVocabulary.errors.provider_key,stale:'Setup changed in another tab. The latest recorded step has been restored.',invalid_libraries:settingsVocabulary.errors.libraries,invalid_library_name:settingsVocabulary.errors.library_name,mounts_unready:'Every selected ebook library needs one matching read-only mount before kickoff.'}[code]||'That could not be recorded. Check the fields and try again.');const mutate=async(path,payload,form)=>{try{await api(path,{method:'POST',body:JSON.stringify(payload),mutation:true});await load();announce('Setup progress recorded')}catch(e){if(e.message==='stale'){await load()}error(form,e.message)}};const drawLedger=()=>{ledger.replaceChildren();for(const [id,label] of steps){const row=document.createElement('li');row.className='ledger-row';const complete=state?.completed_steps?.includes(id);row.setAttribute('aria-current',state?.next_step===id?'step':'false');const mark=document.createElement('span');mark.textContent=complete?'✓':state?.next_step===id?'→':'·';const text=document.createElement('span');text.textContent=label;row.append(mark,text);if(complete&&id!=='claim'){const change=document.createElement('button');change.type='button';change.textContent='Change';change.addEventListener('click',async()=>{try{await api('/api/setup/change',{method:'POST',body:JSON.stringify({expected_revision:state.revision,step:id}),mutation:true});await load();announce(`${label} reopened`)}catch(e){announce(recovery(e.message))}});row.append(change)}ledger.append(row)}};const card=(title,intro)=>{main.replaceChildren();main.setAttribute('aria-busy','false');const section=document.createElement('section');section.className='step-card';const eyebrow=document.createElement('p');eyebrow.className='eyebrow';eyebrow.textContent=`Step ${steps.findIndex(([id])=>id===state.next_step)+1} of 6`;const heading=document.createElement('h2');heading.tabIndex=-1;heading.textContent=title;const copy=document.createElement('p');copy.className='intro';copy.textContent=intro;section.append(eyebrow,heading,copy);main.append(section);requestAnimationFrame(()=>{heading.focus({preventScroll:true});section.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})});return section};const renderClaim=()=>{if(!state)state={next_step:'claim',completed_steps:[]};const section=card('Set owner password','Use the owner password provisioned with this deployment. Mechanical Librarian never stores the password itself.');const form=document.createElement('form');const password=input('password','password');password.autocomplete='current-password';form.append(field('Owner password',password),button('Continue',true));form.addEventListener('submit',async event=>{event.preventDefault();try{const result=await api('/api/setup/session',{method:'POST',body:JSON.stringify({password:password.value})});csrf=result.csrf_token;await load()}catch(e){error(form,e.message)}});section.append(form);password.focus()};const renderConnect=()=>{const section=card('Connect Audiobookshelf','Setup makes one bounded read of the supported libraries API. Nothing is written to Audiobookshelf.');const form=document.createElement('form');const endpoint=input('endpoint','url');endpoint.placeholder='https://audiobookshelf.example';const key=input('key','password');key.autocomplete='off';form.append(field('Audiobookshelf address',endpoint),field('API key',key),button('Verify and continue',true));form.addEventListener('submit',event=>{event.preventDefault();mutate('/api/setup/connect',{expected_revision:state.revision,name:'primary',endpoint:endpoint.value,key:key.value},form)});section.append(form);endpoint.focus()};const renderProviders=()=>{const section=card('Choose metadata providers','Mechanical Librarian gets better results when it can compare more sources. Connect as many providers as you reasonably can; you can add or change them later.');const form=document.createElement('form');const specs=[['open','open_library','Open Library'],['hardcover','hardcover','Hardcover'],['google','google_books','Google Books']];const rows=[];for(const [name,adapter,label] of specs){const row=document.createElement('div');row.className='option';const enabled=input(`${name}-enabled`,'checkbox');const head=document.createElement('label');head.append(enabled,document.createTextNode(label));row.append(head);const email=adapter==='open_library'?input(`${name}-email`,'email'):null;const key=adapter!=='open_library'?input(`${name}-key`,'password'):null;const emailField=email?field('Contact email',email):null;const keyField=key?field('API key',key):null;if(emailField)row.append(emailField);if(keyField)row.append(keyField);const disclose=()=>{for(const control of [email,key]){if(control){control.disabled=!enabled.checked;control.closest('.field').hidden=!enabled.checked}}};enabled.addEventListener('change',disclose);disclose();form.append(row);rows.push({name,adapter,enabled,email,key})}const actions=document.createElement('div');actions.className='actions';actions.append(button('Continue',true));const skip=button('Skip providers');skip.type='button';skip.addEventListener('click',()=>mutate('/api/setup/providers',{expected_revision:state.revision,providers:[]},form));actions.append(skip);form.append(actions);form.addEventListener('submit',event=>{event.preventDefault();const invalidProvider=rows.find(row=>row.enabled.checked&&((row.email&&!settingsOpenLibraryEmailValid(row.email.value))||(row.key&&((settingsProviderKeyRequired(row.adapter)&&!settingsSecretValid(row.key.value))||(row.key.value&&!settingsSecretValid(row.key.value))))));if(invalidProvider){const control=invalidProvider.email&&!settingsOpenLibraryEmailValid(invalidProvider.email.value)?invalidProvider.email:invalidProvider.key;control.focus();error(form,control===invalidProvider.email?'invalid_provider_email':!control.value&&settingsProviderKeyRequired(invalidProvider.adapter)?'missing_provider_key':'invalid_secret');return}const providers=rows.filter(row=>row.enabled.checked).map(row=>({name:row.name,adapter:row.adapter,enabled:true,cover_enabled:false,contact_email:row.email?.value||null,key:row.key?.value||null}));mutate('/api/setup/providers',{expected_revision:state.revision,providers},form)});section.append(form)};const renderLibraries=async()=>{const section=card('Choose libraries','Select one or more book libraries.');const form=document.createElement('form');const loading=document.createElement('p');loading.textContent='Reading libraries…';form.append(loading);section.append(form);try{const result=await api(`/api/setup/libraries?expected_revision=${state.revision}`);loading.remove();for(const item of result.libraries){const row=document.createElement('div');row.className='option';const selected=input(`library-${item.library_id}`,'checkbox');selected.disabled=item.media_kind==='unsupported';const head=document.createElement('label');head.append(selected,document.createTextNode(item.name));row.append(head);const name=input(`name-${item.library_id}`);name.value=escapeName(item.name);name.maxLength=settingsVocabulary.local_library_name.maximum;name.pattern=settingsVocabulary.local_library_name.pattern;name.title=settingsVocabulary.errors.library_name;const kind=document.createElement('select');kind.name=`kind-${item.library_id}`;for(const value of ['ebook','audiobook']){const option=document.createElement('option');option.value=value;option.textContent=value==='ebook'?'Ebook library':'Audiobook library';kind.append(option)}row.append(field('Local name',name),field('Library kind',kind));if(item.media_kind==='unsupported'){name.disabled=true;kind.disabled=true;const note=document.createElement('p');note.className='muted';note.textContent='This library type is not supported.';row.append(note)}row.dataset.id=item.library_id;form.append(row)}form.append(button('Continue',true));form.addEventListener('submit',event=>{event.preventDefault();const selected=[...form.querySelectorAll('.option')].filter(row=>row.querySelector('input[type=checkbox]').checked);if(!selected.length){error(form,'invalid_libraries');return}const invalid=selected.map(row=>row.querySelector('input[type=text]')).find(control=>!control.checkValidity()||!settingsLocalLibraryNameValid(control.value));if(invalid){invalid.focus();invalid.reportValidity();error(form,'invalid_library_name');return}const libraries=selected.map(row=>({name:row.querySelector('input[type=text]').value,library_id:row.dataset.id,media_kind:row.querySelector('select').value,provider_connections:state.providers.filter(value=>value.enabled).map(value=>value.name)}));mutate('/api/setup/libraries',{expected_revision:state.revision,libraries},form)})}catch(e){loading.textContent=recovery(e.message);loading.className='error';const retry=button('Retry');retry.type='button';retry.addEventListener('click',render);form.append(retry)}};const renderMounts=()=>{const section=card('Check read-only mounts','Check that each selected library can reach its files. Mechanical Librarian only reads these folders.');const form=document.createElement('form');if(state.mounts.length){const results=document.createElement('div');results.className='mount-results notice';results.setAttribute('role','status');const mountCopy={matched:'A deployment-owned read-only mapping matches this service path.',unmatched:'No read-only mapping matches yet. Correct the Audiobookshelf service path and compare again; Mechanical Librarian never creates or changes mounts.',ambiguous:'More than one read-only mapping matches. Correct the service path so exactly one deployment-owned read-only mapping matches.',unobservable:'No service path was available to compare. Enter the Audiobookshelf service path; Mechanical Librarian never creates or changes mounts.'};for(const item of state.mounts){const line=document.createElement('p');line.textContent=`${item.library}: ${mountCopy[item.state]}`;results.append(line)}form.append(results)}for(const library of state.libraries){const path=input(`path-${library.name}`);path.placeholder=library.media_kind==='audiobook'?'Optional for audiobook libraries':'/service/path/to/books';form.append(field(`${library.name} service path`,path))}form.append(button('Compare mounts',true));form.addEventListener('submit',event=>{event.preventDefault();const service_paths={};for(const library of state.libraries){const value=form.elements[`path-${library.name}`].value.trim();service_paths[library.name]=value||null}mutate('/api/setup/mounts',{expected_revision:state.revision,service_paths},form)});section.append(form)};const renderKickoff=()=>{const section=card('Kick off preparation','Preparation will begin in the workspace.');const notice=document.createElement('p');notice.className='notice';notice.textContent='Nothing in your library changes. Writes stay off until you turn them on in Activity.';const form=document.createElement('form');form.append(notice,button('Kick off preparation',true));form.addEventListener('submit',async event=>{event.preventDefault();try{const result=await api('/api/setup/kick-off',{method:'POST',body:JSON.stringify({expected_revision:state.revision}),mutation:true});section.replaceChildren();const heading=document.createElement('h2');heading.textContent='Preparation is starting';const copy=document.createElement('p');copy.textContent=result.message;const progress=document.createElement('p');progress.className='notice';progress.textContent='Waiting for the normal workspace…';section.append(heading,copy,progress);const bridge=setInterval(async()=>{try{const response=await fetch('/api/v1/review-preparation',{credentials:'same-origin'});if(response.status===401){clearInterval(bridge);for(const row of ledger.querySelectorAll('.ledger-row')){row.removeAttribute('aria-current');row.querySelector('span').textContent='✓';row.querySelector('button')?.remove()}heading.textContent='Sign in to continue';copy.textContent='Your setup sign-in expired while preparation continued safely. Sign in to the workspace to see live progress.';progress.textContent='Preparation continues in the normal workspace.';const signIn=document.createElement('a');signIn.href='/library?lens=all';signIn.textContent='Sign in to workspace';section.append(signIn);heading.tabIndex=-1;heading.focus({preventScroll:true});section.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});return}if(response.ok){const preparation=await response.json();const libraries=Array.isArray(preparation.libraries)?preparation.libraries:[];const ready=libraries.reduce((sum,item)=>sum+item.ready,0);const checked=libraries.reduce((sum,item)=>sum+item.checked,0);const total=libraries.reduce((sum,item)=>sum+item.total,0);progress.textContent=`${ready} ready · ${checked} of ${total} checked`;const needsAttention=preparation.actionable||preparation.paused;if(ready>0||preparation.first_boot_complete||needsAttention){clearInterval(bridge);for(const row of ledger.querySelectorAll('.ledger-row')){row.removeAttribute('aria-current');row.querySelector('span').textContent='✓';row.querySelector('button')?.remove()}heading.textContent=ready>0?'Your work is ready':preparation.first_boot_complete?'Preparation is complete':'Preparation needs attention';copy.textContent=ready>0?(preparation.first_boot_complete?'Preparation is complete. Open the workspace to review your ready work.':'Open the workspace while preparation continues.'):preparation.first_boot_complete?(preparation.actionable?'No work became ready. Open the workspace to review the failures and recovery.':'Preparation finished without ready work. Open the workspace to continue.'):'Open the workspace to review the paused connection and use Check connection again.';const open=document.createElement('a');open.href='/library?lens=all';open.textContent='Open workspace';section.append(open);heading.tabIndex=-1;heading.focus({preventScroll:true});section.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})}}}catch{}},1500)}catch(e){error(form,e.message)}});section.append(form)};const render=async()=>{drawLedger();({claim:renderClaim,connect:renderConnect,providers:renderProviders,libraries:renderLibraries,mounts:renderMounts,kick_off:renderKickoff}[state.next_step]||renderClaim)()};const load=async()=>{csrf=null;try{state=await api('/api/setup/state');csrf=state.csrf_token;await render()}catch{state=null;renderClaim()}};load();"""


_VOCABULARY = json.loads(
    files(__package__).joinpath("settings_vocabulary.json").read_text(encoding="utf-8")
)
SETUP_JS = (
    "const settingsVocabulary="
    + json.dumps(_VOCABULARY, separators=(",", ":"), ensure_ascii=True)
    + ";const settingsTextValid=(value,rule)=>Array.from(value).length>=rule.minimum&&Array.from(value).length<=rule.maximum;"
    + "const settingsSecretValid=value=>settingsTextValid(value,settingsVocabulary.secret)&&new RegExp(settingsVocabulary.secret.printable_ascii,'u').test(value)&&!value.toLowerCase().startsWith(settingsVocabulary.secret.forbidden_prefix);"
    + "const settingsOpenLibraryEmailValid=value=>settingsTextValid(value,settingsVocabulary.open_library_email)&&new RegExp(settingsVocabulary.open_library_email.pattern,'u').test(value);"
    + "const settingsLocalLibraryNameValid=value=>settingsTextValid(value,settingsVocabulary.local_library_name)&&new RegExp(settingsVocabulary.local_library_name.pattern,'u').test(value);"
    + "const settingsProviderKeyRequired=adapter=>settingsVocabulary.required_provider_keys.includes(adapter);"
    + SETUP_JS.replace(
        "const field=(label,input)=>",
        "const humanName=value=>String(value).split(/[-_]+/u).filter(Boolean).map(word=>word.charAt(0).toUpperCase()+word.slice(1)).join(' ');const field=(label,input)=>",
    )
    .replace(
        "invalid_library_name:settingsVocabulary.errors.library_name,",
        "invalid_library_name:settingsVocabulary.errors.library_name,invalid_library_display_name:'Use a library name from 1 to 256 characters without leading or trailing spaces.',",
    )
    .replace(
        "['claim','Set owner password']",
        "['claim','Create owner access']",
    )
    .replace(
        "{'x-csrf-token':csrf,'origin':location.origin}",
        "{'x-csrf-token':csrf}",
    )
    .replace(
        "mounts_unready:'Every selected ebook library needs one matching read-only mount before kickoff.'",
        "mounts_unready:'Every selected book library needs one matching read-only mount before kickoff.',invalid_password:'Use matching passwords between 1 and 1,024 bytes.',claimed:'This workspace was claimed in another tab. Sign in with its owner password.',csrf:'This setup form expired. Reload it and try again.'",
    )
    .replace(
        "const renderClaim=()=>{if(!state)state={next_step:'claim',completed_steps:[]};const section=card('Set owner password','Use the owner password provisioned with this deployment. Mechanical Librarian never stores the password itself.');const form=document.createElement('form');const password=input('password','password');password.autocomplete='current-password';form.append(field('Owner password',password),button('Continue',true));form.addEventListener('submit',async event=>{event.preventDefault();try{const result=await api('/api/setup/session',{method:'POST',body:JSON.stringify({password:password.value})});csrf=result.csrf_token;await load()}catch(e){error(form,e.message)}});section.append(form);password.focus()}",
        "const renderClaim=async()=>{if(!state)state={next_step:'claim',completed_steps:[]};let claim;try{claim=await api('/api/setup/claim')}catch(e){const section=card('Setup is unavailable','Mechanical Librarian could not open owner access safely.');const retry=button('Retry',true);retry.type='button';retry.addEventListener('click',renderClaim);section.append(retry);return}state.revision=claim.revision;const section=card(claim.claimed?'Sign in to continue':'Create your owner password',claim.claimed?'Owner access already exists. Sign in with the owner password to resume exactly where you stopped.':'The first visitor creates the only owner password. Choose the password you will use to sign in. Mechanical Librarian never stores the password itself.');const form=document.createElement('form');const password=input('password','password');password.autocomplete=claim.claimed?'current-password':'new-password';form.append(field('Owner password',password));let confirmation=null;if(!claim.claimed){confirmation=input('confirmation','password');confirmation.autocomplete='new-password';form.append(field('Confirm owner password',confirmation))}form.append(button(claim.claimed?'Sign in':'Create owner access',true));form.addEventListener('submit',async event=>{event.preventDefault();if(confirmation&&password.value!==confirmation.value){error(form,'invalid_password');confirmation.focus();return}try{const payload={password:password.value,...(claim.claimed?{}:{confirmation:confirmation.value,expected_revision:claim.revision})};const result=await api('/api/setup/session',{method:'POST',body:JSON.stringify(payload),headers:claim.claimed?{}:{'x-setup-nonce':claim.nonce}});csrf=result.csrf_token;await load()}catch(e){if(e.message==='claimed'){state=null;await renderClaim();return}error(form,e.message)}});section.append(form);password.focus()}",
    )
    .replace(
        "const form=document.createElement('form');const specs=[['open','open_library','Open Library'],['hardcover','hardcover','Hardcover'],['google','google_books','Google Books']];",
        "const links=document.createElement('nav');links.className='provider-links';links.setAttribute('aria-label','Provider account links');const lead=document.createElement('span');lead.textContent='Get provider access:';links.append(lead);for(const [label,href] of [['Open Library','https://openlibrary.org/account/create'],['Hardcover','https://hardcover.app/account/api'],['Google Books','https://console.cloud.google.com/apis/credentials']]){const link=document.createElement('a');link.href=href;link.rel='noreferrer';link.textContent=label;links.append(link)}section.append(links);const form=document.createElement('form');const specs=[['open','open_library','Open Library'],['hardcover','hardcover','Hardcover'],['google','google_books','Google Books']];",
    )
    .replace(
        "loading.remove();for(const item of result.libraries){",
        "loading.remove();const usedNames=new Set();const unsupported=result.libraries.filter(item=>item.media_kind==='unsupported');if(unsupported.length){const note=document.createElement('p');note.className='notice';note.textContent=`${unsupported.length} unsupported podcast ${unsupported.length===1?'library':'libraries'} excluded.`;form.append(note)}for(const item of result.libraries.filter(value=>value.media_kind!=='unsupported')){",
    )
    .replace(
        "row.append(field('Local name',name),field('Library kind',kind));",
        "row.append(field('Local name',name));",
    )
    .replace(
        "media_kind:row.querySelector('select').value",
        "media_kind:'book'",
    )
    .replace(
        "Preparation will begin in the workspace.",
        "Preparation will begin in the workspace.",
    )
    .replace(
        "const progress=document.createElement('p');progress.className='notice';progress.textContent='Waiting for the normal workspace…';section.append(heading,copy,progress);",
        "const progress=document.createElement('p');progress.className='notice';progress.textContent='Starting preparation…';const open=document.createElement('a');open.href='/library?lens=all';open.textContent='Open workspace';section.append(heading,copy,progress,open);",
    )
    .replace(
        "const open=document.createElement('a');open.href='/library?lens=all';open.textContent='Open workspace';section.append(open);",
        "open.textContent='Open workspace';",
    )
    .replace(
        "Choose metadata providers','Mechanical Librarian gets better results when it can compare more sources. Connect as many providers as you reasonably can; you can add or change them later.",
        "Choose metadata providers','Mechanical Librarian gets better results when it can compare more sources. Connect as many providers as you reasonably can; you can add or change them later.",
    )
    .replace(
        "const links=document.createElement('nav');links.className='provider-links';links.setAttribute('aria-label','Provider account links');const lead=document.createElement('span');lead.textContent='Get provider access:';links.append(lead);for(const [label,href] of [['Open Library','https://openlibrary.org/account/create'],['Hardcover','https://hardcover.app/account/api'],['Google Books','https://console.cloud.google.com/apis/credentials']]){const link=document.createElement('a');link.href=href;link.rel='noreferrer';link.textContent=label;links.append(link)}section.append(links);const form=document.createElement('form');",
        "const form=document.createElement('form');",
    )
    .replace(
        "row.append(head);const email=adapter===",
        "row.append(head);const access=document.createElement('a');access.className='provider-access';access.href={open_library:'https://openlibrary.org/account/create',hardcover:'https://hardcover.app/account/api',google_books:'https://console.cloud.google.com/apis/credentials'}[adapter];access.rel='noreferrer';access.textContent=`Get ${label} access`;row.append(access);const email=adapter===",
    )
    .replace(
        "Select one or more book libraries.",
        "Select one or more book libraries.",
    )
    .replace(
        "open.textContent='Open workspace';",
        "open.className='primary workspace-handoff';open.textContent='Open workspace';",
    )
    .replace(
        ":preparation.first_boot_complete?(preparation.actionable?'No work became ready. Open the workspace to review the failures and recovery.':'Preparation finished without ready work. Open the workspace to continue.'):'Open the workspace to review the paused connection and use Check connection again.'",
        ":preparation.first_boot_complete?(preparation.actionable?'No work became ready. Open the workspace to review the failures and recovery.':'Preparation finished without ready work. Open the workspace to continue.'):'Open the workspace to review the paused connection and use Check connection again.'",
    )
    .replace(
        "section.append(eyebrow,heading,copy)", "section.append(heading,eyebrow,copy)"
    )
    .replace(
        "row.append(field('Local name',name));",
        "const displayName=input(`display-${item.library_id}`);displayName.value=item.name;displayName.required=true;const displayNameField=field('Library name',displayName);displayNameField.hidden=true;const changeName=button('Change name');changeName.type='button';changeName.addEventListener('click',()=>{displayNameField.hidden=false;changeName.hidden=true;displayName.focus()});row.append(changeName,displayNameField);",
    )
    .replace(
        "row.dataset.id=item.library_id;",
        "let stableName=escapeName(item.name);let suffix=2;while(usedNames.has(stableName)){const tail=`-${suffix++}`;stableName=`${escapeName(item.name).slice(0,64-tail.length)}${tail}`}usedNames.add(stableName);row.dataset.id=item.library_id;row.dataset.name=stableName;",
    )
    .replace(
        "const invalid=selected.map(row=>row.querySelector('input[type=text]')).find(control=>!control.checkValidity()||!settingsLocalLibraryNameValid(control.value));if(invalid){invalid.focus();invalid.reportValidity();error(form,'invalid_library_name');return}",
        "const invalid=selected.map(row=>row.querySelector('input[name^=display-]')).find(control=>{const value=control.value;return Array.from(value).length<1||Array.from(value).length>256||value.includes('\\0')||value.trim()!==value});if(invalid){invalid.focus();error(form,'invalid_library_display_name');return}",
    )
    .replace(
        "const libraries=selected.map(row=>({name:",
        "const libraries=selected.map(row=>({display_name:row.querySelector('input[name^=display-]').value,name:",
    )
    .replace(
        "name:row.querySelector('input[type=text]').value",
        "name:row.dataset.name",
    )
    .replace("Your work is ready", "Your work is ready")
    .replace("Preparation is complete", "Preparation is complete")
    .replace("unmatched:", "missing:")
    .replace("unobservable:", "unavailable:")
    .replace(
        "line.textContent=`${item.library}: ${mountCopy[item.state]}`",
        "const selectedLibrary=state.libraries.find(library=>library.name===item.library);const evidence=discovered.find(candidate=>candidate.library_id===selectedLibrary?.library_id);line.textContent=`${evidence?.name||humanName(item.library)}: ${mountCopy[item.state]}`",
    )
    .replace(
        "field(`${library.name} service path`,path)",
        "field(`${evidence?.name||humanName(library.name)} service path`,path)",
    )
    .replace(
        "button('Compare mounts',true)",
        "button(state.mounts.length?'Retest mounts':'Check mounts',true)",
    )
    .replace(
        "const renderMounts=()=>{const section=card(",
        "const renderMounts=async()=>{const section=card(",
    )
    .replace(
        "const form=document.createElement('form');if(state.mounts.length){",
        "const form=document.createElement('form');let discovered=[];try{discovered=(await api(`/api/setup/libraries?expected_revision=${state.revision}`)).libraries}catch{}if(state.mounts.length){",
    )
    .replace(
        "const path=input(`path-${library.name}`);path.placeholder=",
        "const path=input(`path-${library.name}`);const evidence=discovered.find(item=>item.library_id===library.library_id);path.value=evidence?.service_path||'';path.placeholder=",
    )
    .replace(
        "['mounts','Check read-only mounts'],['kick_off','Kick off preparation']",
        "['mounts','Check read-only mounts'],['reviewer','Choose reviewer'],['kick_off','Kick off preparation']",
    )
    .replace("} of 6`", "} of 7`")
    .replace(
        "const renderKickoff=()=>{",
        "const renderReviewer=async()=>{const section=card('Choose an optional cover reviewer','Reviewer observations are advice only. Continue without one, or configure exactly one engine now; you can change this later in Settings.');const form=document.createElement('form');section.append(form);let access;try{access=await api('/api/setup/reviewer-access')}catch(e){error(form,e.message);return}const current=document.createElement('p');current.textContent=`Current selection: ${access.mapping?.engine||'No reviewer'}`;form.append(current);const skip=button('Continue without a reviewer',true);skip.type='button';skip.addEventListener('click',()=>mutate('/api/setup/reviewer',{expected_revision:state.revision,outcome:'skipped'},form));form.append(skip);const codex=document.createElement('div');codex.className='option';const codexHeading=document.createElement('h3');codexHeading.textContent='Codex device sign-in';const codexStatus=document.createElement('p');codexStatus.textContent=`Status: ${access.codex.state.replaceAll('_',' ')}`;codex.append(codexHeading,codexStatus);if(access.codex.state==='usable'||access.codex.state==='configured'){const use=button('Use Codex');use.type='button';use.addEventListener('click',async()=>{try{await api('/api/setup/reviewer/selection',{method:'POST',body:JSON.stringify({expected_revision:access.revision,engine:'codex'}),mutation:true});await mutate('/api/setup/reviewer',{expected_revision:state.revision,outcome:'configured'},form)}catch(e){error(form,e.message)}});codex.append(use)}else{const start=button('Start device sign-in');start.type='button';start.disabled=access.codex.state==='host_unsupported';start.addEventListener('click',async()=>{try{const device=await api('/api/setup/reviewer/codex/device',{method:'POST',body:'{}',mutation:true});codex.replaceChildren(codexHeading);const prompt=document.createElement('p');prompt.setAttribute('role','status');prompt.textContent=device.verification_url&&device.user_code?`Open ${device.verification_url} and enter ${device.user_code}.`: `Device sign-in: ${device.state.replaceAll('_',' ')}`;codex.append(prompt);if(device.verification_url){const link=document.createElement('a');link.href=device.verification_url;link.rel='noreferrer';link.textContent='Open device sign-in';codex.append(link)}}catch(e){error(form,e.message)}});codex.append(start)}form.append(codex);const router=document.createElement('div');router.className='option';const routerHeading=document.createElement('h3');routerHeading.textContent='OpenRouter';const caveat=document.createElement('p');caveat.textContent=access.data_use_caveat;const recommendation=document.createElement('p');recommendation.textContent=access.recommendations.length?'Qualified models: '+access.recommendations.join(', '):'No model recommendation is qualified by delivered evidence yet.';const model=input('reviewer-model');model.placeholder=access.mapping?.engine==='openrouter'?access.mapping.model:'provider/model';model.maxLength=256;const key=input('reviewer-key','password');key.autocomplete='new-password';key.maxLength=4096;const save=button('Validate and use OpenRouter');save.type='button';save.addEventListener('click',async()=>{try{await api('/api/setup/reviewer/openrouter',{method:'POST',body:JSON.stringify({expected_revision:access.revision,api_key:key.value,model:model.value}),mutation:true});key.value='';await mutate('/api/setup/reviewer',{expected_revision:state.revision,outcome:'configured'},form)}catch(e){key.value='';error(form,e.message)}});router.append(routerHeading,caveat,recommendation,field('Model ID',model),field('API key (write-only)',key),save);form.append(router)};const renderKickoff=()=>{",
    )
    .replace(
        "mounts:renderMounts,kick_off:renderKickoff",
        "mounts:renderMounts,reviewer:renderReviewer,kick_off:renderKickoff",
    )
)

__all__ = ["SETUP_CSS", "SETUP_HTML", "SETUP_JS"]
