"""Pagina della GUI: HTML, CSS e JS in un unico file, senza asset esterni.

Nessun font remoto, nessuna CDN, nessun framework. Oltre a essere coerente con la
promessa local-first, rende la pagina ispezionabile per intero: chi vuole
verificare che i dati non escano dalla macchina legge un file solo.

Nota implementativa: la sostituzione usa sentinelle esplicite e ``str.replace``,
non ``string.Template``. Il template di libreria interpreta ogni ``$`` come un
segnaposto, e in una pagina che contiene template literal JavaScript e la
scorciatoia ``$(id)`` questo obbliga a raddoppiare i dollari ovunque — una fonte
di errori silenziosi che si manifestano solo a runtime.
"""

from __future__ import annotations

import html

CSRF_SENTINEL = "__ANTON_CSRF__"
VERSION_SENTINEL = "__ANTON_VERSION__"

_PAGE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>anton-ocr</title>
<style>
*{box-sizing:border-box;margin:0}
:root{
  --bg:#09090b;--surface:#18181b;--surface-2:#27272a;--border:#3f3f46;
  --fg:#fafafa;--muted:#a1a1aa;--accent:#3b82f6;--accent-hover:#60a5fa;
  --green:#22c55e;--yellow:#eab308;--red:#ef4444;--purple:#a78bfa;
  --radius:10px;--font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media(prefers-color-scheme:light){
  :root{
    --bg:#fafafa;--surface:#fff;--surface-2:#f4f4f5;--border:#e4e4e7;
    --fg:#09090b;--muted:#71717a;--purple:#7c3aed;
  }
}
body{
  background:var(--bg);color:var(--fg);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
}
a{color:var(--accent)}

/* ── layout ── */
.wrap{max-width:720px;margin:0 auto;padding:24px 20px 64px}
header{
  display:flex;align-items:center;gap:10px;padding:16px 0 20px;
  border-bottom:1px solid var(--border);margin-bottom:24px;flex-wrap:wrap;
}
.logo{font-size:18px;font-weight:700;letter-spacing:-.03em}
.logo span{color:var(--accent)}
.ver{color:var(--muted);font-size:12px}
.spacer{flex:1}
.badge{
  font-size:11px;padding:3px 10px;border-radius:99px;
  background:var(--surface-2);color:var(--muted);border:1px solid var(--border);
}
.badge.ok{color:var(--green)}.badge.warn{color:var(--yellow)}.badge.bad{color:var(--red)}

/* ── sezioni ── */
section{margin-bottom:28px}
section h2{
  font-size:13px;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px;
}

/* ── form elements ── */
textarea,input[type=text]{
  width:100%;padding:10px 14px;border-radius:var(--radius);
  border:1px solid var(--border);background:var(--surface);color:var(--fg);
  font:13px/1.6 var(--font-mono);resize:vertical;transition:border-color .15s;
}
textarea:focus,input[type=text]:focus{outline:none;border-color:var(--accent)}
textarea{min-height:180px}
input[type=text]{font-size:14px}

.drop{
  border:2px dashed var(--border);border-radius:var(--radius);
  padding:28px 16px;text-align:center;cursor:pointer;transition:.15s;
  background:var(--surface);margin-bottom:10px;
}
.drop:hover,.drop.over{border-color:var(--accent);background:var(--surface-2)}
.drop b{display:block;margin-bottom:4px}
.drop small{color:var(--muted);font-size:12px}

.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}

button{
  padding:8px 18px;border-radius:var(--radius);font-size:13px;font-weight:500;
  cursor:pointer;border:1px solid var(--border);background:var(--surface-2);
  color:var(--fg);transition:.15s;
}
button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
button:disabled{opacity:.4;cursor:not-allowed}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover:not(:disabled){background:var(--accent-hover);border-color:var(--accent-hover)}
button.sm{padding:5px 12px;font-size:12px}
button.danger{color:var(--red)}
button.danger:hover:not(:disabled){border-color:var(--red)}

/* ── output ── */
pre{
  padding:14px;background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);overflow:auto;max-height:400px;
  font:12.5px/1.65 var(--font-mono);white-space:pre-wrap;word-break:break-word;
}
.tok{color:var(--purple);font-weight:600}

.alert{
  padding:12px 16px;border-radius:var(--radius);font-size:13px;
  margin-bottom:12px;border:1px solid;
}
.alert.ok{background:#22c55e12;border-color:#22c55e40;color:var(--green)}
.alert.warn{background:#eab30812;border-color:#eab30840;color:var(--yellow)}
.alert.bad{background:#ef444412;border-color:#ef444440;color:var(--red)}
.alert b{display:block;margin-bottom:2px;color:var(--fg)}

.meta{
  display:grid;grid-template-columns:auto 1fr;gap:3px 16px;
  font-size:13px;margin-bottom:14px;
}
.meta dt{color:var(--muted)}.meta dd{font-family:var(--font-mono);word-break:break-all}

table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-size:11px;font-weight:600;text-transform:uppercase}
td.r{text-align:right;font-variant-numeric:tabular-nums}
.tag{
  font-size:10px;padding:2px 8px;border-radius:99px;
  background:var(--surface-2);border:1px solid var(--border);
}
.tag.checksum{color:var(--green)}.tag.ocr_recovery{color:var(--yellow)}.tag.ner{color:var(--accent)}

.info{color:var(--muted);font-size:12px;margin-top:8px}
.fname{color:var(--muted);font-size:12px;margin:6px 0 0}
.hide{display:none !important}
.sep{border:none;border-top:1px solid var(--border);margin:20px 0}
footer{color:var(--muted);font-size:11px;margin-top:32px;padding-top:16px;border-top:1px solid var(--border)}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="logo">anton<span>-ocr</span></div>
  <div class="ver">__ANTON_VERSION__</div>
  <div class="spacer"></div>
  <span class="badge ok" title="Solo loopback">locale</span>
  <span class="badge" id="pill-policy"></span>
  <span class="badge" id="pill-ner"></span>
</header>

<!-- ── 1. INPUT ── -->
<section>
  <h2>Documento</h2>
  <div class="drop" id="drop">
    <b>Trascina un file</b>
    <small>.pdf .txt .md — oppure incolla il testo sotto</small>
  </div>
  <input type="file" id="file" class="hide" accept=".txt,.md,.pdf">
  <div class="fname" id="fname"></div>
  <textarea id="input" placeholder="Incolla qui il testo del documento…"></textarea>
  <div class="actions">
    <button class="primary" id="btn-process">Elabora</button>
    <button id="btn-scan">Anteprima</button>
    <button id="btn-clear">Pulisci</button>
  </div>
</section>

<!-- ── 2. RILEVAMENTO ── -->
<section id="sec-detect" class="hide">
  <h2>Rilevamento</h2>
  <div id="detect-summary" class="info" style="margin:0 0 10px"></div>
  <table><thead>
    <tr><th>Tipo</th><th>Valore</th><th>Strato</th><th class="r">Conf</th></tr>
  </thead><tbody id="detect-rows"></tbody></table>
</section>

<!-- ── 3. OUTPUT ── -->
<section id="sec-result" class="hide">
  <h2>Documento sicuro</h2>
  <div id="verdict"></div>
  <dl class="meta" id="meta"></dl>
  <pre id="safe"></pre>
  <div class="actions" style="margin-top:10px">
    <button class="primary sm" id="btn-copy">Copia</button>
    <button class="sm" id="btn-download">Scarica .safe.md</button>
    <button class="sm" id="btn-download-manifest">Scarica manifest</button>
    <span class="info" id="saved"></span>
  </div>
  <p class="info">
    Il documento non viene salvato su disco. Sono registrati solo l'audit log e il vault.
  </p>
</section>

<hr class="sep">

<!-- ── 4. REHYDRATE ── -->
<section>
  <h2>Ripristina valori reali</h2>
  <p class="info" style="margin-bottom:10px">
    Incolla la risposta dell'agente: i segnaposto vengono risolti leggendo il vault cifrato in locale.
  </p>
  <div class="actions" style="margin:0 0 10px">
    <input type="text" id="uuid" placeholder="UUID documento" style="flex:1">
    <button class="sm danger" id="btn-forget" title="Distrugge la chiave — irreversibile">Dimentica</button>
  </div>
  <textarea id="response" placeholder="Risposta dell'agente con i segnaposto ⟦…⟧" style="min-height:100px"></textarea>
  <div class="actions"><button class="primary" id="btn-rehydrate">Ripristina</button></div>
  <div id="sec-rehydrated" class="hide" style="margin-top:14px">
    <div id="rehydrate-warn"></div>
    <pre id="rehydrated-text"></pre>
  </div>
</section>

<footer>
  Elaborazione interamente locale — nessun dato lascia questa macchina.<br>
  anton-ocr produce artefatti di evidenza tecnica, non è consulenza legale.
</footer>

</div>

<script>
const CSRF="__ANTON_CSRF__";
const $=id=>document.getElementById(id);
let lastUuid=null,lastMd=null,lastManifest=null,lastName="documento",pendingPdf=null;

const esc=s=>String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const hlTok=s=>esc(s).replace(/(⟦[A-Z0-9]+_[0-9a-f]+⟧)/g,'<span class="tok">$1</span>');

async function api(path,body){
  const r=await fetch(path,{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(Object.assign({},body,{csrf:CSRF}))
  });
  const d=await r.json();
  if(d.error) throw new Error(d.error);
  return d;
}

/* ── stato ── */
(async()=>{
  try{
    const s=await(await fetch("/api/status")).json();
    $("pill-policy").textContent=s.policy.profile+" · "+s.policy.mode;
    const n=$("pill-ner");
    n.textContent=s.ner_enabled?"ner: "+s.ner_backend:"ner: off";
    n.className="badge "+(s.ner_enabled?"ok":"bad");
    n.title=s.ner_enabled
      ?"Nomi e organizzazioni rilevati"
      :"NER spento — installa anton-ocr[ner]";
  }catch(e){console.error(e)}
})();

/* ── drop ── */
const drop=$("drop");
drop.onclick=()=>$("file").click();
["dragenter","dragover"].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add("over")}));
["dragleave","drop"].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove("over")}));
drop.addEventListener("drop",e=>{if(e.dataTransfer.files[0])loadFile(e.dataTransfer.files[0])});
$("file").onchange=e=>{if(e.target.files[0])loadFile(e.target.files[0])};

function loadFile(f){
  lastName=f.name;
  const sizeKB=(f.size/1024).toFixed(1);
  if(f.name.toLowerCase().endsWith(".pdf")){
    pendingPdf=null;
    $("input").value="";
    $("input").placeholder="PDF caricato — premi Elabora";
    $("fname").textContent=f.name+" · "+sizeKB+" kB (PDF)";
    const r=new FileReader();
    r.onload=()=>{pendingPdf=r.result.split(",")[1]};
    r.readAsDataURL(f);
    return;
  }
  pendingPdf=null;
  $("input").placeholder="Incolla qui il testo del documento…";
  $("fname").textContent=f.name+" · "+sizeKB+" kB";
  const r=new FileReader();
  r.onload=()=>{$("input").value=r.result};
  r.readAsText(f);
}

/* ── pulisci ── */
$("btn-clear").onclick=()=>{
  $("input").value="";$("fname").textContent="";pendingPdf=null;
  $("input").placeholder="Incolla qui il testo del documento…";
  $("sec-detect").classList.add("hide");
  $("sec-result").classList.add("hide");
};

/* ── anteprima ── */
$("btn-scan").onclick=async()=>{
  const text=$("input").value;
  if(!text.trim())return;
  const btn=$("btn-scan");btn.disabled=true;
  try{
    const d=await api("/api/scan",{text});
    $("detect-rows").innerHTML=d.spans.map(s=>
      "<tr><td>"+esc(s.type)+"</td>"+
      "<td><code>"+esc(s.text)+"</code>"+
      (s.recovered?" <small style='color:var(--muted)'>"+esc(s.corrected||"")+"</small>":"")+
      "</td><td><span class='tag "+esc(s.layer)+"'>"+esc(s.layer)+"</span></td>"+
      "<td class='r'>"+s.confidence.toFixed(2)+"</td></tr>"
    ).join("")||"<tr><td colspan='4' style='color:var(--muted)'>Nessuna entità</td></tr>";

    const rec=d.stats.ocr_recovered||0;
    $("detect-summary").innerHTML=d.spans.length+" entità"+(rec?" · "+rec+" recuperate OCR":"")+" · nulla scritto su disco";
    $("sec-detect").classList.remove("hide");
  }catch(e){alert(e.message)}
  finally{btn.disabled=false}
};

/* ── elabora ── */
$("btn-process").onclick=async()=>{
  const text=$("input").value;
  const isPdf=!!pendingPdf;
  if(!isPdf&&!text.trim())return;
  const btn=$("btn-process");
  btn.disabled=true;btn.textContent="Elaborazione…";
  try{
    const d=isPdf
      ? await api("/api/process-pdf",{data:pendingPdf,filename:lastName})
      : await api("/api/process",{text,filename:lastName});
    lastUuid=d.uuid;lastMd=d.safe_markdown;lastManifest=d.manifest_json;
    $("uuid").value=d.uuid;

    const cls={allow:"ok",review:"warn",block:"bad"}[d.decision];
    const lbl={allow:"Sicuro",review:"Richiede revisione",block:"Bloccato"}[d.decision];
    let v='<div class="alert '+cls+'"><b>'+lbl+"</b>"+
      d.tokens+" segnaposto · qualità "+d.ocr_confidence+
      (d.reasons.length?"<br>"+d.reasons.map(esc).join("<br>"):"")+"</div>";
    if(!d.ner_enabled)
      v+='<div class="alert bad"><b>NER spento</b>Solo identificatori strutturati rilevati. Nomi e organizzazioni potrebbero essere ancora nel testo.</div>';
    $("verdict").innerHTML=v;

    const ent=Object.entries(d.entities).sort((a,b)=>b[1]-a[1]).map(kv=>kv[0]+" x"+kv[1]).join(" · ")||"nessuna";
    $("meta").innerHTML=
      "<dt>UUID</dt><dd>"+esc(d.uuid)+"</dd>"+
      "<dt>Entità</dt><dd>"+esc(ent)+"</dd>"+
      "<dt>Qualità</dt><dd>"+d.ocr_confidence+"</dd>";

    $("safe").innerHTML=hlTok(d.safe_markdown);
    $("saved").textContent="";
    $("sec-result").classList.remove("hide");
    $("sec-result").scrollIntoView({behavior:"smooth",block:"nearest"});
  }catch(e){alert(e.message)}
  finally{btn.disabled=false;btn.textContent="Elabora"}
};

/* ── copia / download ── */
$("btn-copy").onclick=async()=>{
  await navigator.clipboard.writeText(lastMd);
  $("saved").textContent="copiato";
  setTimeout(()=>{$("saved").textContent=""},2000);
};

function dl(content,name,mime){
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([content],{type:mime}));
  a.download=name;a.click();URL.revokeObjectURL(a.href);
}
const base=()=>lastName.replace(/\.[^.]+$/,"");
$("btn-download").onclick=()=>dl(lastMd,base()+".safe.md","text/markdown");
$("btn-download-manifest").onclick=()=>dl(lastManifest,base()+".manifest.json","application/json");

/* ── rehydrate ── */
$("btn-rehydrate").onclick=async()=>{
  const uuid=$("uuid").value.trim(),resp=$("response").value;
  if(!uuid||!resp.trim())return;
  const btn=$("btn-rehydrate");btn.disabled=true;
  try{
    const d=await api("/api/rehydrate",{uuid,response:resp});
    $("rehydrated-text").textContent=d.text;
    $("rehydrate-warn").innerHTML=
      '<div class="alert '+(d.warnings.length?"warn":"ok")+'"><b>'+
      d.substituted+"/"+d.tokens_issued+" risolti ("+Math.round(d.coverage*100)+
      "%)</b>"+d.warnings.map(esc).join("<br>")+"</div>";
    $("sec-rehydrated").classList.remove("hide");
  }catch(e){alert(e.message)}
  finally{btn.disabled=false}
};

/* ── forget ── */
$("btn-forget").onclick=async()=>{
  const uuid=$("uuid").value.trim();
  if(!uuid)return;
  if(!confirm("Distruggere la chiave di "+uuid+"?\n\nI segnaposto diventeranno irreversibili. Non è annullabile."))return;
  try{
    await api("/api/forget",{uuid});
    alert("Documento dimenticato. Token ora irreversibili.");
  }catch(e){alert(e.message)}
};
</script>
</body>
</html>
"""


def render_page(csrf_token: str, version: str) -> str:
    """Compone la pagina.

    Il token CSRF viene interpolato in un letterale JavaScript: va sanificato
    anche se generato internamente, perché un giorno potrebbe non esserlo più.
    """
    safe_token = html.escape(csrf_token, quote=True)
    return _PAGE.replace(CSRF_SENTINEL, safe_token).replace(
        VERSION_SENTINEL, html.escape(version)
    )
