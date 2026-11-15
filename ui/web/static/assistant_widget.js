(function(){
  const STORAGE_KEY = 'pm_assistant_widget_v1';
  function $(sel, root){ return (root||document).querySelector(sel); }
  function $all(sel, root){ return Array.prototype.slice.call((root||document).querySelectorAll(sel)); }
  function getCsrf(){ var el = document.getElementById('csrf-global'); return el ? el.value : ''; }
  function loadState(){
    try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}') || {}; } catch(e){ return {}; }
  }
  function saveState(s){ try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s||{})); } catch(e){} }
  function state(){
    var s=loadState();
    if(!Array.isArray(s.messages)) s.messages=[];
    if(typeof s.open!=='boolean') s.open=false;
    if(!('pendingAction' in s)) s.pendingAction=null;
    return s;
  }

  function currentUiContext(){
    var path = location.pathname || '/';
    var params = new URLSearchParams(location.search || '');
    var tab = params.get('tab') || '';
    var title = document.title || '';
    var fields = [];
    $all('form').slice(0,6).forEach(function(form){
      $all('label, input, select, textarea', form).slice(0,40).forEach(function(el){
        var tag = (el.tagName||'').toLowerCase();
        if(tag==='label'){
          var txt=(el.innerText||el.textContent||'').trim().replace(/\s+/g,' ').slice(0,120);
          if(txt) fields.push({kind:'label', text: txt});
        } else {
          var name=el.getAttribute('name')||'';
          if(!name || name==='csrf') return;
          var type=el.getAttribute('type')||tag;
          var ph=el.getAttribute('placeholder')||'';
          var labelTxt='';
          try{ var lab = el.closest('label'); if(lab) labelTxt=(lab.innerText||lab.textContent||'').trim().replace(/\s+/g,' ').slice(0,120); }catch(e){}
          fields.push({kind:'field', name:name, type:type, placeholder:ph.slice(0,80), label:labelTxt});
        }
      });
    });
    var seen={}; var out=[];
    fields.forEach(function(f){
      var k=JSON.stringify(f);
      if(seen[k]) return; seen[k]=1; out.push(f);
    });
    out = out.slice(0,60);
    var acts = [];
    try { acts = JSON.parse(sessionStorage.getItem('pm_ui_actions_v1')||'[]')||[]; } catch(e){ acts=[]; }
    return { path:path, tab:tab, title:title, url: location.pathname + (location.search||''), fields: out, recent_actions: acts.slice(-10) };
  }

  function trackAction(a){
    try{
      var arr = JSON.parse(sessionStorage.getItem('pm_ui_actions_v1')||'[]')||[];
      arr.push(Object.assign({at: Date.now(), path: location.pathname, tab: (new URLSearchParams(location.search||'')).get('tab')||''}, a||{}));
      if(arr.length>20) arr = arr.slice(-20);
      sessionStorage.setItem('pm_ui_actions_v1', JSON.stringify(arr));
    }catch(e){}
  }

  function esc(s){ return (s==null?'':String(s)).replace(/[&<>"']/g,function(c){return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];}); }
  function nl2brSafe(s){ return esc(s).replace(/\n/g,'<br>'); }
  function jsonPre(obj){
    try{ return esc(JSON.stringify(obj, null, 2)); }catch(e){ return esc(String(obj)); }
  }

  function pendingActionHtml(pa){
    if(!pa || !pa.id) return '';
    var summary = esc(pa.summary || pa.title || pa.type || 'Proposed action');
    var desc = pa.description ? '<div class="pm-aw-meta" style="margin-top:4px;">'+esc(pa.description)+'</div>' : '';
    var warnings = '';
    if(pa.warnings){
      var ws = Array.isArray(pa.warnings) ? pa.warnings : [pa.warnings];
      if(ws.length){
        warnings = '<div style="margin-top:6px;color:#b45309;font-size:12px;"><strong>Warnings:</strong><ul style="margin:4px 0 0 16px;padding:0;">'+ws.map(function(w){return '<li>'+esc(w)+'</li>';}).join('')+'</ul></div>';
      }
    }
    var details = '';
    if(pa.resolved){ details += '<details style="margin-top:6px;"><summary>Resolved</summary><pre style="white-space:pre-wrap;font-size:11px;margin:4px 0 0;">'+jsonPre(pa.resolved)+'</pre></details>'; }
    if(pa.prechecks){ details += '<details style="margin-top:6px;"><summary>Prechecks</summary><pre style="white-space:pre-wrap;font-size:11px;margin:4px 0 0;">'+jsonPre(pa.prechecks)+'</pre></details>'; }
    if(pa.preview){ details += '<details style="margin-top:6px;"><summary>Preview</summary><pre style="white-space:pre-wrap;font-size:11px;margin:4px 0 0;">'+jsonPre(pa.preview)+'</pre></details>'; }
    var expiry = pa.expires_at ? '<div class="pm-aw-meta" style="margin-top:6px;">Expires: '+esc(pa.expires_at)+'</div>' : '';
    var pw = pa.requires_password ? '<div style="margin-top:6px;"><input type="password" class="pm-aw-pw" placeholder="Password/Proof (if required)" style="width:100%;box-sizing:border-box;"/></div>' : '';
    return ''
      + '<div class="pm-aw-pending" data-action-id="'+esc(pa.id)+'" style="margin:8px 0;padding:8px;border:1px solid #d9d9d9;border-radius:8px;background:#fff;">'
      +   '<div style="font-weight:600;">Proposed action</div>'
      +   '<div style="margin-top:4px;">'+summary+(pa.type?' <span class="pm-aw-meta">('+esc(pa.type)+')</span>':'')+'</div>'
      +   desc + warnings + details + expiry + pw
      +   '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;">'
      +     '<button type="button" class="pm-aw-btn pm-aw-confirm" data-action-id="'+esc(pa.id)+'">Confirm</button>'
      +     '<button type="button" class="pm-aw-btn pm-aw-cancel" data-action-id="'+esc(pa.id)+'">Cancel</button>'
      +   '</div>'
      +   '<div class="pm-aw-meta" style="margin-top:6px;">No mutation runs until you confirm.</div>'
      + '</div>';
  }

  function render(){
    var root = document.getElementById('pm-assistant-widget');
    if(!root) return;
    var s = state();
    root.classList.toggle('open', !!s.open);
    var list = $('#pm-aw-messages', root);
    if(!list) return;
    var msgsHtml = '';
    if(!s.messages.length){
      msgsHtml = '<div class="pm-aw-empty">Ask me anything. I can answer from real app data, explain fields, and take you to the right page/tab.</div>';
    } else {
      msgsHtml = s.messages.map(function(m){
        var cls = m.role==='user' ? 'user' : 'assistant';
        var meta = m.meta ? '<div class="pm-aw-meta">'+esc(m.meta)+'</div>' : '';
        var pendingInline = '';
        if(m.role==='assistant' && m.proposed_action && s.pendingAction && m.proposed_action.id === s.pendingAction.id){
          pendingInline = pendingActionHtml(s.pendingAction);
        }
        return '<div class="pm-aw-msg '+cls+'"><div class="pm-aw-bubble">'+meta+'<div class="pm-aw-text">'+nl2brSafe(m.content||'')+'</div>'+pendingInline+'</div></div>';
      }).join('');
    }
    // If pending exists but wasn't attached to a message, show at bottom
    if(s.pendingAction && (!s.messages.length || !s.messages.some(function(m){ return m && m.proposed_action && m.proposed_action.id===s.pendingAction.id; }))){
      msgsHtml += '<div class="pm-aw-msg assistant"><div class="pm-aw-bubble">'+pendingActionHtml(s.pendingAction)+'</div></div>';
    }
    list.innerHTML = msgsHtml;
    list.scrollTop = list.scrollHeight;
    var ctx = currentUiContext();
    var badge = $('#pm-aw-context', root);
    if(badge){ badge.textContent = (ctx.path + (ctx.tab?('?tab='+ctx.tab):'')); }

    $all('.pm-aw-confirm', root).forEach(function(btn){
      btn.onclick = function(){ confirmPending(btn.getAttribute('data-action-id'), btn); };
    });
    $all('.pm-aw-cancel', root).forEach(function(btn){
      btn.onclick = function(){ cancelPending(btn.getAttribute('data-action-id')); };
    });
  }

  function executeActions(actions){
    if(!Array.isArray(actions)) return;
    actions.forEach(function(a){
      if(!a || !a.type) return;
      if(a.type==='navigate' && a.url){
        trackAction({type:'assistant_navigate', url:a.url});
        var s=state(); s.open=true; saveState(s);
        window.location.href = a.url;
      } else if(a.type==='focus' && a.selector){
        try{ var el=document.querySelector(a.selector); if(el){ el.focus(); el.scrollIntoView({block:'center',behavior:'smooth'}); } }catch(e){}
      } else if(a.type==='highlight' && a.selector){
        try{ var el=document.querySelector(a.selector); if(el){ el.classList.add('pm-aw-highlight'); setTimeout(function(){ el.classList.remove('pm-aw-highlight'); }, 4000); el.scrollIntoView({block:'center',behavior:'smooth'}); } }catch(e){}
      }
    });
  }

  function applyResponseToState(data, fallbackErr){
    var s = state();
    if(!data){
      s.messages.push({role:'assistant', content:fallbackErr || 'Sorry, I could not complete that request.', meta:'error'});
      saveState(s); return s;
    }
    if(data.ok===false && !data.answer){
      s.messages.push({role:'assistant', content:(data.error||data.answer||fallbackErr||'Sorry, I could not complete that request.'), meta:'error'});
      if('pending_action' in data) s.pendingAction = data.pending_action || null;
      else if('proposed_action' in data) s.pendingAction = data.proposed_action || null;
      saveState(s); return s;
    }
    var pa = data.proposed_action || data.pending_action || null;
    var meta = 'source='+(data.source||'?')+' intent='+(data.intent||'?')+' conf='+(data.confidence!=null?data.confidence:'?');
    if(data.intent==='action_confirm' || data.intent==='action_cancel'){ meta = data.intent; }
    s.messages.push({role:'assistant', content:data.answer||'', meta: meta, intent:data.intent, tools_used:data.tools_used||[], ui_actions:data.ui_actions||[], proposed_action: pa || undefined});
    s.pendingAction = pa || null;
    s.messages = s.messages.slice(-40);
    saveState(s);
    return s;
  }

  async function sendMessage(text){
    text = (text||'').trim(); if(!text) return;
    var s = state();
    s.messages.push({role:'user', content:text}); saveState(s); render();
    var root = document.getElementById('pm-assistant-widget');
    var status = $('#pm-aw-status', root); if(status) status.textContent='Thinking…';
    try{
      var fd = new FormData();
      fd.append('csrf', getCsrf());
      fd.append('message', text);
      fd.append('ui_context', JSON.stringify(currentUiContext()));
      fd.append('widget', '1');
      var res = await fetch('/assistant/ask', {method:'POST', body: fd, headers: {'X-Requested-With':'assistant-widget','Accept':'application/json'}});
      var data = null;
      var ct = res.headers.get('content-type')||'';
      if(ct.indexOf('application/json')>=0){ data = await res.json(); }
      else { data = {ok:false, answer:'Assistant returned an unexpected response.'}; }
      applyResponseToState(data, 'Assistant returned an unexpected response.');
      render();
      if(data) executeActions(data.ui_actions||[]);
    }catch(e){
      var st = state(); st.messages.push({role:'assistant', content:'Network/error while contacting assistant.', meta:'error'}); saveState(st);
    } finally {
      if(status) status.textContent='';
      render();
    }
  }

  async function confirmPending(actionId, btn){
    var root = document.getElementById('pm-assistant-widget');
    var status = $('#pm-aw-status', root); if(status) status.textContent='Confirming action…';
    try{
      var card = btn ? btn.closest('.pm-aw-pending') : null;
      var pwEl = card ? card.querySelector('.pm-aw-pw') : null;
      var fd = new FormData();
      fd.append('csrf', getCsrf());
      fd.append('action_id', actionId || '');
      fd.append('widget', '1');
      if(pwEl && pwEl.value) fd.append('password', pwEl.value);
      var res = await fetch('/assistant/action/confirm', {method:'POST', body: fd, headers: {'X-Requested-With':'assistant-widget','Accept':'application/json'}});
      var data = null; var ct = res.headers.get('content-type')||'';
      if(ct.indexOf('application/json')>=0){ data = await res.json(); } else { data = {ok:false, answer:'Unexpected response while confirming action.'}; }
      applyResponseToState(data, 'Failed to confirm action.');
      render();
      if(data) executeActions(data.ui_actions||[]);
    }catch(e){
      var st=state(); st.messages.push({role:'assistant', content:'Network/error while confirming action.', meta:'error'}); saveState(st); render();
    } finally { if(status) status.textContent=''; }
  }

  async function cancelPending(actionId){
    var root = document.getElementById('pm-assistant-widget');
    var status = $('#pm-aw-status', root); if(status) status.textContent='Cancelling…';
    try{
      var fd = new FormData();
      fd.append('csrf', getCsrf());
      fd.append('action_id', actionId || '');
      fd.append('widget', '1');
      var res = await fetch('/assistant/action/cancel', {method:'POST', body: fd, headers: {'X-Requested-With':'assistant-widget','Accept':'application/json'}});
      var data = null; var ct = res.headers.get('content-type')||'';
      if(ct.indexOf('application/json')>=0){ data = await res.json(); } else { data = {ok:false, answer:'Unexpected response while cancelling action.'}; }
      applyResponseToState(data, 'Failed to cancel action.');
      render();
      if(data) executeActions(data.ui_actions||[]);
    }catch(e){
      var st=state(); st.messages.push({role:'assistant', content:'Network/error while cancelling action.', meta:'error'}); saveState(st); render();
    } finally { if(status) status.textContent=''; }
  }

  function init(){
    if(document.getElementById('pm-assistant-widget')) return;
    var root = document.createElement('div');
    root.id = 'pm-assistant-widget';
    root.className = 'pm-aw';
    root.innerHTML = ''
      + '<button type="button" class="pm-aw-fab" id="pm-aw-fab" title="Open assistant">💬 Assistant</button>'
      + '<div class="pm-aw-panel" role="dialog" aria-label="Assistant">'
      + '  <div class="pm-aw-header"><div><strong>Assistant</strong><div class="pm-aw-ctx" id="pm-aw-context"></div></div>'
      + '    <div class="pm-aw-actions"><button type="button" class="pm-aw-btn" id="pm-aw-clear" title="Clear chat">Clear</button><button type="button" class="pm-aw-btn" id="pm-aw-close" title="Close">✕</button></div>'
      + '  </div>'
      + '  <div class="pm-aw-messages" id="pm-aw-messages"></div>'
      + '  <div class="pm-aw-status" id="pm-aw-status"></div>'
      + '  <form class="pm-aw-form" id="pm-aw-form"><textarea id="pm-aw-input" rows="2" placeholder="Ask anything… I can guide you, explain fields, or open the right page/tab." ></textarea><button type="submit">Send</button></form>'
      + '</div>';
    document.body.appendChild(root);

    $('#pm-aw-fab', root).addEventListener('click', function(){ var s=state(); s.open=!s.open; saveState(s); render(); if(s.open){ var i=$('#pm-aw-input',root); if(i) i.focus(); } });
    $('#pm-aw-close', root).addEventListener('click', function(){ var s=state(); s.open=false; saveState(s); render(); });
    $('#pm-aw-clear', root).addEventListener('click', function(){ var s=state(); s.messages=[]; s.pendingAction=null; saveState(s); render(); trackAction({type:'clear_chat'}); });
    $('#pm-aw-form', root).addEventListener('submit', function(e){ e.preventDefault(); var i=$('#pm-aw-input',root); var v=i?i.value:''; if(i) i.value=''; sendMessage(v); });
    $('#pm-aw-input', root).addEventListener('keydown', function(e){ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); $('#pm-aw-form',root).dispatchEvent(new Event('submit', {cancelable:true,bubbles:true})); } });

    document.addEventListener('click', function(e){
      var t=e.target; if(!t) return;
      var a = t.closest ? t.closest('a,button,[role="tab"]') : null;
      if(a){
        var txt=(a.innerText||a.textContent||'').trim().replace(/\s+/g,' ').slice(0,80);
        trackAction({type:'click', text: txt, href: a.getAttribute && a.getAttribute('href') || ''});
      }
    }, true);
    document.addEventListener('submit', function(e){
      var f=e.target; if(!f) return;
      var act=(f.getAttribute && f.getAttribute('action')) || '';
      trackAction({type:'submit', action: act});
    }, true);

    render();
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
