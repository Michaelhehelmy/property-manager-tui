(function(){
  const STORAGE_KEY = 'pm_assistant_widget_v1';
  function $(sel, root){ return (root||document).querySelector(sel); }
  function $all(sel, root){ return Array.prototype.slice.call((root||document).querySelectorAll(sel)); }
  function getCsrf(){ var el = document.getElementById('csrf-global'); return el ? el.value : ''; }
  function loadState(){
    try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}') || {}; } catch(e){ return {}; }
  }
  function saveState(s){ try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s||{})); } catch(e){} }
  function state(){ var s=loadState(); if(!Array.isArray(s.messages)) s.messages=[]; if(typeof s.open!=='boolean') s.open=false; return s; }

  function currentUiContext(){
    var path = location.pathname || '/';
    var params = new URLSearchParams(location.search || '');
    var tab = params.get('tab') || '';
    var title = document.title || '';
    // collect visible form fields metadata (lightweight)
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
    // dedupe-ish and cap
    var seen={}; var out=[];
    fields.forEach(function(f){
      var k=JSON.stringify(f);
      if(seen[k]) return; seen[k]=1; out.push(f);
    });
    out = out.slice(0,60);
    // recent actions stored separately
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

  function render(){
    var root = document.getElementById('pm-assistant-widget');
    if(!root) return;
    var s = state();
    root.classList.toggle('open', !!s.open);
    var list = $('#pm-aw-messages', root);
    if(!list) return;
    if(!s.messages.length){
      list.innerHTML = '<div class="pm-aw-empty">Ask me anything. I can answer from real app data, explain fields, and take you to the right page/tab.</div>';
    } else {
      list.innerHTML = s.messages.map(function(m){
        var cls = m.role==='user' ? 'user' : 'assistant';
        var meta = m.meta ? '<div class="pm-aw-meta">'+esc(m.meta)+'</div>' : '';
        return '<div class="pm-aw-msg '+cls+'"><div class="pm-aw-bubble">'+meta+'<div class="pm-aw-text">'+nl2brSafe(m.content||'')+'</div></div></div>';
      }).join('');
    }
    list.scrollTop = list.scrollHeight;
    var ctx = currentUiContext();
    var badge = $('#pm-aw-context', root);
    if(badge){ badge.textContent = (ctx.path + (ctx.tab?('?tab='+ctx.tab):'')); }
  }

  function executeActions(actions){
    if(!Array.isArray(actions)) return;
    actions.forEach(function(a){
      if(!a || !a.type) return;
      if(a.type==='navigate' && a.url){
        trackAction({type:'assistant_navigate', url:a.url});
        // preserve widget open state
        var s=state(); s.open=true; saveState(s);
        window.location.href = a.url;
      } else if(a.type==='focus' && a.selector){
        try{ var el=document.querySelector(a.selector); if(el){ el.focus(); el.scrollIntoView({block:'center',behavior:'smooth'}); } }catch(e){}
      } else if(a.type==='highlight' && a.selector){
        try{ var el=document.querySelector(a.selector); if(el){ el.classList.add('pm-aw-highlight'); setTimeout(function(){ el.classList.remove('pm-aw-highlight'); }, 4000); el.scrollIntoView({block:'center',behavior:'smooth'}); } }catch(e){}
      }
    });
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
      else {
        // fallback: parse not expected; reload messages area via page not needed
        data = {ok:false, answer:'Assistant returned an unexpected response.'};
      }
      if(!data || data.ok===false && !data.answer){
        s = state(); s.messages.push({role:'assistant', content:(data && (data.error||data.answer)) || 'Sorry, I could not complete that request.', meta:'error'}); saveState(s);
      } else {
        var meta = 'source='+(data.source||'?')+' intent='+(data.intent||'?')+' conf='+(data.confidence!=null?data.confidence:'?');
        s = state(); s.messages.push({role:'assistant', content:data.answer||'', meta: meta, intent:data.intent, tools_used:data.tools_used||[], ui_actions:data.ui_actions||[]});
        s.messages = s.messages.slice(-40); saveState(s);
        render();
        executeActions(data.ui_actions||[]);
      }
    }catch(e){
      s = state(); s.messages.push({role:'assistant', content:'Network/error while contacting assistant.', meta:'error'}); saveState(s);
    } finally {
      if(status) status.textContent='';
      render();
    }
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
    $('#pm-aw-clear', root).addEventListener('click', function(){ var s=state(); s.messages=[]; saveState(s); render(); trackAction({type:'clear_chat'}); });
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
