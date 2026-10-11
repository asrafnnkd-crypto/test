"""
ZYNK — one file: web page + Python server + downloader.
Deploy: upload this single file to any Python host and run:  python zynk.py
Visitors need NOTHING installed — they just open the site.
Libraries (fastapi, uvicorn, yt-dlp, ffmpeg binary) install themselves on first run.
"""
import importlib.util, os, subprocess, sys

def _boot():
    need = {"fastapi": "fastapi", "uvicorn": "uvicorn[standard]", "yt_dlp": "yt-dlp", "imageio_ffmpeg": "imageio-ffmpeg"}
    miss = [p for m, p in need.items() if importlib.util.find_spec(m) is None]
    if miss:
        print("Installing:", ", ".join(miss), flush=True)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *miss])
_boot()

import asyncio, ipaddress, shutil, socket, tempfile, threading, time, uuid
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import uvicorn
import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from yt_dlp.utils import download_range_func

FFMPEG = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()   # bundled binary if the host has none
TMP = Path(tempfile.gettempdir()) / "zynk"
TMP.mkdir(exist_ok=True)
MAX_CLIP = 3600
TTL = 900
ALLOW_PRIVATE = os.environ.get("ZYNK_ALLOW_PRIVATE") == "1"          # for local testing only
COOKIES = os.environ.get("ZYNK_COOKIES")                              # optional cookies.txt path (helps on datacenter IPs)
AUDIO = [("mp3", "128"), ("mp3", "320"), ("flac", "0"), ("wav", "0")]
VIDEO = [720, 1080, 2160, 4320]

PAGE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#030705">
<title>ZYNK</title>
<style>
@property --p{syntax:'<number>';inherits:true;initial-value:0}
:root{--g:#00ff9d;--c:#00c2ff;--bg:#030705;--panel:rgba(10,26,19,.6);--line:rgba(0,255,157,.2);--tx:#e8fff5;--mut:#80a899}
*{box-sizing:border-box;margin:0;-webkit-tap-highlight-color:transparent}
html{scroll-padding-top:env(safe-area-inset-top,0px)}
body{background:var(--bg);color:var(--tx);font-family:Tajawal,"Segoe UI",system-ui,sans-serif;min-height:100dvh;overflow-x:hidden;padding:env(safe-area-inset-top,0px) env(safe-area-inset-right,0px) env(safe-area-inset-bottom,0px) env(safe-area-inset-left,0px)}
#bg{position:fixed;inset:0;width:100%;height:100%;z-index:-1}
body:before{content:"";position:fixed;inset:0;z-index:-1;background:radial-gradient(70% 45% at 50% 0,rgba(0,255,157,.14),transparent),radial-gradient(40% 40% at 100% 100%,rgba(0,194,255,.1),transparent)}
header{display:flex;justify-content:space-between;align-items:center;padding:16px clamp(14px,5vw,56px)}
.logo{font:800 24px/1 system-ui;letter-spacing:.32em;direction:ltr}.logo i{color:var(--g);font-style:normal;text-shadow:0 0 18px var(--g)}
.lang{min-height:44px;min-width:56px;border:1px solid var(--line);border-radius:99px;background:var(--panel);backdrop-filter:blur(12px);color:var(--g);font:700 14px system-ui;cursor:pointer}
main{max-width:960px;margin:0 auto;padding:0 14px 48px}
.hero{text-align:center;padding:3vh 0 24px}
h1{font-size:clamp(32px,8vw,66px);line-height:1.15;font-weight:800}
h1 span{background:linear-gradient(100deg,var(--g),var(--c),var(--g));background-size:200%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:flow 6s linear infinite}
@keyframes flow{to{background-position:200%}}
.hero p{color:var(--mut);margin:14px auto 0;max-width:540px;font-size:clamp(15px,3.8vw,17px);line-height:1.7}
.card{position:relative;border:1px solid var(--line);border-radius:28px;background:var(--panel);backdrop-filter:blur(20px);padding:clamp(14px,3vw,28px);overflow:clip;box-shadow:0 30px 80px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.05)}
.card:before{content:"";position:absolute;inset:0;background:radial-gradient(260px circle at var(--mx,50%) var(--my,0),rgba(0,255,157,.15),transparent 70%);pointer-events:none}
.card>*{position:relative}
.bar{display:flex;gap:8px;background:rgba(0,0,0,.5);border:1px solid var(--line);border-radius:20px;padding:6px}
.bar:focus-within{box-shadow:0 0 0 2px rgba(0,255,157,.4),0 0 40px rgba(0,255,157,.15)}
input{flex:1;min-width:0;background:none;border:0;outline:0;color:var(--tx);font:16px system-ui;padding:12px;direction:ltr;text-align:start}
button{font-family:inherit;cursor:pointer}
.go{min-height:48px;background:linear-gradient(135deg,var(--g),#00d17a);color:#001a0e;border:0;border-radius:15px;padding:0 24px;font-weight:800;font-size:16px;transition:.2s}
.go:active{transform:scale(.96)}
.plats{display:flex;gap:8px;margin-top:14px;overflow-x:auto;scrollbar-width:none;padding-bottom:2px;justify-content:center;flex-wrap:wrap}
.plat{font-size:13px;padding:6px 14px;border-radius:99px;border:1px solid var(--line);color:var(--mut);transition:.25s;white-space:nowrap}
.plat.on{color:#001a0e;background:var(--g);border-color:var(--g);box-shadow:0 0 20px rgba(0,255,157,.5)}
#studio{display:none;margin-top:20px}#studio.show{display:block;animation:rise .6s cubic-bezier(.2,.9,.2,1)}
@keyframes rise{from{opacity:0;transform:translateY(28px) scale(.97)}}
.meta{display:flex;gap:14px;align-items:center;margin-bottom:16px}
.thumb{width:96px;aspect-ratio:16/10;border-radius:16px;flex:none;object-fit:cover;background:conic-gradient(from 200deg,var(--g),var(--c),#052,var(--g));box-shadow:0 0 36px rgba(0,255,157,.25)}
.meta h3{font-size:17px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.meta small{color:var(--mut)}
.wave{direction:ltr;height:130px;border-radius:20px;background:rgba(0,0,0,.45);border:1px solid var(--line);touch-action:pan-y;cursor:ew-resize}
canvas#wv{width:100%;height:100%;display:block}
.times{display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:end;margin:14px 0;direction:ltr}
.times label{font-size:12px;color:var(--mut);display:block;margin-bottom:6px}
.times input{width:100%;text-align:center;background:rgba(0,0,0,.5);border:1px solid var(--line);border-radius:14px;font:700 22px ui-monospace,monospace;color:var(--g)}
.len{text-align:center;padding-bottom:10px}.len b{display:block;font:800 22px ui-monospace,monospace}.len small{color:var(--mut);font-size:12px}
.row{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.seg{display:flex;border:1px solid var(--line);border-radius:15px;overflow:hidden;width:100%}
.seg button{flex:1;background:none;border:0;color:var(--mut);min-height:46px;font-size:15px;transition:.2s}
.seg button.on{background:var(--g);color:#001a0e;font-weight:800}
.q{flex:1 1 70px;min-height:46px;background:none;border:1px solid var(--line);border-radius:13px;color:var(--mut);font-size:14px;transition:.2s}
.q.on{color:var(--g);border-color:var(--g);box-shadow:inset 0 0 18px rgba(0,255,157,.2)}
.dl{display:flex;align-items:center;gap:14px;margin-top:16px;flex-wrap:wrap;position:sticky;bottom:8px;z-index:3}
.dlb{flex:1;min-width:210px;height:60px;border:0;border-radius:20px;font-size:18px;font-weight:800;color:#001a0e;background:linear-gradient(135deg,var(--g),#00d17a);position:relative;overflow:hidden;box-shadow:0 10px 40px rgba(0,255,157,.3)}
.dlb:before{content:"";position:absolute;inset:0;inset-inline-end:auto;width:calc(var(--p)*1%);background:rgba(255,255,255,.4)}
.dlb:disabled{cursor:wait}.dlb span{position:relative}
.est{color:var(--mut);font-size:14px}.est b{color:var(--tx)}
.feat{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:24px}
.feat .card{padding:20px}.feat h4{color:var(--g);margin-bottom:6px;font-size:18px}.feat p{color:var(--mut);font-size:14px;line-height:1.7}
footer{text-align:center;color:var(--mut);font-size:12px;padding:20px 16px}
#toast{position:fixed;bottom:calc(20px + env(safe-area-inset-bottom,0px));left:50%;transform:translate(-50%,140px);background:#0b1f16;border:1px solid var(--g);padding:12px 20px;border-radius:14px;transition:.4s;z-index:9;max-width:90vw;font-size:14px;text-align:center}
#toast.on{transform:translate(-50%,0)}
@media(max-width:560px){.times{grid-template-columns:1fr 1fr}.len{grid-column:1/3;order:-1}.go{padding:0 18px}.thumb{width:78px}
.dl{background:rgba(3,10,7,.85);backdrop-filter:blur(14px);border:1px solid var(--line);border-radius:24px;padding:8px}.est{width:100%;text-align:center;order:-1}.dlb{min-width:0;height:54px}}
@media(prefers-reduced-motion:reduce){*{animation:none!important}}
</style>
</head>
<body>
<canvas id="bg"></canvas>
<header><div class="logo">ZYN<i>K</i></div><button class="lang" id="lang" aria-label="Language"></button></header>
<main>
<section class="hero"><h1 data-i="h1"></h1><p data-i="sub"></p></section>
<section class="card">
  <div class="bar"><input id="url" type="url" inputmode="url" autocomplete="off" autocapitalize="off" spellcheck="false" data-ph="ph"><button class="go" id="an" data-i="an"></button></div>
  <div class="plats" id="plats"></div>
  <div id="studio">
    <div class="meta"><img class="thumb" id="th" alt="" referrerpolicy="no-referrer"><div><h3 id="ttl"></h3><small id="sub2"></small></div></div>
    <div class="wave"><canvas id="wv"></canvas></div>
    <div class="times">
      <div><label data-i="from"></label><input id="ia" value="00:00"></div>
      <div class="len"><b id="ln">01:00</b><small data-i="len"></small></div>
      <div><label data-i="to"></label><input id="ib" value="01:00"></div>
    </div>
    <div class="row"><div class="seg" id="mode"><button data-m="a" class="on" data-i="aud"></button><button data-m="v" data-i="vid"></button></div></div>
    <div class="row" id="qs"></div>
    <div class="dl"><div class="est"><span data-i="size"></span> <b id="sz">—</b></div><button class="dlb" id="dl" style="--p:0"><span id="dlt"></span></button></div>
  </div>
</section>
<section class="feat">
  <div class="card"><h4 data-i="f1t"></h4><p data-i="f1p"></p></div>
  <div class="card"><h4 data-i="f2t"></h4><p data-i="f2p"></p></div>
  <div class="card"><h4 data-i="f3t"></h4><p data-i="f3p"></p></div>
</section>
</main>
<footer data-i="foot"></footer>
<div id="toast" role="status"></div>
<script>
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
/* ---------- i18n: language auto-detected from the browser ---------- */
const T={en:{h1:'Download <span>any audio or video</span><br>exactly the seconds you need',sub:'Paste a link, pick only the part you want from a long video, choose the quality and go.',ph:'Paste a link: https://youtube.com/watch?v=...',an:'Analyze',busy:'Analyzing…',from:'From',to:'To',len:'Clip length',aud:'🎧 Audio',vid:'🎬 Video',size:'Approx. size:',dl:'⬇ Download clip',work:'Trimming & processing…',done:'✔ Ready',f1t:'✂ Second-level trimming',f1p:'50-minute video? Take just one minute. Trimming happens before download, so it is fast and light.',f2t:'⚡ Built for speed',f2p:'Parallel fragment downloads and instant processing on the server.',f3t:'💎 Top quality',f3p:'MP3 320, FLAC and WAV for audio, up to 8K for video when the source has it.',foot:'ZYNK — only download content you own or are allowed to download.',bad:'Paste a valid link first',err:'Something went wrong. Check the link and try again.',max:'Maximum clip length is 60 minutes',other:'Link'},
ar:{h1:'حمّل <span>أي صوت أو فيديو</span><br>بالثواني اللي تحتاجها بالضبط',sub:'ألصق الرابط، اختر الجزء الذي تريده فقط من الفيديو الطويل، حدد الجودة وابدأ.',ph:'ألصق الرابط: https://youtube.com/watch?v=...',an:'حلّل',busy:'…جارٍ التحليل',from:'من',to:'إلى',len:'مدة المقطع',aud:'🎧 صوت',vid:'🎬 فيديو',size:'الحجم التقريبي:',dl:'⬇ حمّل المقطع',work:'…جارٍ القص والمعالجة',done:'✔ جاهز',f1t:'✂ قصّ بالثانية',f1p:'فيديو من 50 دقيقة؟ خذ منه دقيقة واحدة. القص يتم قبل التحميل فيكون سريعاً وخفيفاً.',f2t:'⚡ مصمم للسرعة',f2p:'تحميل متوازٍ للأجزاء ومعالجة فورية على السيرفر.',f3t:'💎 جودة عالية جداً',f3p:'MP3 320 وFLAC وWAV للصوت، وحتى 8K للفيديو إذا كان المصدر يدعمها.',foot:'ZYNK — حمّل فقط المحتوى الذي تملكه أو يُسمح لك بتحميله.',bad:'ألصق رابطاً صحيحاً أولاً',err:'حدث خطأ. تأكد من الرابط وحاول مجدداً.',max:'الحد الأقصى لمدة المقطع 60 دقيقة',other:'رابط'}};
let L=/^ar/i.test((navigator.languages&&navigator.languages[0])||navigator.language||'')?'ar':'en';
try{const s=localStorage.getItem('zl');if(T[s])L=s}catch(e){}
function setLang(l){L=l;try{localStorage.setItem('zl',l)}catch(e){}
 document.documentElement.lang=l;document.documentElement.dir=l=='ar'?'rtl':'ltr';
 $$('[data-i]').forEach(e=>e.innerHTML=T[l][e.dataset.i]);$$('[data-ph]').forEach(e=>e.placeholder=T[l][e.dataset.ph]);
 $('#lang').textContent=l=='ar'?'EN':'عربي';$('#dlt').textContent=T[l].dl;$('#an').textContent=T[l].an;document.title='ZYNK — '+(l=='ar'?'حمّل المستقبل':'Download the future');renderQ()}
$('#lang').onclick=()=>setLang(L=='ar'?'en':'ar');
const toast=m=>{const t=$('#toast');t.textContent=m;t.classList.add('on');clearTimeout(toast.i);toast.i=setTimeout(()=>t.classList.remove('on'),3500)};
const fmt=t=>{t=Math.max(0,Math.round(t));const h=t/3600|0,m=t%3600/60|0,s=t%60,p=n=>String(n).padStart(2,'0');return(h?h+':':'')+p(m)+':'+p(s)};
const parse=v=>{const a=v.trim().split(':').map(Number);return a.some(isNaN)?NaN:a.reduce((x,y)=>x*60+y,0)};
/* ---------- platforms ---------- */
const PL=[['YouTube',/youtu\.?be/],['Facebook',/facebook|fb\.watch/],['TikTok',/tiktok/],['Instagram',/instagram/],['X',/twitter|x\.com/],['SoundCloud',/soundcloud/],['Vimeo',/vimeo/]];
$('#plats').innerHTML=PL.map(p=>`<span class="plat">${p[0]}</span>`).join('')+'<span class="plat">+1000</span>';
const detect=()=>{const v=$('#url').value;$$('.plat').forEach((e,i)=>e.classList.toggle('on',i<PL.length&&PL[i][1].test(v)))};
$('#url').addEventListener('input',detect);
/* ---------- state ---------- */
const S={dur:60,a:0,b:60,mode:'a',q:1,peaks:[]};
const QS={a:[['MP3 128',16],['MP3 320',40],['FLAC',120],['WAV',176]],v:[['720p',250],['1080p',600],['4K',2600],['8K',9000]]};
function renderQ(){$('#qs').innerHTML=QS[S.mode].map((q,i)=>`<button class="q ${i==S.q?'on':''}" data-i="${i}">${q[0]}</button>`).join('');$$('.q').forEach(b=>b.onclick=()=>{S.q=+b.dataset.i;renderQ();upd()})}
$$('#mode button').forEach(b=>b.onclick=()=>{S.mode=b.dataset.m;S.q=1;$$('#mode button').forEach(x=>x.classList.toggle('on',x==b));renderQ();upd()});
/* ---------- timeline ---------- */
const cv=$('#wv'),cx=cv.getContext('2d');
const mkPeaks=s=>{S.peaks=Array.from({length:200},(_,i)=>{const x=i/200;return Math.min(1,.15+Math.abs(Math.sin(x*38+s)*.35+Math.sin(x*11+s*2)*.3+Math.sin(x*97)*.15)+(Math.sin(x*5+s)>.6?.2:0))})};
function draw(){const r=cv.getBoundingClientRect(),d=devicePixelRatio||1;cv.width=r.width*d;cv.height=r.height*d;cx.setTransform(d,0,0,d,0,0);
 const W=r.width,H=r.height,n=S.peaks.length,bw=W/n,xa=S.a/S.dur*W,xb=S.b/S.dur*W,g=cx.createLinearGradient(0,0,0,H);g.addColorStop(0,'#00ff9d');g.addColorStop(1,'#00c2ff');
 S.peaks.forEach((p,i)=>{const x=i*bw,h=p*H*.8,on=x+bw>=xa&&x<=xb;cx.fillStyle=on?g:'rgba(128,168,153,.28)';cx.beginPath();cx.roundRect(x+1,(H-h)/2,Math.max(1,bw-2),h,2);cx.fill()});
 cx.fillStyle='rgba(0,255,157,.08)';cx.fillRect(xa,0,xb-xa,H);
 [xa,xb].forEach(x=>{cx.fillStyle='#00ff9d';cx.shadowColor='#00ff9d';cx.shadowBlur=14;cx.fillRect(x-1.5,0,3,H);cx.beginPath();cx.roundRect(x-10,H/2-22,20,44,10);cx.fill();cx.shadowBlur=0;cx.fillStyle='#001a0e';cx.fillRect(x-1,H/2-9,2,18)})}
let drag=null;
cv.addEventListener('pointerdown',e=>{const r=cv.getBoundingClientRect(),W=r.width,x=e.clientX-r.left,xa=S.a/S.dur*W,xb=S.b/S.dur*W;
 if(Math.abs(x-xa)<24)drag={t:'a'};else if(Math.abs(x-xb)<24)drag={t:'b'};else if(x>xa&&x<xb)drag={t:'m',off:x/W*S.dur-S.a};else drag={t:Math.abs(x-xa)<Math.abs(x-xb)?'a':'b'};
 cv.setPointerCapture(e.pointerId);move(e)});
cv.addEventListener('pointermove',e=>drag&&move(e));cv.addEventListener('pointerup',()=>drag=null);cv.addEventListener('pointercancel',()=>drag=null);
function move(e){const r=cv.getBoundingClientRect(),t=Math.max(0,Math.min(S.dur,(e.clientX-r.left)/r.width*S.dur)),Ln=S.b-S.a;
 if(drag.t=='a')S.a=Math.min(t,S.b-1);else if(drag.t=='b')S.b=Math.max(t,S.a+1);else{S.a=Math.max(0,Math.min(S.dur-Ln,t-drag.off));S.b=S.a+Ln}
 S.a=Math.round(S.a);S.b=Math.round(S.b);upd()}
function upd(){$('#ia').value=fmt(S.a);$('#ib').value=fmt(S.b);$('#ln').textContent=fmt(S.b-S.a);
 const mb=QS[S.mode][S.q][1]*(S.b-S.a)/1024;$('#sz').textContent=mb>1024?(mb/1024).toFixed(1)+' GB':mb.toFixed(1)+' MB';draw()}
['ia','ib'].forEach(id=>$('#'+id).addEventListener('change',()=>{let a=parse($('#ia').value),b=parse($('#ib').value);if(isNaN(a)||isNaN(b))return upd();a=Math.max(0,Math.min(a,S.dur-1));b=Math.max(a+1,Math.min(b,S.dur));S.a=a;S.b=b;upd()}));
addEventListener('resize',()=>$('#studio').classList.contains('show')&&draw());
/* ---------- API ---------- */
const api=async(p,body)=>{const r=await fetch(p,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});if(!r.ok)throw new Error((await r.json().catch(()=>({}))).detail||r.status);return r.json()};
$('#an').onclick=async()=>{const v=$('#url').value.trim();if(!/^https?:\/\/.+\..+/.test(v))return toast(T[L].bad);
 const b=$('#an');b.disabled=true;b.textContent=T[L].busy;
 try{const d=await api('/api/info',{url:v});S.dur=d.duration;S.a=0;S.b=Math.min(60,d.duration);mkPeaks(d.duration%9+1);
  $('#ttl').textContent=d.title||'—';$('#sub2').textContent=(d.site||T[L].other)+' · '+fmt(d.duration);
  if(d.thumb)$('#th').src=d.thumb;else $('#th').removeAttribute('src');
  $('#studio').classList.add('show');renderQ();upd();$('#studio').scrollIntoView({behavior:'smooth',block:'nearest'})}
 catch(e){toast(T[L].err)}b.disabled=false;b.textContent=T[L].an};
$('#url').addEventListener('keydown',e=>e.key=='Enter'&&$('#an').click());
$('#dl').onclick=async()=>{if(S.b-S.a>3600)return toast(T[L].max);
 const btn=$('#dl'),t=$('#dlt');btn.disabled=true;t.textContent=T[L].work;btn.style.setProperty('--p',3);
 try{const {id}=await api('/api/jobs',{url:$('#url').value.trim(),start:S.a,end:S.b,mode:S.mode,q:S.q});
  for(;;){await new Promise(r=>setTimeout(r,700));const s=await api('/api/jobs/'+id);btn.style.setProperty('--p',Math.max(3,s.p));
   if(s.state=='error')throw 0;
   if(s.state=='done'){t.textContent=T[L].done;const a=document.createElement('a');a.href='/api/jobs/'+id+'/file';a.click();break}}}
 catch(e){toast(T[L].err);t.textContent=T[L].dl}
 setTimeout(()=>{btn.disabled=false;btn.style.setProperty('--p',0);t.textContent=T[L].dl},2500)};
/* ---------- live background ---------- */
const bg=$('#bg'),bx=bg.getContext('2d');let mx=.5,my=.5,T0=0;
addEventListener('pointermove',e=>{mx=e.clientX/innerWidth;my=e.clientY/innerHeight;$$('.card').forEach(c=>{const r=c.getBoundingClientRect();c.style.setProperty('--mx',e.clientX-r.left+'px');c.style.setProperty('--my',e.clientY-r.top+'px')})});
function loop(){const d=Math.min(devicePixelRatio||1,2),W=bg.width=innerWidth*d,H=bg.height=innerHeight*d;T0+=.008;bx.clearRect(0,0,W,H);bx.lineWidth=1.2*d;
 for(let k=0;k<12;k++){bx.beginPath();bx.strokeStyle=`rgba(0,255,157,${.05+k*.008})`;
  for(let x=0;x<=W;x+=14*d){const nx=x/W,a=Math.exp(-Math.pow(nx-mx,2)*18)*60*d*(my+.3),y=H*.74+k*14*d+Math.sin(nx*6+T0+k*.35)*26*d+Math.sin(nx*15-T0*1.6+k)*8*d-a*Math.sin(T0*2+k*.3);x?bx.lineTo(x,y):bx.moveTo(x,y)}bx.stroke()}
 if(!document.hidden)requestAnimationFrame(loop);else setTimeout(loop,500)}
if(!matchMedia('(prefers-reduced-motion:reduce)').matches)loop();
setLang(L);
</script>
</body>
</html>
'''

app = FastAPI(title="ZYNK")
app.add_middleware(GZipMiddleware, minimum_size=1024)
JOBS: dict[str, dict] = {}
HITS: dict[str, deque] = defaultdict(deque)


class Info(BaseModel):
    url: str = Field(max_length=500)


class Clip(Info):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    mode: str = Field(pattern="^[av]$")
    q: int = Field(ge=0, le=3)


def ip_of(req: Request):
    return (req.headers.get("x-forwarded-for") or req.client.host).split(",")[0].strip()


def limit(ip: str, n: int = 20, per: int = 60):
    now, q = time.time(), HITS[ip]
    while q and now - q[0] > per:
        q.popleft()
    if len(q) >= n:
        raise HTTPException(429, "Too many requests")
    q.append(now)


def safe_url(url: str):
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise HTTPException(400, "Invalid link")
    if ALLOW_PRIVATE:
        return
    try:
        for *_, addr in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(addr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise HTTPException(400, "Blocked address")
    except socket.gaierror:
        raise HTTPException(400, "Unknown host")


def base_opts():
    o = {"quiet": True, "noplaylist": True, "ffmpeg_location": FFMPEG}
    if COOKIES and Path(COOKIES).exists():
        o["cookiefile"] = COOKIES
    return o


@app.get("/")
async def home():
    return HTMLResponse(PAGE)


@app.get("/health")
async def health():
    return {"ok": True, "yt_dlp": yt_dlp.version.__version__}


@app.post("/api/info")
async def info(r: Info, req: Request):
    limit(ip_of(req))
    await asyncio.to_thread(safe_url, r.url)

    def go():
        with yt_dlp.YoutubeDL(base_opts()) as y:
            d = y.extract_info(r.url, download=False)
        return {"title": d.get("title"), "duration": int(d.get("duration") or 0),
                "thumb": d.get("thumbnail"), "by": d.get("uploader"), "site": d.get("extractor_key")}

    try:
        data = await asyncio.to_thread(go)
    except Exception as e:
        raise HTTPException(400, "Could not read this link: " + str(e)[:160])
    if not data["duration"]:
        raise HTTPException(400, "Live or unknown duration")
    return data


def work(jid: str, c: Clip):
    j, out = JOBS[jid], TMP / jid
    out.mkdir(exist_ok=True)

    def hook(d):
        if d["status"] == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate")
            if tot:
                j["p"] = min(90, d["downloaded_bytes"] / tot * 90)
        elif d["status"] == "finished":
            j["p"] = 92

    o = {**base_opts(), "restrictfilenames": True, "outtmpl": str(out / "%(title).70s.%(ext)s"),
         "progress_hooks": [hook], "download_ranges": download_range_func(None, [(c.start, c.end)]),
         "force_keyframes_at_cuts": True, "concurrent_fragment_downloads": 8}
    if c.mode == "a":
        codec, br = AUDIO[c.q]
        o["format"] = "bestaudio/best"
        o["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": codec, "preferredquality": br}]
    else:
        h = VIDEO[c.q]
        o["format"] = f"bv*[height<={h}]+ba/b[height<={h}]/b"
        o["merge_output_format"] = "mp4"
    try:
        with yt_dlp.YoutubeDL(o) as y:
            y.download([c.url])
        j.update(p=100, state="done", file=str(next(out.iterdir())), t=time.time())
    except Exception as e:
        j.update(state="error", err=str(e)[:200], t=time.time())


@app.post("/api/jobs")
async def create(c: Clip, req: Request):
    limit(ip_of(req), n=6)
    await asyncio.to_thread(safe_url, c.url)
    if c.end <= c.start or c.end - c.start > MAX_CLIP:
        raise HTTPException(400, "Bad range")
    jid = uuid.uuid4().hex
    JOBS[jid] = {"state": "run", "p": 0, "t": time.time()}
    threading.Thread(target=work, args=(jid, c), daemon=True).start()
    return {"id": jid}


@app.get("/api/jobs/{jid}")
async def status(jid: str):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404)
    return {"state": j["state"], "p": round(j["p"], 1), "err": j.get("err")}


@app.get("/api/jobs/{jid}/file")
async def file(jid: str):
    j = JOBS.get(jid)
    if not j or j["state"] != "done":
        raise HTTPException(404)
    return FileResponse(j["file"], filename=Path(j["file"]).name)


async def janitor():
    while True:
        await asyncio.sleep(120)
        for k, j in list(JOBS.items()):
            if time.time() - j["t"] > TTL and j["state"] != "run":
                shutil.rmtree(TMP / k, ignore_errors=True)
                JOBS.pop(k, None)


@app.on_event("startup")
async def boot():
    asyncio.create_task(janitor())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), proxy_headers=True, forwarded_allow_ips="*")
