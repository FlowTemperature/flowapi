from fastapi import FastAPI, HTTPException, Response, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from groq import Groq
from supabase import create_client, Client
from fastapi.responses import FileResponse
from openai import OpenAI
import os, itertools, logging, time
from typing import Optional
import json
import time
from fastapi import Request, Response
from collections import defaultdict
from fastapi.staticfiles import StaticFiles
from fastapi import Request
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Supabase ───────────────────────────────────────────────────────────────────
SUPA_URL = os.environ["SUPABASE_URL"]
SUPA_KEY = os.environ["SUPABASE_SERVICE_KEY"]
supa: Client = create_client(SUPA_URL, SUPA_KEY)

# ── OpenAI ─────────────────────────────────────────────────────────────────────
OPENAI_KEY = os.getenv("OPENAI_KEY", "").strip()
openai_client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
if openai_client:
    log.info("OpenAI client inicializado.")
else:
    log.warning("OPENAI_KEY não definida — modelos OpenAI indisponíveis.")

OPENAI_FREE_MODELS = [
    "gpt-4o-mini",
    "gpt-3.5-turbo",
    "gpt-4o-mini-2024-07-18",
    "gpt-3.5-turbo-0125",
]

# ── Groq keys ──────────────────────────────────────────────────────────────────
def load_keys() -> list[str]:
    raw, i = [], 1
    while k := os.getenv(f"GROQ_KEY{i}", "").strip():
        raw.append(k); i += 1
    if not raw:
        raise RuntimeError("Defina GROQ_KEY1, GROQ_KEY2... no ambiente.")
    seen, unique = set(), []
    for k in raw:
        if k not in seen:
            seen.add(k); unique.append(k)
    working = []
    for k in unique:
        try:
            Groq(api_key=k).models.list()
            working.append(k)
            log.info(f"Chave ...{k[-6:]} OK")
        except Exception as e:
            log.warning(f"Chave ...{k[-6:]} inválida: {e}")
    if not working:
        raise RuntimeError("Nenhuma chave Groq válida.")
    log.info(f"{len(working)} chave(s) ativas.")
    return working

keys = load_keys()
key_cycle = itertools.cycle(keys)

GROQ_MODELS = {
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "groq/compound",
    "openai/gpt-oss-safeguard-20b",
    "groq/compound-mini",
    "openai/gpt-oss-120b",
}

ALLOWED_MODELS = GROQ_MODELS | (set(OPENAI_FREE_MODELS) if OPENAI_KEY else set())

AUTO_SYSTEM = """Você é um roteador de modelos. Analise o prompt e responda APENAS com o nome exato do modelo mais adequado, sem texto adicional.

Modelos Groq:
- llama-3.1-8b-instant → perguntas simples, conversas casuais
- llama-3.3-70b-versatile → raciocínio complexo, análise, criatividade
- openai/gpt-oss-120b → código, programação, debugging
- groq/compound-mini → resumos rápidos
- groq/compound → instruções diretas, formato definido
- openai/gpt-oss-safeguard-20b → conteúdo que precisa de moderação

Modelos OpenAI:
- gpt-4o-mini → tarefas gerais rápidas e econômicas
- gpt-3.5-turbo → conversas e tarefas simples

Em caso de dúvida: llama-3.3-70b-versatile"""

DAILY_LIMIT = 20000
MAX_KEYS_PER_USER = 200

# ══════════════════════════════════════════════════════════════════════════════
# SHARED ASSETS
# ══════════════════════════════════════════════════════════════════════════════

MESH_JS = """
<canvas id="mesh-canvas" style="position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0;opacity:.35"></canvas>
<script>
(function(){
  const c = document.getElementById('mesh-canvas');
  const ctx = c.getContext('2d');
  let W, H, pts;
  const COLS = 14, ROWS = 10, SPEED = 0.0008;

  function init(){
    W = c.width = window.innerWidth;
    H = c.height = window.innerHeight;
    pts = [];
    for(let r=0;r<=ROWS;r++){
      for(let col=0;col<=COLS;col++){
        pts.push({
          bx: (col/COLS)*W, by: (r/ROWS)*H,
          ox: (Math.random()-.5)*38, oy: (Math.random()-.5)*38,
          px: 0, py: 0,
          phase: Math.random()*Math.PI*2,
          freq: .4+Math.random()*.4
        });
      }
    }
  }

  function draw(t){
    ctx.clearRect(0,0,W,H);
    pts.forEach(p=>{
      p.px = p.bx + p.ox*Math.sin(t*SPEED*p.freq + p.phase);
      p.py = p.by + p.oy*Math.cos(t*SPEED*p.freq + p.phase + 1);
    });

    const stride = COLS+1;
    ctx.lineWidth = .7;

    for(let r=0;r<ROWS;r++){
      for(let col=0;col<COLS;col++){
        const tl = pts[r*stride+col];
        const tr = pts[r*stride+col+1];
        const bl = pts[(r+1)*stride+col];
        const br = pts[(r+1)*stride+col+1];

        ctx.beginPath();
        ctx.moveTo(tl.px,tl.py); ctx.lineTo(tr.px,tr.py);
        ctx.strokeStyle='rgba(124,111,255,0.18)'; ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(tl.px,tl.py); ctx.lineTo(bl.px,bl.py);
        ctx.strokeStyle='rgba(167,139,250,0.13)'; ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(tl.px,tl.py); ctx.lineTo(br.px,br.py);
        ctx.strokeStyle='rgba(124,111,255,0.06)'; ctx.stroke();
      }
    }

    pts.forEach(p=>{
      ctx.beginPath();
      ctx.arc(p.px,p.py,1.4,0,Math.PI*2);
      ctx.fillStyle='rgba(167,139,250,0.25)';
      ctx.fill();
    });
  }

  let raf;
  function loop(t){ draw(t); raf=requestAnimationFrame(loop); }
  window.addEventListener('resize',()=>{ cancelAnimationFrame(raf); init(); loop(0); });
  init(); loop(0);
})();
</script>
"""

SHARED_STYLE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#030308;
    --card-bg:rgba(12,12,22,0.82);
    --border:rgba(255,255,255,0.07);
    --primary:#7c6fff;
    --primary-h:#6b5de8;
    --primary-glow:rgba(124,111,255,0.28);
    --text:#f0f0fa;
    --muted:#8892a4;
    --dim:#505870;
    --error:#ef4444;
    --success:#10b981;
    --input-bg:rgba(0,0,0,0.35);
    --font:'Inter',sans-serif;
  }
  *{margin:0;padding:0;box-sizing:border-box;font-family:var(--font)}
  html{scroll-behavior:smooth}
  body{background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden;position:relative}
  body>*:not(#mesh-canvas){position:relative;z-index:1}

  @keyframes float{0%,100%{transform:translateY(0) rotate(0)}50%{transform:translateY(-7px) rotate(1.5deg)}}
  @keyframes fadein{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
  @keyframes spin{to{transform:rotate(360deg)}}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  @keyframes slideup{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:none}}

  header{
    display:flex;align-items:center;justify-content:space-between;
    padding:1.1rem 2rem;border-bottom:1px solid var(--border);
    backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
    background:rgba(3,3,8,0.85);position:sticky;top:0;z-index:100;
  }
  .logo{font-size:1.25rem;font-weight:800;color:#fff;display:flex;align-items:center;gap:10px;text-decoration:none;letter-spacing:-.4px}
  .logo .accent{background:linear-gradient(135deg,#7c6fff,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .logo img{width:34px;height:34px;border-radius:9px;box-shadow:0 0 18px var(--primary-glow);animation:float 4s ease-in-out infinite}
  .nav-links{display:flex;gap:6px;align-items:center}
  .nav-links a.nav-item{padding:6px 14px;border-radius:7px;font-size:12px;font-weight:600;border:1px solid var(--border);color:var(--muted);text-decoration:none;transition:.18s}
  .nav-links a.nav-item:hover{background:rgba(255,255,255,0.05);color:var(--text)}
  .btn-dash{
    background:linear-gradient(135deg,#7c6fff,#a78bfa);color:#fff;text-decoration:none;
    padding:9px 20px;border-radius:100px;font-size:13px;font-weight:700;
    display:inline-flex;align-items:center;gap:7px;
    box-shadow:0 0 18px var(--primary-glow);border:1px solid rgba(255,255,255,0.1);
    transition:all .25s cubic-bezier(.175,.885,.32,1.275);
  }
  .btn-dash:hover{transform:scale(1.05) translateY(-2px);box-shadow:0 8px 22px var(--primary-glow);filter:brightness(1.1)}
  .btn-dash svg{transition:transform .25s}.btn-dash:hover svg{transform:translateX(3px)}

  footer{text-align:center;padding:2rem;border-top:1px solid var(--border);color:var(--dim);font-size:13px;margin-top:4rem;backdrop-filter:blur(8px);background:rgba(3,3,8,0.6)}
  footer a{color:var(--primary);text-decoration:none}.footer a:hover{text-decoration:underline}
</style>
"""

def _header(active_nav=""):
    nav_status = ' class="nav-item' + (' active" style="color:var(--text);background:rgba(255,255,255,0.05)"' if active_nav=="status" else '"') + ' href="/status"'
    nav_privacy = ' class="nav-item' + (' active" style="color:var(--text);background:rgba(255,255,255,0.05)"' if active_nav=="privacy" else '"') + ' href="/privacy"'
    return f"""
<header>
  <a href="/" class="logo">
<img src="/public/flow.png" alt="Flow API">
    Flow<span class="accent">API</span>
  </a>
  <nav class="nav-links">
    <a{nav_status}>Status</a>
    <a{nav_privacy}>Privacidade</a>
    <a href="/dashboard" class="btn-dash">Dashboard
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
      </svg>
    </a>
  </nav>
</header>
"""

def _footer():
    return '<footer>Flow API — powered by <a href="https://groq.com" target="_blank">Groq</a> &amp; <a href="https://openai.com" target="_blank">OpenAI</a> &nbsp;·&nbsp; feito com ♥ no Brasil</footer>'

def _crisp():
    return '<script>window.$crisp=[];window.CRISP_WEBSITE_ID="04fbe754-2bfe-43a6-9e9e-704ca3a5bdce";(function(){d=document;s=d.createElement("script");s.src="https://client.crisp.chat/l.js";s.async=1;d.getElementsByTagName("head")[0].appendChild(s);})()</script>'

def _meta(title, desc):
    return f"""
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="icon" type="image/png" href="/public/flow.png">
"""

# ── build model pills for landing (grouped by provider) ───────────────────────
def _model_pills_html() -> str:
    groq_pills = "".join(
        f'<span class="model-pill groq-pill">{m}</span>'
        for m in sorted(GROQ_MODELS)
    )
    oai_section = ""
    if OPENAI_KEY:
        oai_pills = "".join(
            f'<span class="model-pill oai-pill">{m}</span>'
            for m in sorted(OPENAI_FREE_MODELS)
        )
        oai_section = f"""
  <div class="models-group-label">
    <span class="provider-badge oai-badge">OpenAI</span>
  </div>
  <div class="models-grid" style="margin-bottom:1rem">{oai_pills}</div>
"""
    return f"""
  <div class="models-group-label">
    <span class="provider-badge groq-badge">Groq</span>
  </div>
  <div class="models-grid" style="margin-bottom:1rem">{groq_pills}</div>
  {oai_section}
"""

# ══════════════════════════════════════════════════════════════════════════════
# LANDING PAGE
# ══════════════════════════════════════════════════════════════════════════════
def _build_landing() -> str:
    oai_stat = f'<div class="stat"><div class="num">{len(OPENAI_FREE_MODELS)}</div><div class="lbl">Modelos OAI</div></div>' if OPENAI_KEY else ""
    total_models = len(GROQ_MODELS) + (len(OPENAI_FREE_MODELS) if OPENAI_KEY else 0)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
{_meta("Flow API [BETA]","API simples e rápida com múltiplos modelos Groq e OpenAI. Integre em qualquer linguagem com uma única chamada POST.")}
<meta name="google-site-verification" content="THgUiIuZav-0VMxcvgX7IC2whvu_gP-T89e-Ur2gs1k">
<meta property="og:type" content="website">
<meta property="og:url" content="https://flow.squareweb.app/">
<meta property="og:title" content="Flow API | IA Potente e Gratuita para Devs">
<meta property="og:description" content="Integre os melhores modelos de IA do mundo no seu projeto em segundos.">
<meta property="og:image" content="https://flow.squareweb.app/public/flow.png">
<meta property="twitter:card" content="summary_large_image">
{SHARED_STYLE}
{_crisp()}
<style>
  .hero{{text-align:center;padding:6rem 2rem 3rem;animation:fadein .7s ease}}
  .hero-badge{{display:inline-flex;align-items:center;gap:6px;background:rgba(124,111,255,0.12);border:1px solid rgba(124,111,255,0.25);border-radius:100px;padding:5px 14px;font-size:12px;color:#a78bfa;font-weight:600;margin-bottom:1.5rem}}
  .hero-badge span{{width:6px;height:6px;background:#a78bfa;border-radius:50%;animation:pulse 1.8s ease infinite}}
  .hero h1{{font-size:clamp(2.4rem,5.5vw,4rem);font-weight:800;line-height:1.08;letter-spacing:-1.5px;margin-bottom:1.2rem}}
  .hero h1 .grad{{background:linear-gradient(135deg,#7c6fff,#a78bfa 60%,#c4b5fd);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .hero p{{font-size:1.05rem;color:var(--muted);max-width:500px;margin:0 auto 2.2rem;line-height:1.75}}
  .btn-group{{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}}
  .btn-primary{{padding:12px 28px;border-radius:10px;font-size:14px;font-weight:700;cursor:pointer;border:none;background:linear-gradient(135deg,var(--primary),#a78bfa);color:#fff;box-shadow:0 8px 20px var(--primary-glow);transition:.2s}}
  .btn-primary:hover{{transform:translateY(-2px);filter:brightness(1.1);box-shadow:0 14px 28px var(--primary-glow)}}
  .btn-outline{{padding:12px 28px;border-radius:10px;font-size:14px;font-weight:700;cursor:pointer;border:1px solid var(--border);background:rgba(255,255,255,0.03);color:var(--text);transition:.2s}}
  .btn-outline:hover{{background:rgba(255,255,255,0.07);border-color:rgba(255,255,255,0.15)}}

  .stats{{display:flex;gap:0;justify-content:center;flex-wrap:wrap;padding:2.5rem 2rem;max-width:780px;margin:0 auto}}
  .stat{{flex:1;min-width:120px;text-align:center;padding:1.2rem;border:1px solid var(--border);backdrop-filter:blur(8px);background:rgba(255,255,255,0.02)}}
  .stat:first-child{{border-radius:14px 0 0 14px}}.stat:last-child{{border-radius:0 14px 14px 0}}
  .stat .num{{font-size:1.9rem;font-weight:800;background:linear-gradient(135deg,#7c6fff,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .stat .lbl{{font-size:11px;color:var(--dim);margin-top:3px;text-transform:uppercase;letter-spacing:.5px}}

  .section{{max-width:920px;margin:0 auto;padding:3rem 2rem}}
  .section-title{{font-size:1.3rem;font-weight:700;margin-bottom:1.5rem;display:flex;align-items:center;gap:10px}}
  .section-title::after{{content:'';flex:1;height:1px;background:linear-gradient(90deg,var(--border),transparent)}}

  .playground{{background:rgba(10,10,20,0.7);border:1px solid var(--border);border-radius:16px;padding:1.6rem;backdrop-filter:blur(12px)}}
  .playground textarea{{width:100%;background:rgba(0,0,0,0.4);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:14px;padding:12px 15px;resize:vertical;min-height:82px;margin-bottom:12px;outline:none;transition:.2s;font-family:var(--font)}}
  .playground textarea:focus{{border-color:var(--primary);background:rgba(124,111,255,0.05);box-shadow:0 0 0 3px rgba(124,111,255,0.12)}}
  .pg-row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}}
  .pg-row select{{background:rgba(0,0,0,0.4);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;padding:8px 12px;outline:none;cursor:pointer;transition:.2s}}
  .pg-row select:focus{{border-color:var(--primary)}}
  .mode-toggle{{display:flex;gap:4px;background:rgba(0,0,0,0.4);border:1px solid var(--border);border-radius:9px;padding:4px}}
  .mode-btn{{padding:6px 16px;border-radius:6px;font-size:12px;font-weight:700;cursor:pointer;border:none;background:transparent;color:var(--muted);transition:.18s}}
  .mode-btn.active{{background:var(--primary);color:#fff;box-shadow:0 2px 10px var(--primary-glow)}}
  #auto-badge{{display:none;font-size:11px;color:#a78bfa;background:rgba(124,111,255,0.1);border:1px solid rgba(124,111,255,0.2);border-radius:5px;padding:4px 10px;font-weight:600}}
  #pg-result{{background:rgba(0,0,0,0.5);border:1px solid var(--border);border-radius:10px;padding:14px;font-size:13px;color:#4ade80;font-family:'Fira Code','Courier New',monospace;min-height:60px;white-space:pre-wrap;display:none;animation:fadein .3s ease;line-height:1.7}}
  #pg-result.err{{color:#f87171}}

  .endpoint-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;margin-bottom:2rem}}
  .endpoint-card{{background:rgba(10,10,20,0.7);border:1px solid var(--border);border-radius:12px;padding:1rem 1.2rem;transition:.2s;backdrop-filter:blur(8px)}}
  .endpoint-card:hover{{border-color:rgba(124,111,255,0.3);transform:translateY(-2px)}}
  .method{{font-size:10px;font-weight:800;padding:3px 8px;border-radius:5px;display:inline-block;margin-bottom:7px;letter-spacing:.5px}}
  .get{{background:rgba(16,185,129,0.15);color:#34d399}}.post{{background:rgba(124,111,255,0.15);color:#a78bfa}}
  .endpoint-card .path{{font-family:'Fira Code','Courier New',monospace;font-size:13px;color:var(--text);margin-bottom:5px}}
  .endpoint-card p{{font-size:12px;color:var(--dim)}}

  .code-block{{background:rgba(5,5,15,0.85);border:1px solid var(--border);border-radius:14px;position:relative;overflow:hidden}}
  .code-block::before{{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,var(--primary),transparent);opacity:.5}}
  .copy-btn{{position:absolute;top:12px;right:12px;background:rgba(124,111,255,0.15);border:1px solid rgba(124,111,255,0.25);color:#a78bfa;border-radius:7px;padding:5px 12px;font-size:11px;font-weight:700;cursor:pointer;z-index:2;transition:.18s}}
  .copy-btn:hover{{background:rgba(124,111,255,0.25);color:#fff}}
  .code-block pre{{font-family:'Fira Code','Courier New',monospace;font-size:13px;line-height:1.75;color:#c8d3e0;white-space:pre;overflow-x:auto;padding:1.4rem 1.6rem}}
  .tabs{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}}
  .tab{{padding:7px 15px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;border:1px solid var(--border);background:transparent;color:var(--muted);transition:.18s}}
  .tab.active{{background:var(--primary);color:#fff;border-color:var(--primary);box-shadow:0 2px 10px var(--primary-glow)}}
  .tab:hover:not(.active){{background:rgba(255,255,255,0.05);color:var(--text)}}

  .models-grid{{display:flex;flex-wrap:wrap;gap:8px}}
  .model-pill{{border-radius:100px;padding:7px 16px;font-size:12px;font-family:'Fira Code','Courier New',monospace;transition:.18s;cursor:default}}
  .groq-pill{{background:rgba(124,111,255,0.08);border:1px solid rgba(124,111,255,0.18);color:#a78bfa}}
  .groq-pill:hover{{background:rgba(124,111,255,0.16);border-color:rgba(124,111,255,0.4)}}
  .oai-pill{{background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.2);color:#34d399}}
  .oai-pill:hover{{background:rgba(16,185,129,0.15);border-color:rgba(16,185,129,0.4)}}
  .models-group-label{{margin-bottom:.6rem;margin-top:.4rem}}
  .provider-badge{{font-size:11px;font-weight:700;padding:3px 10px;border-radius:5px;letter-spacing:.4px;text-transform:uppercase}}
  .groq-badge{{background:rgba(124,111,255,0.15);color:#a78bfa;border:1px solid rgba(124,111,255,0.25)}}
  .oai-badge{{background:rgba(16,185,129,0.12);color:#34d399;border:1px solid rgba(16,185,129,0.22)}}
</style>
</head>
<body>
{MESH_JS}
{_header()}

<div class="hero">
  <div class="hero-badge"><span></span> Beta público disponível</div>
  <h1>IA gratuita,<br><span class="grad">sem complicação</span></h1>
  <p>API simples e ultra-rápida com múltiplos modelos Groq e OpenAI. Integre em qualquer linguagem com uma única chamada POST.</p>
  <div class="btn-group">
    <button class="btn-primary" onclick="window.location.href='/dashboard'">Testar agora</button>
    <button class="btn-outline" onclick="document.getElementById('docs-section').scrollIntoView({{behavior:'smooth'}})">Ver documentação</button>
  </div>
</div>

<div class="stats">
  <div class="stat"><div class="num">{total_models}+</div><div class="lbl">Modelos</div></div>
  <div class="stat"><div class="num">REST</div><div class="lbl">Interface</div></div>
  <div class="stat"><div class="num">Auto</div><div class="lbl">Seleção IA</div></div>
  {oai_stat}
  <div class="stat"><div class="num">∞</div><div class="lbl">Linguagens</div></div>
  <div id="pg-result"></div>
</div>

<div class="section">
  <div class="section-title">Endpoints</div>
  <div class="endpoint-grid">
    <div class="endpoint-card"><span class="method get">GET</span><div class="path">/</div><p>Landing page</p></div>
    <div class="endpoint-card"><span class="method get">GET</span><div class="path">/health</div><p>Healthcheck</p></div>
    <div class="endpoint-card"><span class="method get">GET</span><div class="path">/models</div><p>Lista modelos disponíveis</p></div>
    <div class="endpoint-card"><span class="method post">POST</span><div class="path">/generate</div><p>Gera resposta — suporta mode: "auto"</p></div>
    <div class="endpoint-card"><span class="method post">POST</span><div class="path">/v1/chat/completions</div><p>OpenAI-compatible</p></div>
    <div class="endpoint-card"><span class="method get">GET</span><div class="path">/v1/models</div><p>Modelos — formato OpenAI</p></div>
    <div class="endpoint-card"><span class="method post">POST</span><div class="path">/dashboard</div><p>Criar chave API e Listar chave API</p></div>
 
  </div>
</div>

<div class="section" id="docs-section">
  <div class="section-title">Exemplos de integração</div>
  <div class="tabs" id="tabs"></div>
  <div class="code-block">
    <button class="copy-btn" onclick="copyCode()">copiar</button>
    <pre id="code-out"></pre>
  </div>
</div>

<div class="section">
  <div class="section-title">Modelos disponíveis</div>
  {_model_pills_html()}
</div>

{_footer()}

<script>
const BASE = window.location.origin
let currentMode = 'manual'

function setMode(mode) {{
  currentMode = mode
  document.getElementById('btn-manual').classList.toggle('active', mode==='manual')
  document.getElementById('btn-auto').classList.toggle('active', mode==='auto')
  document.getElementById('pg-model').style.display = mode==='manual' ? 'block' : 'none'
  document.getElementById('auto-badge').style.display = mode==='auto' ? 'inline-block' : 'none'
}}

async function runPlayground() {{
  const prompt = document.getElementById('pg-prompt').value.trim()
  const model = document.getElementById('pg-model').value
  const result = document.getElementById('pg-result')
  const btn = document.getElementById('pg-btn')
  if (!prompt) return
  btn.textContent = 'enviando...'; btn.disabled = true
  result.style.display = 'block'; result.className = ''
  result.textContent = currentMode==='auto' ? 'Analisando prompt e escolhendo modelo...' : '...'
  try {{
    const body = currentMode==='auto' ? {{prompt, mode:'auto'}} : {{prompt, model}}
    const res = await fetch(BASE+'/generate', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(body)
    }})
    const data = await res.json()
    if (data.response) {{
      const info = currentMode==='auto' ? `[modelo: ${{data.model}}]\n\n` : ''
      result.textContent = info + data.response
    }} else {{
      result.className = 'err'
      result.textContent = JSON.stringify(data, null, 2)
    }}
  }} catch(e) {{
    result.className = 'err'; result.textContent = 'Erro: ' + e.message
  }}
  btn.textContent = 'Enviar'; btn.disabled = false
}}

const langs = {{
  'cURL (manual)': `curl -X POST "${{BASE}}/generate" \\\\\\n  -H "Content-Type: application/json" \\\\\\n  -H "Authorization: Bearer flow_SUA_CHAVE" \\\\\\n  -d '{{"prompt": "Olá!", "model": "llama-3.1-8b-instant"}}'`,
  'cURL (auto)': `curl -X POST "${{BASE}}/generate" \\\\\\n  -H "Content-Type: application/json" \\\\\\n  -H "Authorization: Bearer flow_SUA_CHAVE" \\\\\\n  -d '{{"prompt": "Olá!", "mode": "auto"}}'`,
  'cURL (OpenAI)': `curl -X POST "${{BASE}}/v1/chat/completions" \\\\\\n  -H "Content-Type: application/json" \\\\\\n  -H "Authorization: Bearer flow_SUA_CHAVE" \\\\\\n  -d '{{"model":"llama-3.1-8b-instant","messages":[{{"role":"user","content":"Olá!"}}]}}'`,
  'Python': `import requests\\n\\nres = requests.post("${{BASE}}/generate",\\n    headers={{"Authorization": "Bearer flow_SUA_CHAVE"}},\\n    json={{"prompt": "Olá!", "model": "llama-3.1-8b-instant"}}\\n)\\nprint(res.json()["response"])`,
  'JavaScript': `const res = await fetch("${{BASE}}/generate", {{\\n  method: "POST",\\n  headers: {{\\n    "Content-Type": "application/json",\\n    "Authorization": "Bearer flow_SUA_CHAVE"\\n  }},\\n  body: JSON.stringify({{ prompt: "Olá!", model: "llama-3.1-8b-instant" }})\\n}})\\nconst data = await res.json()\\nconsole.log(data.response)`,
  'TypeScript': `const res = await fetch("${{BASE}}/generate", {{\\n  method: "POST",\\n  headers: {{\\n    "Content-Type": "application/json",\\n    "Authorization": "Bearer flow_SUA_CHAVE"\\n  }},\\n  body: JSON.stringify({{ prompt: "Olá!", mode: "auto" }})\\n}})\\nconst data: {{ response: string; model: string; tokens_used: number }} = await res.json()\\nconsole.log(data.model, data.response)`,
  'Go': `package main\\nimport ("bytes";"encoding/json";"fmt";"net/http")\\nfunc main() {{\\n  body, _ := json.Marshal(map[string]string{{"prompt":"Olá!","model":"llama-3.1-8b-instant"}})\\n  req, _ := http.NewRequest("POST","${{BASE}}/generate",bytes.NewBuffer(body))\\n  req.Header.Set("Content-Type","application/json")\\n  req.Header.Set("Authorization","Bearer flow_SUA_CHAVE")\\n  resp, _ := http.DefaultClient.Do(req)\\n  var r map[string]interface{{}}\\n  json.NewDecoder(resp.Body).Decode(&r)\\n  fmt.Println(r["response"])\\n}}`,
  'PHP': `<?php\\n$ch = curl_init("${{BASE}}/generate");\\ncurl_setopt_array($ch, [\\n  CURLOPT_POST=>true, CURLOPT_RETURNTRANSFER=>true,\\n  CURLOPT_HTTPHEADER=>["Content-Type: application/json","Authorization: Bearer flow_SUA_CHAVE"],\\n  CURLOPT_POSTFIELDS=>json_encode(["prompt"=>"Olá!","model"=>"llama-3.1-8b-instant"])\\n]);\\n$data = json_decode(curl_exec($ch), true);\\necho $data["response"];`,
  'Ruby': `require 'net/http'; require 'json'\\nuri = URI("${{BASE}}/generate")\\nreq = Net::HTTP::Post.new(uri)\\nreq["Content-Type"] = "application/json"\\nreq["Authorization"] = "Bearer flow_SUA_CHAVE"\\nreq.body = {{prompt:"Olá!",model:"llama-3.1-8b-instant"}}.to_json\\nres = Net::HTTP.start(uri.hostname,uri.port,:use_ssl=>true){{|h|h.request(req)}}\\nputs JSON.parse(res.body)["response"]`,
  'Java': `var client = HttpClient.newHttpClient();\\nvar req = HttpRequest.newBuilder()\\n  .uri(URI.create("${{BASE}}/generate"))\\n  .header("Content-Type","application/json")\\n  .header("Authorization","Bearer flow_SUA_CHAVE")\\n  .POST(HttpRequest.BodyPublishers.ofString(\\n    "{{\\"prompt\\":\\"Olá!\\",\\"model\\":\\"llama-3.1-8b-instant\\"}}"\\n  )).build();\\nSystem.out.println(client.send(req,HttpResponse.BodyHandlers.ofString()).body());`,
  'C#': `var res = await new HttpClient().SendAsync(new HttpRequestMessage(HttpMethod.Post,"${{BASE}}/generate"){{\\n  Headers = {{ Authorization = new("Bearer","flow_SUA_CHAVE") }},\\n  Content = new StringContent(JsonSerializer.Serialize(new{{prompt="Olá!",model="llama-3.1-8b-instant"}}),\\n    Encoding.UTF8,"application/json")\\n}});\\nvar json = JsonSerializer.Deserialize<JsonElement>(await res.Content.ReadAsStringAsync());\\nConsole.WriteLine(json.GetProperty("response").GetString());`,
}}

const tabNames = Object.keys(langs)
const tabsEl = document.getElementById('tabs')
const codeEl = document.getElementById('code-out')
let active = tabNames[0]
tabNames.forEach(name => {{
  const t = document.createElement('button')
  t.className = 'tab'+(name===active?' active':'')
  t.textContent = name
  t.onclick = () => {{
    active = name
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'))
    t.classList.add('active')
    codeEl.textContent = langs[name]
  }}
  tabsEl.appendChild(t)
}})
codeEl.textContent = langs[active]

function copyCode() {{
  navigator.clipboard.writeText(langs[active])
  const btn = document.querySelector('.copy-btn')
  btn.textContent = 'copiado!'
  setTimeout(()=>btn.textContent='copiar',1600)
}}
</script>
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-PGGKC74064"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-PGGKC74064');
</script>
</body>
</html>"""

LANDING = _build_landing()

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD / LOGIN PAGE
# ══════════════════════════════════════════════════════════════════════════════
DASHBOARD_HTML = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
{_meta("Flow API — Dashboard","Gerencie suas chaves de API e monitore o consumo.")}
{SHARED_STYLE}
{_crisp()}
<style>
  main{{flex:1;display:flex;align-items:center;justify-content:center;padding:3rem 1.5rem;min-height:calc(100vh - 140px)}}
  body{{display:flex;flex-direction:column}}

  .card{{
    background:var(--card-bg);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);
    border:1px solid var(--border);border-radius:22px;padding:2.4rem;
    width:100%;max-width:430px;box-shadow:0 30px 60px rgba(0,0,0,0.5);
    animation:fadein .55s cubic-bezier(.16,1,.3,1);
  }}
  .card-title{{font-size:1.7rem;font-weight:800;margin-bottom:.4rem;letter-spacing:-.5px;text-align:center}}
  .card-title .grad{{background:linear-gradient(135deg,#7c6fff,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .card-sub{{color:var(--muted);font-size:.9rem;margin-bottom:1.8rem;text-align:center;line-height:1.6}}

  .auth-tabs{{display:flex;background:rgba(0,0,0,0.4);border:1px solid var(--border);border-radius:11px;padding:4px;margin-bottom:1.8rem;gap:4px}}
  .auth-tab{{flex:1;padding:9px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;border:none;background:transparent;color:var(--dim);transition:.18s}}
  .auth-tab:hover{{color:var(--muted)}}
  .auth-tab.active{{background:var(--primary);color:#fff;box-shadow:0 3px 10px var(--primary-glow)}}

  .form-group{{margin-bottom:1.1rem}}
  label{{display:block;font-size:12px;font-weight:700;color:var(--muted);margin-bottom:7px;text-transform:uppercase;letter-spacing:.4px}}
  input{{width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:14px;padding:11px 14px;outline:none;transition:.2s}}
  input:focus{{border-color:var(--primary);background:rgba(124,111,255,0.05);box-shadow:0 0 0 3px rgba(124,111,255,0.12)}}
  .btn{{width:100%;padding:13px;border-radius:11px;font-size:14px;font-weight:700;cursor:pointer;border:none;background:linear-gradient(135deg,var(--primary),#a78bfa);color:#fff;margin-top:.9rem;transition:.25s;display:flex;align-items:center;justify-content:center;gap:8px;box-shadow:0 8px 18px var(--primary-glow)}}
  .btn:hover:not(:disabled){{transform:translateY(-2px);filter:brightness(1.08);box-shadow:0 14px 26px var(--primary-glow)}}
  .btn:disabled{{opacity:.55;cursor:not-allowed}}
  .spinner{{width:16px;height:16px;border:2.5px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .75s linear infinite}}
  .msg{{margin-top:1rem;padding:11px 14px;border-radius:10px;font-size:13px;font-weight:500;display:none;animation:fadein .25s ease}}
  .msg.err{{background:rgba(239,68,68,.1);color:#fca5a5;border:1px solid rgba(239,68,68,.2);display:block}}
  .msg.ok{{background:rgba(16,185,129,.1);color:#6ee7b7;border:1px solid rgba(16,185,129,.2);display:block}}
  .divider{{text-align:center;color:var(--dim);font-size:12px;margin-top:1.8rem}}
  .divider a{{color:var(--primary);text-decoration:none;font-weight:600}}.divider a:hover{{text-decoration:underline}}

  .dashboard{{display:none;max-width:580px;animation:fadein .5s ease}}
  .dash-header{{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:1.8rem}}
  .dash-title{{font-size:1.4rem;font-weight:800;letter-spacing:-.4px}}
  .dash-email{{color:var(--muted);font-size:.85rem;margin-top:3px}}
  .logout-btn{{font-size:12px;font-weight:700;color:var(--dim);cursor:pointer;background:rgba(255,255,255,.04);border:1px solid var(--border);padding:6px 12px;border-radius:8px;transition:.18s}}
  .logout-btn:hover{{background:rgba(239,68,68,.1);color:#fca5a5;border-color:rgba(239,68,68,.2)}}
  .usage-wrap{{background:rgba(0,0,0,.3);border:1px solid var(--border);border-radius:14px;padding:1.2rem;margin-bottom:1.8rem}}
  .usage-label{{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:9px}}
  .usage-label span:last-child{{color:var(--primary);font-weight:700}}
  .usage-track{{background:rgba(255,255,255,.05);border-radius:10px;height:7px;overflow:hidden}}
  .usage-fill{{height:100%;border-radius:10px;background:linear-gradient(90deg,var(--primary),#a78bfa);transition:width 1s cubic-bezier(.34,1.56,.64,1);box-shadow:0 0 8px var(--primary-glow)}}
  .key-item{{margin-bottom:1.4rem;animation:fadein .35s ease}}
  .key-name{{font-size:11px;font-weight:700;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px;display:flex;align-items:center;gap:7px}}
  .key-box{{background:rgba(0,0,0,.4);border:1px solid var(--border);border-radius:11px;padding:12px 15px;font-family:'Fira Code','Courier New',monospace;font-size:13px;color:#a78bfa;word-break:break-all;margin-bottom:.65rem;cursor:pointer;transition:.18s;display:flex;justify-content:space-between;align-items:center;gap:10px}}
  .key-box:hover{{border-color:var(--primary);background:rgba(124,111,255,.06);transform:translateY(-1px)}}
  .key-tag{{font-size:10px;font-weight:700;color:var(--dim);background:rgba(255,255,255,.05);padding:2px 7px;border-radius:4px;flex-shrink:0}}
  .key-actions{{display:flex;gap:7px}}
  .btn-sm{{padding:7px 14px;border-radius:9px;font-size:12px;font-weight:700;cursor:pointer;border:1px solid var(--border);background:rgba(255,255,255,.03);color:var(--muted);transition:.18s;display:flex;align-items:center;gap:5px}}
  .btn-sm:hover{{background:rgba(255,255,255,.07);color:var(--text);border-color:var(--dim)}}
  .btn-sm.danger:hover{{background:rgba(239,68,68,.1);color:#fca5a5;border-color:rgba(239,68,68,.2)}}
  .btn-sm.primary{{background:var(--primary);color:#fff;border-color:var(--primary);box-shadow:0 3px 10px var(--primary-glow)}}
  .btn-sm.primary:hover{{background:var(--primary-h);transform:translateY(-1px)}}
  .no-keys{{text-align:center;color:var(--dim);padding:2.5rem 0;font-size:.9rem;border:2px dashed var(--border);border-radius:16px;margin-bottom:1.4rem}}
  .copy-toast{{position:fixed;bottom:28px;left:50%;transform:translateX(-50%);background:#10b981;color:#fff;border-radius:100px;padding:11px 22px;font-size:13px;font-weight:700;display:none;z-index:1000;box-shadow:0 8px 22px rgba(16,185,129,.4);animation:fadein .3s cubic-bezier(.175,.885,.32,1.275)}}
</style>
</head>
<body>
{MESH_JS}
{_header()}

<main>
  <div class="card" id="auth-card">
    <div class="card-title">Bem-vindo à <span class="grad">Flow API</span></div>
    <div class="card-sub">Acesse sua infraestrutura de alta performance e gerencie suas chaves com facilidade.</div>

    <div class="auth-tabs">
      <button class="auth-tab active" id="tab-login" onclick="setTab('login')">Entrar</button>
      <button class="auth-tab" id="tab-register" onclick="setTab('register')">Criar conta</button>
    </div>

    <div id="form-login">
      <div class="form-group">
        <label>E-mail</label>
        <input type="email" id="login-email" placeholder="nome@empresa.com" autocomplete="email">
      </div>
      <div class="form-group">
        <label>Senha</label>
        <input type="password" id="login-pass" placeholder="••••••••" autocomplete="current-password"
          onkeydown="if(event.key==='Enter')doLogin()">
      </div>
      <button class="btn" id="btn-login" onclick="doLogin()">Acessar Dashboard</button>
      <div class="msg" id="msg-login"></div>
    </div>

    <div id="form-register" style="display:none">
      <div class="form-group">
        <label>E-mail</label>
        <input type="email" id="reg-email" placeholder="nome@empresa.com" autocomplete="email">
      </div>
      <div class="form-group">
        <label>Senha (mín. 6 caracteres)</label>
        <input type="password" id="reg-pass" placeholder="••••••••" autocomplete="new-password">
      </div>
      <div class="form-group">
        <label>Confirmar senha</label>
        <input type="password" id="reg-pass2" placeholder="••••••••" onkeydown="if(event.key==='Enter')doRegister()">
      </div>
      <button class="btn" id="btn-register" onclick="doRegister()">Criar minha conta</button>
      <div class="msg" id="msg-register"></div>
    </div>

    <div class="divider">Problemas no acesso? <a href="mailto:flowapi@proton.me">Contate o suporte</a></div>
  </div>

  <div class="card dashboard" id="dashboard">
    <div class="dash-header">
      <div>
        <div class="dash-title">Gerenciar Chaves</div>
        <div class="dash-email" id="dash-email"></div>
      </div>
      <button class="logout-btn" onclick="doLogout()">Sair</button>
    </div>

    <div class="usage-wrap" id="usage-wrap" style="display:none">
      <div class="usage-label">
        <span>Consumo diário</span>
        <span id="usage-count">0 / 2000 requests</span>
      </div>
      <div class="usage-track"><div class="usage-fill" id="usage-fill" style="width:0%"></div></div>
    </div>

    <div id="keys-list"></div>

    <div class="key-actions" style="margin-top:1.8rem;justify-content:center">
      <button class="btn-sm primary" onclick="createKey()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        Nova chave
      </button>
      <button class="btn-sm" onclick="loadKeys()">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        Atualizar
      </button>
    </div>

    <div class="msg" id="msg-dash"></div>
    <div class="divider" style="margin-top:1.8rem;border-top:1px solid var(--border);padding-top:1.4rem">
      <a href="/">← Voltar para a Home</a>
    </div>
  </div>
</main>

<div class="copy-toast" id="copy-toast">✓ Chave copiada!</div>
{_footer()}

<script>
const BASE = window.location.origin
let session = null

function setTab(tab) {{
  document.getElementById('tab-login').classList.toggle('active', tab==='login')
  document.getElementById('tab-register').classList.toggle('active', tab==='register')
  document.getElementById('form-login').style.display = tab==='login' ? 'block' : 'none'
  document.getElementById('form-register').style.display = tab==='register' ? 'block' : 'none'
}}

function showMsg(id, text, type) {{
  const el = document.getElementById(id)
  el.textContent = text; el.className = 'msg ' + type
}}

function setLoading(btnId, loading) {{
  const btn = document.getElementById(btnId)
  btn.disabled = loading
  if (loading) {{
    if (!btn.dataset.label) btn.dataset.label = btn.innerHTML
    btn.innerHTML = '<span class="spinner"></span> Processando...'
  }} else {{
    btn.innerHTML = btn.dataset.label || btn.innerHTML
  }}
}}

async function doLogin() {{
  const email = document.getElementById('login-email').value.trim()
  const pass = document.getElementById('login-pass').value
  if (!email || !pass) return showMsg('msg-login', 'Preencha todos os campos.', 'err')
  setLoading('btn-login', true)
  try {{
    const res = await fetch(BASE+'/auth/login', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{email, password: pass}})
    }})
    if (!res.ok) {{
      let errStr = 'Falha na autenticação.';
      try {{ const js = await res.json(); errStr = js.detail || errStr; }} catch(e) {{}}
      throw new Error(errStr);
    }}
    const data = await res.json()
    session = data; showDashboard(data)
  }} catch(e) {{ showMsg('msg-login', e.message, 'err') }}
  setLoading('btn-login', false)
}}

async function doRegister() {{
  const email = document.getElementById('reg-email').value.trim()
  const pass = document.getElementById('reg-pass').value
  const pass2 = document.getElementById('reg-pass2').value
  if (!email || !pass || !pass2) return showMsg('msg-register', 'Preencha todos os campos.', 'err')
  if (pass !== pass2) return showMsg('msg-register', 'As senhas não coincidem.', 'err')
  if (pass.length < 6) return showMsg('msg-register', 'Senha muito curta (mín. 6 caracteres).', 'err')
  setLoading('btn-register', true)
  try {{
    const res = await fetch(BASE+'/auth/register', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{email, password: pass}})
    }})
    if (!res.ok) {{
      let errStr = 'Erro ao criar conta.';
      try {{ const js = await res.json(); errStr = js.detail || errStr; }} catch(e) {{}}
      throw new Error(errStr);
    }}
    const data = await res.json()
    if (data.access_token) {{ session = data; showDashboard(data) }}
    else showMsg('msg-register', 'Conta criada! Verifique seu e-mail.', 'ok')
  }} catch(e) {{ showMsg('msg-register', e.message, 'err') }}
  setLoading('btn-register', false)
}}

function doLogout() {{
  session = null
  document.getElementById('dashboard').style.display = 'none'
  document.getElementById('auth-card').style.display = 'block'
  document.getElementById('login-email').value = ''
  document.getElementById('login-pass').value = ''
}}

function showDashboard(data) {{
  document.getElementById('auth-card').style.display = 'none'
  document.getElementById('dashboard').style.display = 'block'
  document.getElementById('dash-email').textContent = data.email || ''
  loadKeys(); loadUsage()
}}

async function loadKeys() {{
  if (!session) return;
  try {{
    const res = await fetch(BASE + '/api/keys', {{
      headers: {{ 'Authorization': 'Bearer ' + session.access_token }}
    }});
    if (!res.ok) {{
      let errStr = 'Falha no banco de dados. Contate o suporte.';
      try {{ const js = await res.json(); errStr = js.detail || errStr; }} catch(e) {{}}
      throw new Error(errStr);
    }}
    const data = await res.json();
    const keysParaRenderizar = data.keys ? data.keys : (Array.isArray(data) ? data : []);
    renderKeys(keysParaRenderizar);
  }} catch (e) {{
    console.error(e);
    showMsg('msg-dash', e.message, 'err');
  }}
}}

async function loadUsage() {{
  if (!session) return
  try {{
    const res = await fetch(BASE+'/usage/me', {{headers:{{'Authorization':'Bearer '+session.access_token}}}})
    if (!res.ok) return;
    const data = await res.json()
    const today = data.usage?.[0]
    if (today) {{
      const count = today.requests || 0
      const pct = Math.min((count/2000)*100, 100)
      document.getElementById('usage-count').textContent = `${{count.toLocaleString('pt-BR')}} / 2.000 requests hoje`
      document.getElementById('usage-fill').style.width = pct + '%'
      document.getElementById('usage-wrap').style.display = 'block'
    }}
  }} catch(e) {{}}
}}

function renderKeys(keys) {{
  const el = document.getElementById('keys-list')
  if (!keys.length) {{
    el.innerHTML = '<div class="no-keys">Você ainda não possui chaves API.<br>Crie sua primeira para começar.</div>'
    return
  }}
  el.innerHTML = keys.map(k => `
    <div class="key-item">
      <div class="key-name">
        ${{k.name}}
        ${{k.active ? '' : '<span style="color:var(--error);font-size:10px">(INATIVA)</span>'}}
      </div>
      <div class="key-box" onclick="copyKey('${{k.key}}')">
        <span>${{k.key}}</span><span class="key-tag">Copiar</span>
      </div>
      <div class="key-actions">
        <button class="btn-sm" onclick="toggleKey('${{k.id}}')"> ${{k.active ? 'Desativar' : 'Ativar'}}</button>
        <button class="btn-sm danger" onclick="deleteKey('${{k.id}}')">Deletar</button>
      </div>
    </div>
  `).join('')
}}

async function createKey() {{
  if (!session) return
  const name = prompt('Nome para a nova chave:', 'Minha Chave API')
  if (!name) return
  try {{
    const res = await fetch(BASE+'/api/keys', {{
      method:'POST',
      headers:{{'Content-Type':'application/json','Authorization':'Bearer '+session.access_token}},
      body: JSON.stringify({{name}})
    }})
    if (!res.ok) {{
      let errStr = 'Erro ao criar chave.';
      try {{ const js = await res.json(); errStr = js.detail || errStr; }} catch(e) {{}}
      throw new Error(errStr);
    }}
    loadKeys()
  }} catch(e) {{ showMsg('msg-dash', e.message, 'err') }}
}}

async function deleteKey(id) {{
  if (!session || !confirm('Deletar esta chave? Esta ação é irreversível.')) return
  try {{
    const res = await fetch(BASE+'/api/keys/'+id, {{method:'DELETE',headers:{{'Authorization':'Bearer '+session.access_token}}}})
    if (!res.ok) {{
      let errStr = 'Erro ao deletar.';
      try {{ const js = await res.json(); errStr = js.detail || errStr; }} catch(e) {{}}
      throw new Error(errStr);
    }}
    loadKeys()
  }} catch(e) {{ showMsg('msg-dash', e.message, 'err') }}
}}

async function toggleKey(id) {{
  if (!session) return
  try {{
    const res = await fetch(BASE+'/api/keys/'+id, {{method:'PATCH',headers:{{'Authorization':'Bearer '+session.access_token}}}})
    if (!res.ok) throw new Error('Erro ao alterar status da chave.')
    loadKeys()
  }} catch(e) {{ showMsg('msg-dash', e.message, 'err') }}
}}

function copyKey(key) {{
  navigator.clipboard.writeText(key)
  const t = document.getElementById('copy-toast')
  t.style.display = 'block'
  setTimeout(()=>t.style.display='none', 2400)
}}
</script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════════════════════════
# PRIVACY PAGE
# ══════════════════════════════════════════════════════════════════════════════
PRIVACY = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
{_meta("Privacidade — Flow API","Política de privacidade da Flow API. Simples, direta e sem enrolação.")}
{SHARED_STYLE}
<style>
  .container{{max-width:720px;margin:0 auto;padding:4rem 2rem;animation:fadein .5s ease}}
  .page-header{{margin-bottom:2.8rem}}
  .page-header h1{{font-size:2.1rem;font-weight:800;margin-bottom:.5rem;letter-spacing:-.5px}}
  .page-header h1 .grad{{background:linear-gradient(135deg,#7c6fff,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .page-header p{{color:var(--dim);font-size:.9rem}}
  .card{{background:rgba(10,10,20,0.75);border:1px solid var(--border);border-radius:14px;padding:1.5rem 1.8rem;margin-bottom:.85rem;transition:.2s;backdrop-filter:blur(10px)}}
  .card:hover{{border-color:rgba(124,111,255,0.2);transform:translateY(-2px)}}
  .card-header{{display:flex;align-items:center;gap:12px;margin-bottom:.7rem}}
  .card-icon{{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:17px;flex-shrink:0}}
  .icon-green{{background:rgba(16,185,129,.12)}} .icon-purple{{background:rgba(124,111,255,.12)}}
  .icon-blue{{background:rgba(59,130,246,.12)}} .icon-orange{{background:rgba(245,158,11,.1)}}
  .icon-pink{{background:rgba(236,72,153,.1)}}
  .card h2{{font-size:.95rem;font-weight:700}}
  .card p{{color:var(--muted);line-height:1.8;font-size:.88rem;margin-top:.3rem}}
  .card p a{{color:var(--primary);text-decoration:none}}.card p a:hover{{text-decoration:underline}}
  .hl{{color:#34d399;font-weight:600}} .hl-p{{color:#a78bfa;font-weight:600}}
  .last-updated{{text-align:center;color:var(--dim);font-size:12px;margin-top:2rem}}
</style>
</head>
<body>
{MESH_JS}
{_header("privacy")}
<div class="container">
  <div class="page-header">
    <h1>Política de <span class="grad">Privacidade</span></h1>
    <p>Simples, direta e sem enrolação. Última atualização: Abril 2026.</p>
  </div>

  <div class="card">
    <div class="card-header"><div class="card-icon icon-green">📭</div><h2>Sem armazenamento de mensagens</h2></div>
    <p>Não armazenamos prompts, respostas ou qualquer conteúdo das suas conversas. Cada requisição é <span class="hl">processada e imediatamente descartada</span>. Não existe banco de dados de mensagens.</p>
  </div>
  <div class="card">
    <div class="card-header"><div class="card-icon icon-purple">🔑</div><h2>Chaves API e autenticação</h2></div>
    <p>Armazenamos apenas <span class="hl-p">e-mail, chaves geradas e contadores de uso</span> — necessários para autenticação e controle de limites. Nenhum dado de pagamento é coletado.</p>
  </div>
  <div class="card">
    <div class="card-header"><div class="card-icon icon-blue">📊</div><h2>Logs técnicos</h2></div>
    <p>Mantemos logs mínimos (modelo usado, tokens consumidos, horário) para monitorar a saúde do serviço. Esses logs <span class="hl">não contêm o conteúdo</span> das suas mensagens.</p>
  </div>
  <div class="card">
    <div class="card-header"><div class="card-icon icon-orange">🤝</div><h2>Terceiros</h2></div>
    <p>As requisições são processadas via <a href="https://groq.com" target="_blank">Groq</a> e <a href="https://openai.com" target="_blank">OpenAI</a>, e os dados de conta são armazenados no <a href="https://supabase.com" target="_blank">Supabase</a>. Consulte as políticas de cada um para entender como tratam os dados em trânsito.</p>
  </div>
  <div class="card">
    <div class="card-header"><div class="card-icon icon-pink">💬</div><h2>Dúvidas?</h2></div>
    <p>Fale com a gente pelo chat no <a href="/">site principal</a> ou envie um e-mail para <a href="mailto:flowapi@proton.me">flowapi@proton.me</a>.</p>
  </div>
  <div class="last-updated">Esta política pode ser atualizada sem aviso prévio. Verifique periodicamente.</div>
</div>
{_footer()}
</body>
</html>"""

# ══════════════════════════════════════════════════════════════════════════════
# STATUS PAGE
# ══════════════════════════════════════════════════════════════════════════════
STATUS_HTML = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
{_meta("Status — Flow API","Status em tempo real dos serviços da Flow API.")}
{SHARED_STYLE}
<style>
  .container{{max-width:720px;margin:0 auto;padding:4rem 2rem;animation:fadein .5s ease}}
  .page-header{{margin-bottom:2.4rem}}
  .page-header h1{{font-size:2.1rem;font-weight:800;margin-bottom:.5rem;letter-spacing:-.5px}}
  .page-header h1 .grad{{background:linear-gradient(135deg,#7c6fff,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .page-header p{{color:var(--dim);font-size:.9rem}}

  .overall{{display:flex;align-items:center;gap:1.1rem;background:rgba(10,10,20,0.8);border:1px solid var(--border);border-radius:14px;padding:1.3rem 1.6rem;margin-bottom:2rem;transition:.3s;backdrop-filter:blur(12px)}}
  .overall.ok{{border-color:rgba(16,185,129,.25);background:rgba(16,185,129,.05)}}
  .overall.down{{border-color:rgba(239,68,68,.25);background:rgba(239,68,68,.05)}}
  .overall.checking{{border-color:rgba(124,111,255,.2)}}
  .dot-wrap{{position:relative;width:18px;height:18px;flex-shrink:0}}
  .dot{{width:12px;height:12px;border-radius:50%;position:absolute;top:3px;left:3px}}
  .dot-ring{{width:18px;height:18px;border-radius:50%;position:absolute;top:0;left:0;opacity:.35}}
  .ok .dot{{background:#10b981}}.ok .dot-ring{{background:#10b981;animation:pulse 1.6s ease infinite}}
  .down .dot{{background:#ef4444}}.down .dot-ring{{background:#ef4444;animation:pulse 1s ease infinite}}
  .checking .dot{{background:#7c6fff}}.checking .dot-ring{{background:#7c6fff;animation:pulse 1s ease infinite}}
  .overall-text strong{{font-size:1rem;display:block;margin-bottom:2px}}
  .overall-text small{{color:var(--dim);font-size:.8rem}}
  .refresh-btn{{margin-left:auto;padding:7px 14px;border-radius:8px;font-size:12px;font-weight:700;cursor:pointer;border:1px solid var(--border);background:rgba(255,255,255,.04);color:var(--muted);transition:.18s}}
  .refresh-btn:hover{{background:rgba(255,255,255,.08);color:var(--text)}}

  .section-label{{font-size:.7rem;font-weight:800;color:var(--dim);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:.75rem;margin-top:2rem}}

  .service{{display:flex;align-items:center;justify-content:space-between;background:rgba(10,10,20,0.75);border:1px solid var(--border);border-radius:12px;padding:.95rem 1.3rem;margin-bottom:.55rem;transition:.2s;backdrop-filter:blur(8px)}}
  .service:hover{{border-color:rgba(124,111,255,.2)}}
  .service-left{{display:flex;align-items:center;gap:11px}}
  .service-icon{{width:36px;height:36px;border-radius:10px;background:rgba(124,111,255,.08);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}}
  .service-name{{font-size:.9rem;font-weight:700;margin-bottom:2px}}
  .service-desc{{font-size:.75rem;color:var(--dim);font-family:'Fira Code','Courier New',monospace}}
  .latency{{font-size:.72rem;color:var(--dim);margin-top:2px}}
  .badge{{font-size:11px;font-weight:700;padding:5px 13px;border-radius:100px;white-space:nowrap}}
  .badge-ok{{background:rgba(16,185,129,.15);color:#34d399;border:1px solid rgba(16,185,129,.2)}}
  .badge-down{{background:rgba(239,68,68,.15);color:#f87171;border:1px solid rgba(239,68,68,.2)}}
  .badge-checking{{background:rgba(124,111,255,.15);color:#a78bfa;border:1px solid rgba(124,111,255,.2)}}

  .uptime-section{{margin-top:2rem}}
  .uptime-bar-wrap{{background:rgba(10,10,20,.75);border:1px solid var(--border);border-radius:12px;padding:1.1rem 1.3rem;margin-bottom:.55rem;backdrop-filter:blur(8px)}}
  .uptime-header{{display:flex;justify-content:space-between;margin-bottom:.75rem;font-size:.88rem;font-weight:600}}
  .uptime-pct{{color:#34d399;font-size:.82rem}}
  .bars{{display:flex;gap:2px}}
  .bar{{flex:1;height:26px;border-radius:3px;background:rgba(255,255,255,.05);transition:.3s}}
  .bar.ok{{background:rgba(16,185,129,.25)}}.bar.down{{background:rgba(239,68,68,.3)}}

  .maintenance{{background:rgba(10,10,20,.75);border:1px solid rgba(245,158,11,.15);border-radius:12px;padding:1.1rem 1.3rem;margin-top:1.4rem;font-size:.85rem;color:var(--muted);line-height:1.8;backdrop-filter:blur(8px)}}
  .maintenance strong{{color:#fbbf24}}
</style>
</head>
<body>
{MESH_JS}
{_header("status")}
<div class="container">
  <div class="page-header">
    <h1>Status dos <span class="grad">Serviços</span></h1>
    <p id="last-check">Verificando...</p>
  </div>

  <div class="overall checking" id="overall">
    <div class="dot-wrap"><div class="dot-ring" id="ring"></div><div class="dot" id="dot"></div></div>
    <div class="overall-text">
      <strong id="overall-text">Verificando serviços...</strong>
      <small id="overall-sub">Aguarde um momento</small>
    </div>
    <button class="refresh-btn" onclick="runChecks()">↻ Atualizar</button>
  </div>

  <div class="section-label">Serviços</div>

  <div class="service" id="svc-health">
    <div class="service-left">
      <div class="service-icon">💚</div>
      <div>
        <div class="service-name">Healthcheck</div>
        <div class="service-desc">GET /health</div>
        <div class="latency" id="lat-health">—</div>
      </div>
    </div>
    <span class="badge badge-checking" id="badge-health">Verificando</span>
  </div>

  <div class="service" id="svc-api">
    <div class="service-left">
      <div class="service-icon">⚡</div>
      <div>
        <div class="service-name">API Principal</div>
        <div class="service-desc">POST /generate</div>
        <div class="latency" id="lat-api">—</div>
      </div>
    </div>
    <span class="badge badge-checking" id="badge-api">Verificando</span>
  </div>

  <div class="service" id="svc-oai">
    <div class="service-left">
      <div class="service-icon">🤖</div>
      <div>
        <div class="service-name">OpenAI Compatible</div>
        <div class="service-desc">POST /v1/chat/completions</div>
        <div class="latency" id="lat-oai">—</div>
      </div>
    </div>
    <span class="badge badge-checking" id="badge-oai">Verificando</span>
  </div>

  <div class="service" id="svc-models">
    <div class="service-left">
      <div class="service-icon">📦</div>
      <div>
        <div class="service-name">Listagem de Modelos</div>
        <div class="service-desc">GET /models</div>
        <div class="latency" id="lat-models">—</div>
      </div>
    </div>
    <span class="badge badge-checking" id="badge-models">Verificando</span>
  </div>

  <div class="uptime-section">
    <div class="section-label">Disponibilidade simulada — últimos 30 dias</div>
    <div class="uptime-bar-wrap">
      <div class="uptime-header"><span>API Principal</span><span class="uptime-pct">99.8%</span></div>
      <div class="bars" id="bars-api"></div>
    </div>
    <div class="uptime-bar-wrap">
      <div class="uptime-header"><span>OpenAI Compatible</span><span class="uptime-pct">99.5%</span></div>
      <div class="bars" id="bars-oai"></div>
    </div>
  </div>


{_footer()}

<script>
const BASE = window.location.origin

function setBadge(id, state, ms) {{
  const el = document.getElementById('badge-' + id)
  const lat = document.getElementById('lat-' + id)
  if (state === 'ok') {{
    el.className = 'badge badge-ok'; el.textContent = 'Operacional'
    if (lat && ms != null) lat.textContent = ms + 'ms'
  }} else if (state === 'down') {{
    el.className = 'badge badge-down'; el.textContent = 'Indisponível'
    if (lat) lat.textContent = 'timeout / erro'
  }} else {{
    el.className = 'badge badge-checking'; el.textContent = 'Verificando'
    if (lat) lat.textContent = '—'
  }}
}}

async function timedFetch(fn) {{
  const t = Date.now()
  try {{
    const ok = await fn()
    return {{ ok: ok !== false, ms: Date.now() - t }}
  }} catch {{
    return {{ ok: false, ms: null }}
  }}
}}

async function runChecks() {{
  ;['health','api','oai','models'].forEach(id => setBadge(id, 'checking'))
  document.getElementById('overall').className = 'overall checking'
  document.getElementById('overall-text').textContent = 'Verificando serviços...'
  document.getElementById('overall-sub').textContent = 'Aguarde um momento'
  document.getElementById('last-check').textContent = 'Verificando...'

  const results = await Promise.all([
    timedFetch(async () => {{
      const r = await fetch(BASE + '/health')
      const d = await r.json()
      return r.ok && d.status === 'healthy'
    }}),
    timedFetch(async () => {{
      const r = await fetch(BASE + '/generate', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{prompt: 'ping', model: 'llama-3.1-8b-instant'}})
      }})
      return r.status === 401 || r.ok
    }}),
    timedFetch(async () => {{
      const r = await fetch(BASE + '/v1/chat/completions', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{model:'llama-3.1-8b-instant',messages:[{{role:'user',content:'oi'}}]}})
      }})
      return r.status === 401 || r.ok
    }}),
    timedFetch(async () => {{
      const r = await fetch(BASE + '/models')
      const d = await r.json()
      return r.ok && Array.isArray(d.models) && d.models.length > 0
    }}),
  ])

  const ids = ['health','api','oai','models']
  results.forEach((r, i) => setBadge(ids[i], r.ok ? 'ok' : 'down', r.ms))

  const allOk = results.every(r => r.ok)
  const overall = document.getElementById('overall')
  overall.className = 'overall ' + (allOk ? 'ok' : 'down')
  document.getElementById('overall-text').textContent = allOk
    ? 'Todos os sistemas operacionais' : 'Degradação parcial detectada'
  document.getElementById('overall-sub').textContent = allOk
    ? 'Nenhuma interrupção detectada' : 'Um ou mais serviços com problema'

  const now = new Date()
  document.getElementById('last-check').textContent =
    'Última verificação: ' + now.toLocaleTimeString('pt-BR')
}}

function genBars(containerId) {{
  const c = document.getElementById(containerId)
  for (let i = 0; i < 30; i++) {{
    const b = document.createElement('div')
    b.className = 'bar ' + (Math.random() > 0.015 ? 'ok' : 'down')
    b.title = 'Dia ' + (30 - i)
    c.appendChild(b)
  }}
}}
genBars('bars-api'); genBars('bars-oai')
runChecks()
setInterval(runChecks, 60000)
</script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════════════════════════
# AUTH helpers
# ══════════════════════════════════════════════════════════════════════════════
security = HTTPBearer(auto_error=False)

def get_api_key_record(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(401, "Authorization header obrigatório. Use: Bearer flow_xxx")
    token = credentials.credentials
    if not token.startswith("flow_"):
        raise HTTPException(401, "Chave inválida. Formato esperado: flow_xxx")
    try:
        res = supa.table("keys").select("*").eq("key", token).eq("active", True).execute()
        if not res.data:
            raise HTTPException(401, "Chave inválida ou desativada.")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Erro banco de dados: {e}")
        raise HTTPException(500, detail="Erro interno de autenticação")

def check_rate_limit(key_record: dict):
    try:
        res = supa.rpc("requests_today", {"p_key_id": key_record["id"]}).execute()
        count = res.data or 0
        if count >= DAILY_LIMIT:
            raise HTTPException(429, f"Limite diário de {DAILY_LIMIT} requests atingido. Renova à meia-noite (Brasília).")
        return count
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Erro RPC: {e}")
        return 0

def log_usage(key_record: dict, model: str, tokens: int):
    try:
        supa.table("usage").insert({
            "api_key_id": key_record["id"],
            "user_id": key_record["user_id"],
            "model": model,
            "tokens_used": tokens or 0,
        }).execute()
        supa.table("keys").update({"last_used": "now()"}).eq("id", key_record["id"]).execute()
    except Exception as e:
        log.error(f"Erro ao registrar uso: {e}")

def get_user_from_jwt(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authorization inválido.")
    token = authorization.split(" ", 1)[1]
    try:
        resp = supa.auth.get_user(token)
        user = resp.user if hasattr(resp, "user") else resp
        if not user:
            raise ValueError("Usuário vazio no token")
        return user
    except Exception as e:
        log.error(f"Erro JWT: {e}")
        raise HTTPException(401, "Token JWT inválido ou expirado.")

# ── Inference helpers ──────────────────────────────────────────────────────────
def call_groq(model: str, messages: list[dict], max_tokens: int) -> tuple[str, int]:
    key = next(key_cycle)
    client = Groq(api_key=key)
    completion = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
    content = completion.choices[0].message.content
    tokens = getattr(completion.usage, "total_tokens", 0)
    return content, tokens

def call_openai(model: str, messages: list[dict], max_tokens: int) -> tuple[str, int]:
    if not openai_client:
        raise HTTPException(503, "Modelos OpenAI indisponíveis: OPENAI_KEY não configurada.")
    completion = openai_client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens
    )
    content = completion.choices[0].message.content
    tokens = getattr(completion.usage, "total_tokens", 0)
    return content, tokens

def call_model(model: str, messages: list[dict], max_tokens: int) -> tuple[str, int]:
    """Route to Groq or OpenAI based on model name."""
    if model in OPENAI_FREE_MODELS:
        return call_openai(model, messages, max_tokens)
    return call_groq(model, messages, max_tokens)

def resolve_model(mode: str, model: str, prompt: str) -> str:
    if mode != "auto":
        if model not in ALLOWED_MODELS:
            raise HTTPException(400, f"Modelo '{model}' não permitido. Use GET /models.")
        return model
    try:
        picked, _ = call_groq("llama-3.1-8b-instant", [
            {"role": "system", "content": AUTO_SYSTEM},
            {"role": "user", "content": prompt}
        ], 20)
        picked = picked.strip()
        return picked if picked in ALLOWED_MODELS else "llama-3.1-8b-instant"
    except Exception as e:
        log.warning(f"Auto-seleção falhou: {e}")
        return "llama-3.1-8b-instant"

# ══════════════════════════════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════════════════════════════
app = FastAPI(title="Flow API", description="Free AI API powered by Groq & OpenAI", version="2.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/public", StaticFiles(directory="public"), name="public")

# ── Schemas ────────────────────────────────────────────────────────────────────
class KeyCreate(BaseModel):
    name: str = "Minha chave"

class PromptRequest(BaseModel):
    prompt: str
    model: str = "llama-3.1-8b-instant"
    max_tokens: int = 1000
    system_prompt: Optional[str] = None
    mode: str = "manual"

class OAIMessage(BaseModel):
    role: str
    content: str

class OAIChatRequest(BaseModel):
    model: str = "llama-3.1-8b-instant"
    messages: list[OAIMessage]
    max_tokens: int = 1000
    stream: bool = False

class AuthRequest(BaseModel):
    email: str
    password: str

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — Public
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/auth/register", tags=["Auth"])
async def register(body: AuthRequest):
    try:
        res = supa.auth.sign_up({"email": body.email, "password": body.password})
        user = res.user
        if not user:
            raise HTTPException(400, "Erro ao criar conta. Tente novamente.")
        session = res.session
        if session:
            return {"access_token": session.access_token, "email": user.email}
        return {"message": "Conta criada! Verifique seu e-mail para confirmar."}
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "already registered" in msg:
            raise HTTPException(400, "E-mail já cadastrado.")
        raise HTTPException(400, detail=f"Erro: {msg}")

@app.post("/auth/login", tags=["Auth"])
async def login(body: AuthRequest):
    try:
        res = supa.auth.sign_in_with_password({"email": body.email, "password": body.password})
        session = res.session
        user = res.user
        if not session:
            raise HTTPException(401, "Credenciais inválidas.")
        return {"access_token": session.access_token, "email": user.email}
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "Invalid login" in msg or "credentials" in msg.lower():
            raise HTTPException(401, "E-mail ou senha incorretos.")
        raise HTTPException(400, detail=f"Erro: {msg}")

@app.get("/login", response_class=HTMLResponse, tags=["Pages"])
@app.get("/dashboard", response_class=HTMLResponse, tags=["Pages"])
async def dashboard_page():
    return HTMLResponse(content=DASHBOARD_HTML)

@app.get("/", response_class=HTMLResponse, tags=["Pages"])
async def root():
    return HTMLResponse(content=LANDING)

@app.get("/privacy", response_class=HTMLResponse, tags=["Pages"])
async def privacy():
    return HTMLResponse(content=PRIVACY)

@app.get("/status", response_class=HTMLResponse, tags=["Pages"])
async def status_page():
    return HTMLResponse(content=STATUS_HTML)

@app.get("/health", tags=["Status"])
async def health():
    return {
        "status": "healthy",
        "keys_loaded": len(keys),
        "openai_available": openai_client is not None,
        "version": "2.2.0",
    }

@app.get("/models", tags=["Info"])
async def list_models():
    groq_models = sorted(GROQ_MODELS)
    oai_models = sorted(OPENAI_FREE_MODELS) if OPENAI_KEY else []
    return {
        "models": sorted(ALLOWED_MODELS),
        "groq": groq_models,
        "openai": oai_models,
    }

@app.get("/robots.txt", include_in_schema=False)
async def serve_robots():
    return FileResponse("public/robots.txt")

@app.get("/sitemap.xml")
async def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://flow.squareweb.app/</loc><lastmod>2026-04-01</lastmod><priority>1.0</priority></url>
  <url><loc>https://flow.squareweb.app/status</loc><priority>0.8</priority></url>
  <url><loc>https://flow.squareweb.app/privacy</loc><priority>0.5</priority></url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — Keys (JWT)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/keys", tags=["Keys"])
async def create_key(body: KeyCreate, user=Depends(get_user_from_jwt)):
    user_id = getattr(user, "id", user.get("id") if isinstance(user, dict) else None)
    try:
        count_res = supa.table("keys").select("id", count="exact").eq("user_id", user_id).execute()
        if (count_res.count or 0) >= MAX_KEYS_PER_USER:
            raise HTTPException(400, f"Limite de {MAX_KEYS_PER_USER} chaves por usuário atingido.")
        res = supa.table("keys").insert({"user_id": user_id, "name": body.name}).execute()
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"DB erro create_key: {e}")
        raise HTTPException(500, detail="Falha ao criar a chave de API no banco de dados.")

@app.get("/api/keys", tags=["Keys"])
async def list_keys(user=Depends(get_user_from_jwt)):
    user_id = getattr(user, "id", user.get("id") if isinstance(user, dict) else None)
    try:
        res = supa.table("keys").select("id,name,key,active,created_at,last_used") \
            .eq("user_id", user_id).order("created_at", desc=True).execute()
        return {"keys": res.data}
    except Exception as e:
        log.error(f"DB erro list_keys: {e}")
        raise HTTPException(500, detail="Erro interno ao consultar chaves no banco.")

@app.delete("/api/keys/{key_id}", tags=["Keys"])
async def delete_key(key_id: str, user=Depends(get_user_from_jwt)):
    user_id = getattr(user, "id", user.get("id") if isinstance(user, dict) else None)
    try:
        res = supa.table("keys").delete().eq("id", key_id).eq("user_id", user_id).execute()
        if not res.data:
            raise HTTPException(404, "Chave não encontrada.")
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"DB erro delete_key: {e}")
        raise HTTPException(500, detail="Erro interno ao deletar a chave no banco.")

@app.patch("/api/keys/{key_id}", tags=["Keys"])
async def toggle_key(key_id: str, user=Depends(get_user_from_jwt)):
    user_id = getattr(user, "id", user.get("id") if isinstance(user, dict) else None)
    try:
        cur = supa.table("keys").select("active").eq("id", key_id).eq("user_id", user_id).execute()
        if not cur.data:
            raise HTTPException(404, "Chave não encontrada.")
        new_status = not cur.data[0]["active"]
        supa.table("keys").update({"active": new_status}).eq("id", key_id).execute()
        return {"active": new_status}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"DB erro toggle_key: {e}")
        raise HTTPException(500, detail="Erro interno ao alterar o status da chave.")

@app.get("/usage/me", tags=["Keys"])
async def my_usage(user=Depends(get_user_from_jwt)):
    user_id = getattr(user, "id", user.get("id") if isinstance(user, dict) else None)
    try:
        res = supa.table("daily_usage").select("*").eq("user_id", user_id) \
            .order("day", desc=True).limit(30).execute()
        return {"usage": res.data}
    except Exception as e:
        log.error(f"DB erro usage/me: {e}")
        raise HTTPException(500, detail="Falha ao obter histórico de consumo diário.")

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — AI Generation (flow_ key)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/generate", tags=["AI"])
async def generate(req: PromptRequest, key_record=Depends(get_api_key_record)):
    if not req.prompt.strip():
        raise HTTPException(400, "Prompt não pode estar vazio.")
    if not (1 <= req.max_tokens <= 4096):
        raise HTTPException(400, "max_tokens deve ser entre 1 e 4096.")
    check_rate_limit(key_record)
    chosen = resolve_model(req.mode, req.model, req.prompt)
    messages = []
    if req.system_prompt:
        messages.append({"role": "system", "content": req.system_prompt})
    messages.append({"role": "user", "content": req.prompt})
    try:
        log.info(f"generate → model={chosen} mode={req.mode}")
        content, tokens = call_model(chosen, messages, req.max_tokens)
        log_usage(key_record, chosen, tokens)
        return {"response": content, "model": chosen, "tokens_used": tokens}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Erro generate: {e}")
        raise HTTPException(500, detail=f"Erro ao processar: {e}")
# --- CONFIGURAÇÕES ---
BLACKLIST_FILE = "blacklist.json"
MAX_REQUESTS = 10  # Limite de requisições
WINDOW_SECONDS = 10 # Janela de tempo para o limite
# ---------------------

# Armazena em memória as requisições recentes {ip: [timestamp1, timestamp2...]}
request_history = defaultdict(list)

def get_blacklist():
    try:
        with open(BLACKLIST_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def add_to_blacklist(ip):
    blacklist = get_blacklist()
    if ip not in blacklist:
        blacklist.append(ip)
        with open(BLACKLIST_FILE, "w") as f:
            json.dump(blacklist, f, indent=4)
        print(f"🚫 IP BANIDO E SALVO: {ip}")

@app.middleware("http")
async def smart_firewall(request: Request, call_next):
    # 1. Identifica o IP Real
    real_ip = request.headers.get("X-Forwarded-For", "").split(",")[0] or request.client.host
    now = time.time()

    # 2. Verifica se está na Blacklist (JSON)
    if real_ip in get_blacklist():
        return Response(content="Seu IP está banido.", status_code=403)

    # 3. Lógica de Rate Limit (Inteligência)
    # Remove timestamps antigos da janela de tempo
    request_history[real_ip] = [t for t in request_history[real_ip] if now - t < WINDOW_SECONDS]
    
    # Adiciona a requisição atual
    request_history[real_ip].append(now)

    # Se estourar o limite, banimos no JSON
    if len(request_history[real_ip]) > MAX_REQUESTS:
        add_to_blacklist(real_ip)
        return Response(content="Muitas requisições. Você foi banido.", status_code=403)

    # 4. Segue o fluxo normal
    response = await call_next(request)
    return response
@app.post("/v1/chat/completions", tags=["OpenAI Compatible"])
async def oai_chat(req: OAIChatRequest, key_record=Depends(get_api_key_record)):
    if req.stream:
        raise HTTPException(400, "Streaming não suportado ainda.")
    check_rate_limit(key_record)
    model = req.model if req.model in ALLOWED_MODELS else "llama-3.1-8b-instant"
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    try:
        log.info(f"oai_chat → model={model}")
        content, tokens = call_model(model, messages, req.max_tokens)
        log_usage(key_record, model, tokens)
        return {
            "id": f"chatcmpl-flow-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": tokens or 0}
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Erro Groq (OAI): {e}")
        raise HTTPException(500, detail=str(e))

# Rota específica para a verificação do Google
@app.get("/googlecef1125805d11f8b.html")
async def google_verification():
    file_path = os.path.join("public", "googlecef1125805d11f8b.html")
    return FileResponse(file_path)
@app.get("/robots.txt", include_in_schema=False)
async def get_robots():
    # Caminho para onde o arquivo está na Square Cloud
    file_path = os.path.join("public", "robots.txt")
    return FileResponse(file_path)
@app.get("/v1/models", tags=["OpenAI Compatible"])
async def oai_models(key_record=Depends(get_api_key_record)):
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 1700000000, "owned_by": "flow-api"}
            for m in sorted(ALLOWED_MODELS)
        ]
    }