"""The setup walkthrough's reviewer step: Codex sign-in and OpenRouter.

`setup_frontend.SETUP_JS` concatenates this source between its shared helpers
(`api`, `card`, `error`, `mutate`, `load`, `render`) and the steps after it, so
it runs in that one script's scope and defines `renderReviewer` for `render`.
"""

from __future__ import annotations

SETUP_REVIEWER_JS = r"""// The model a check on this page proved usable. A later read of reviewer access
// says only that the key is configured, so the check's own answer is kept.
let openrouterChecked=null;
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
    announce('Cancelling sign-in…');
    try{
      codexTerminal=await api('/api/setup/reviewer/codex/device/cancel',{method:'POST',body:'{}',mutation:true});
      // Say the redrawn line, which stays true if the sign-in finished first,
      // and return focus to that card's command rather than the step heading.
      await render();
      const status=main.querySelector('[data-codex-status]');
      announce(status?.textContent||'');
      const next=status?.closest('.reviewer-option')?.querySelector('button:not(:disabled)');
      if(next)requestAnimationFrame(()=>{if(next.isConnected)next.focus()})
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
        prompt.replaceChildren(lead,row);
        // Focus sits on Cancel after a press, so the code is said there too.
        if(focus)announce(`${lead.textContent} ${device.user_code}`)
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
  const hostBlocked=['host_unsupported','not_provisioned'].includes(access.codex.state);
  // A sandbox the host refused blocks sign-in like a host reason (#2011).
  const blocked=hostBlocked||codexTerminal?.failure==='sandbox_unavailable';
  const codexState=hostBlocked?access.codex:(codexTerminal||access.codex);
  const signedIn=['usable','configured'].includes(codexState.state);
  const line=document.createElement('p');
  line.setAttribute('aria-live','polite');
  line.dataset.codexStatus='';
  line.textContent=signedIn?'Signed in':'Not signed in';
  const allowance=document.createElement('p');
  allowance.textContent='Uses your ChatGPT plan.';
  codex.append(line,allowance);
  // Settings' words for each host reason (#1607).
  const reasons={profile_not_provisioned:'The Codex profile is missing. Restore the image’s profile or correct the profile path, then restart the service.',profile_unreadable:'The Codex profile cannot be read. Fix the file’s permissions or contents, then restart the service.',profile_contract_mismatch:'The Codex profile does not match the model and limits this version needs. Restore the image’s profile or regenerate it, then restart the service.',host_evidence_unavailable:'Mechanical Librarian cannot read the host’s free memory. Fix the host or container memory reporting before using Codex.',cgroup_limit_below_1_gib:'The container memory limit is below 1 GB. Increase it before using Codex.',headroom_below_1_gib:'Less than 1 GB of memory is free. Free some memory or raise the container limit.',sandbox_unavailable:'Codex could not start its sandbox on this host. The container log says why.'};
  if(blocked){
    const remedy=document.createElement('p');
    remedy.textContent=reasons[hostBlocked?access.codex.reason:'sandbox_unavailable']||recoveryCopy(access.codex.state);
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
        announce('Selecting Codex…');
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
      announce('Starting sign-in…');
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
          failed(e,null,signIn,true)
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
    const perThousand=measured?Number(measured.per_1000_usd):0;
    // Rounded as Settings → Reviewer rounds it (ReviewerCost.tsx, #1863).
    cost.textContent=!model.value?'':measured?`${label}: ${perThousand<0.01?'under $0.01':`about $${perThousand.toFixed(2)}`} per 1,000 works.`:`${label}: cost not measured.`;
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
  getKeyLink.target='_blank';
  getKeyLink.rel='noopener noreferrer';
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
      const checked=await api('/api/setup/reviewer/openrouter',{method:'POST',body:JSON.stringify({expected_revision:access.revision,api_key:key.value,model:model.value}),mutation:true});
      openrouterChecked=checked?.openrouter?.state==='usable'?checked.mapping?.model??null:null;
      key.value='';
      await render();
      // The folded card says the chosen model; Continue is next.
      const chosen=main.querySelector('[data-openrouter-selected]');
      const next=main.querySelector('.actions .primary');
      announce(chosen?.textContent||'');
      requestAnimationFrame(()=>{
        chosen?.scrollIntoView?.({block:'nearest'});
        next?.scrollIntoView?.({block:'nearest'});
        if(next?.isConnected)next.focus()
      })
    }catch(e){
      openrouterChecked=null;
      // What was typed stays for the next press, as in Settings.
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
  // Once OpenRouter is chosen the card says so under its heading, and the
  // form waits behind one toggle.
  const change=document.createElement('div');
  change.id='setup-openrouter-change';
  change.append(cost,toggle,others,keyField,getKey,save);
  if(mapped){
    const selected=document.createElement('p');
    selected.dataset.openrouterSelected='';
    selected.textContent=`Selected: ${reviewerLabel(mapped)}`;
    router.append(selected);
    if(access.openrouter?.state==='usable'||openrouterChecked===mapped){
      const ready=document.createElement('p');
      ready.textContent='Status: ready to use';
      router.append(ready)
    }
    const fold=button('Change model or key');
    fold.type='button';
    fold.setAttribute('aria-expanded','false');
    fold.setAttribute('aria-controls',change.id);
    change.hidden=true;
    fold.addEventListener('click',()=>{
      const expanded=fold.getAttribute('aria-expanded')!=='true';
      fold.setAttribute('aria-expanded',String(expanded));
      change.hidden=!expanded
    });
    router.append(fold)
  }
  router.append(change,about);
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
  // `automatic` marks the check no one pressed.
  const failed=async(e,control,submitted,automatic=false)=>{
    // A press that got no answer may still have been saved (Settings' words).
    const code=submitted&&!automatic&&e.status===undefined?'no_answer':refusal(e);
    // Another tab rotated this form's token; read the state again before saying so.
    if(e.status===403&&e.message==='csrf'){
      const typed=[...form.querySelectorAll('input[name^="reviewer-"]')].map(field=>[field.name,field.value]);
      const label=submitted?.textContent;
      await load();
      const fresh=main.querySelector('form');
      if(!fresh)return;
      // Still on this step: the fresh button asks for the press again, with
      // what was typed kept. Otherwise the step changed, said at the top.
      const again=!automatic&&state?.next_step==='reviewer'?[...fresh.querySelectorAll('button')].find(action=>action.textContent===label):null;
      // A chosen reviewer's form starts folded; open it to the pressed button.
      const folded=again?.closest('[hidden]');
      if(folded)fresh.querySelector(`[aria-controls="${folded.id}"]`)?.click();
      if(again)for(const [name,value] of typed){
        const field=fresh.querySelector(`input[name="${name}"]`);
        if(!field||field.value===value)continue;
        field.value=value;
        field.dispatchEvent(new Event('input'));
        const others=field.closest('[hidden]');
        if(others)fresh.querySelector(`[aria-controls="${others.id}"]`)?.click()
      }
      const node=error(fresh,again?'csrf_renewed':code);
      if(again)again.after(node);
      // After the redrawn card's own focus and scroll.
      requestAnimationFrame(()=>{
        if(again?.isConnected){
          again.focus();
          again.scrollIntoView?.({block:'nearest'})
        }
        node.scrollIntoView?.({block:'nearest'})
      });
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
"""

__all__ = ["SETUP_REVIEWER_JS"]
