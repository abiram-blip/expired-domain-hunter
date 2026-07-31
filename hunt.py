#!/usr/bin/env python3
"""Expired Domain Hunter v2 — local compute helpers for the DAILY pipeline.
The AGENT drives browser steps (harvest, Spamhaus browser-fetch, URIBL loop, archive
eyeball, sheet fallback paste) via browser-harness; this script does the deterministic parts.
Export EDH_RUN_DATE=$(date +%F) at run start so a run crossing midnight stays in one run dir.

Usage:
  hunt.py plan-harvest                      -> today's harvest schedule (lists/pages/tokens/window/boost)
  hunt.py merge-harvest <files...> [--date D] [--window LO,HI]
                                            -> run/D/harvest_new.json (dedupe + seen-filter + endtime parse)
  hunt.py prescore   <domains.json>         -> {domain:{score,fit,reason}}  numeric name pre-rank
  hunt.py blocklist  <domains.json>         -> {domain:[listed_zones]}      41-list DNS sweep
  hunt.py vt         <domains.json>         -> {domain:{malicious,...}}     needs VT_API_KEY
  hunt.py archive    <domains.json>         -> {domain:{years,first,last,flags}}
  hunt.py namegrade  <domains.json>         -> {domain:{fit,reason}}        A/B/C
  hunt.py tier       <verified.json>        -> {domain:{tier,...}}          T1-T5 from WBY/score/price
  hunt.py seen       <domains.json>         -> filters out ledger.seen, prints NEW only
  hunt.py append     <shortlist.json>       -> POST rows to sheet webhook; TSV fallback on stdout, exit 2
  hunt.py slack-post <shortlist.json> [--note s] -> post daily summary to Slack (best-effort, never blocks delivery)
  hunt.py state --date D --stage S --status ok|partial|failed|pending [--count N] [--note s] [--shortfall N] [--start] [--finish]
  hunt.py status [--brief] [--days N]       -> recent run reports from run/*/state.json
  hunt.py carryover [--date D]              -> prior run's live undelivered survivors + stage reached
  hunt.py commit --delivered f [--carryover f] [--stats f] [--advance-wheel N] [--boost D] [--harvested f]
                     (seen-additions are derived from today's stage files automatically since
                      2026-07-15; --harvested is now an optional EXTRA explicit-blacklist list,
                      omit it in normal daily use — see reviewed_domains())
"""
import sys, os, json, re, time, datetime
HERE=os.path.dirname(os.path.abspath(__file__))
LEDGER=os.path.join(HERE,'ledger.json')
CONFIG=os.path.join(HERE,'config.json')
RUNDIR=os.path.join(HERE,'run')
def cfg():
    try: return json.load(open(CONFIG))
    except FileNotFoundError: return {}
def today():
    # EDH_RUN_DATE pins a run to one date across midnight (RUNBOOK exports it at run start)
    return os.environ.get('EDH_RUN_DATE') or datetime.date.today().isoformat()
def rundir(date):
    d=os.path.join(RUNDIR,date); os.makedirs(d,exist_ok=True); return d
def wjson(path,obj):
    # atomic write: a crash mid-dump must never leave a truncated file
    tmp=path+'.tmp'
    with open(tmp,'w') as f:
        json.dump(obj,f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)
def _sh(v):
    # spamhaus score in whatever shape the agent wrote: 9.7 / "9.7" / {"score":9.7} / "abused" / null
    try:
        if isinstance(v,dict): v=v.get('score',0)
        return float(v)
    except (TypeError,ValueError): return 0.0
def _window(l=None):
    l=l if l is not None else load_ledger()
    return [24,168] if l.get('harvest_boost',0)>0 else cfg().get('harvest_window_hours',[36,120])

ZONES={"0spam":"0spamurl.fusionzero.com","abuse.ro":"uribl.abuse.ro","spamlookup":"bsb.spamlookup.net",
 "brukalai-b":"black.dnsbl.brukalai.lt","brukalai-l":"light.dnsbl.brukalai.lt","fmb-bl":"bl.fmb.la",
 "fmb-comm":"communicado.fmb.la","fmb-nsbl":"nsbl.fmb.la","fmb-short":"short.fmb.la",
 "hostkarma-b":"black.junkemailfilter.com","mailcleaner-n":"nuribl.mailcleaner.net","mailcleaner":"uribl.mailcleaner.net",
 "nordspam":"dbl.nordspam.com","nszones":"ubl.nszones.com","pofon":"uribl.pofon.foobar.hu",
 "polspam":"rhsbl.rbl.polspam.pl","polspam-h":"rhsbl-h.rbl.polspam.pl","rjek-mail":"mailsl.dnsbl.rjek.com","rjek-url":"urlsl.dnsbl.rjek.com",
 "rspamd":"uribl.rspamd.com","rymsho":"rhsbl.rymsho.ru","sarbl":"public.sarbl.org","scientificspam":"rhsbl.scientificspam.net",
 "sem-fresh":"fresh.spameatingmonkey.net","sem-fresh10":"fresh10.spameatingmonkey.net","sem-fresh15":"fresh15.spameatingmonkey.net",
 "sem-fresh30":"fresh30.spameatingmonkey.net","sem-freshz":"freshzero.spameatingmonkey.net","sem-uri":"uribl.spameatingmonkey.net","sem-urired":"urired.spameatingmonkey.net",
 "spamhaus":"dbl.spamhaus.org","spfbl":"dnsbl.spfbl.net","suomispam":"dbl.suomispam.net","surbl":"multi.surbl.org",
 "swinog":"uribl.swinog.ch","dob":"dob.sibl.support-intelligence.net","woody":"uri.blacklist.woody.ch","zapbl":"rhsbl.zapbl.net",
 "scrollout-d":"reputation-domain.rbl.scrolloutf1.com","scrollout-n":"reputation-ns.rbl.scrolloutf1.com","spfbl-score":"score.spfbl.net"}

def blocklist(doms):
    import dns.resolver, concurrent.futures
    def q(d,z):
        r=dns.resolver.Resolver(); r.lifetime=8; r.timeout=8; r.nameservers=['1.1.1.1','8.8.8.8','9.9.9.9']
        try:
            a=[x.to_text() for x in r.resolve(f"{d}.{z}",'A')]
            real=[c for c in a if c!='127.0.0.1' and not c.startswith('127.255.')]
            if z.startswith('black.junkemailfilter'): real=[c for c in real if c=='127.0.0.2']
            return bool(real)
        except Exception: return False
    out={d:[] for d in doms}
    tasks=[(d,n,z) for d in doms for n,z in ZONES.items()]
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=48) as ex:
        futs={ex.submit(q,d,z):(d,n) for d,n,z in tasks}
        for f in cf.as_completed(futs):
            d,n=futs[f]
            if f.result(): out[d].append(n)
    return out

def vt(doms):
    import urllib.request
    key=cfg().get('VT_API_KEY')
    if not key: return {"_error":"no VT_API_KEY in config.json"}
    out={}
    for i,d in enumerate(doms):
        try:
            req=urllib.request.Request(f"https://www.virustotal.com/api/v3/domains/{d}",headers={"x-apikey":key})
            j=json.load(urllib.request.urlopen(req,timeout=20))
            st=j["data"]["attributes"]["last_analysis_stats"]
            out[d]={"malicious":st.get("malicious",0),"suspicious":st.get("suspicious",0)}
        except Exception as e:
            out[d]={"error":str(e)[:60]}
        time.sleep(16)  # free tier 4/min
    return out

def archive(doms):
    import urllib.request, concurrent.futures as cf
    # 2026-07-17 fix: naive substring matching false-positived on 'cialis' inside
    # 'specialists' (a mental-health-furniture site's real content, not pharma spam).
    # Word-boundary regex for every flag word except the deliberate stem 'gambl'
    # (must match as a prefix of gambling/gambler/gamble, no trailing boundary).
    FLAGS={'adult':['porn','xxx','sex','nude','escort','webcam','milf','anal','pussy','hardcore','adult','tgp','hentai','erotic'],
           'gambling':['casino','poker','betting','slots','sportsbook','gambl'],
           'pharma':['viagra','cialis','online pharmacy','tramadol','levitra']}
    _FLAG_RE={c:[re.compile(r'\b'+re.escape(w)+(r'' if w=='gambl' else r'\b')) for w in ws] for c,ws in FLAGS.items()}
    def one(d):
        try:
            cdx=f"https://web.archive.org/cdx/search/cdx?url={d}&output=json&fl=timestamp&filter=statuscode:200&collapse=timestamp:6&limit=100"
            ts=[x[0] for x in json.load(urllib.request.urlopen(urllib.request.Request(cdx,headers={'User-Agent':'edh/1'}),timeout=25))[1:]]
            txt=""
            for t in ([ts[len(ts)//3],ts[2*len(ts)//3]] if len(ts)>2 else ts):
                try:
                    h=urllib.request.urlopen(urllib.request.Request(f"https://web.archive.org/web/{t}id_/http://{d}/",headers={'User-Agent':'edh/1'}),timeout=25).read().decode('utf-8','ignore')
                    txt+=" "+re.sub(r'<[^>]+>',' ',h).lower()
                except Exception: pass
                time.sleep(0.5)
            hits={c:[rx.pattern for rx in rxs if rx.search(txt)] for c,rxs in _FLAG_RE.items()}
            return d,{"years":len(ts),"first":ts[0][:4] if ts else None,"last":ts[-1][:4] if ts else None,
                      "flags":{c:v for c,v in hits.items() if v}}
        except Exception as e:
            return d,{"error":str(e)[:60]}
    out={}
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        for d,r in ex.map(one,doms): out[d]=r; time.sleep(0.3)
    return out

WORDS=set(w.strip().lower() for w in open('/usr/share/dict/words') if len(w.strip())>=3) if os.path.exists('/usr/share/dict/words') else set()
ADULT=('xxx','porn','sex','nude','teen','adult','cam','escort','milf','fetish','babe')
HOBBY=('airsoft','golf','bike','fitness','game','pet','dog','cat','wine','beer','fishing','casino','music','comic','movie','ponies','spa','motel','hotel','waterpark','travel')
# 2026-07-14 rule change (user directive): only auction-live + (abstract OR IT) + $1-$5 domains.
# VERTICAL_BAD = explicitly names a non-IT profession/trade -> hard reject even if otherwise clean
# (confirmed against a real batch: vitruvianlaw, pittmanandavis, onsiteworks, scrapsolutions,
# odinsearchgroup, dacitconsulting all rejected; itfloor, nexsysdigital, zydecodigital,
# arielwebsites, tetonsolutions, koigroup, garvingroup, daamsgroup, thedaltongroup,
# shermangroupllc, thesunworkscorp all accepted).
VERTICAL_BAD=('law','legal','attorney','notary','consult','construct','contractor','roofing','hvac',
 'plumb','scrap','recycl','waste','appraisal','realty','staffing','recruit','search','title',
 'accounting','bookkeep','insurance','medical','dental','veterinar','agri','mining','foundry',
 'fastener','forge','weld','manufactur','industr','precision','machine','steel','metal','engineer',
 'automat','component','electric','signage','kennel','saddle','tradeshow','tradefair')
# 'works' alone is a vertical-bad trade suffix (onsiteworks = construction) UNLESS the stem also
# carries a neutral corporate suffix, in which case it reads as one compound brand word, not a
# literal trade descriptor (thesunworkscorp = brand "Sunworks" + Corp, confirmed accept 2026-07-14).
VERTICAL_BAD_UNLESS_SUFFIXED=('works',)
# NEUTRAL_SUFFIX = generic corporate wrapper that carries an abstract/invented lead word without
# declaring any vertical (koigroup, garvingroup, shermangroupllc, thesunworkscorp all fit this).
NEUTRAL_SUFFIX=('group','corp','corporation','associat','partner','holding','company','capital','venture')
TOKENS_GOOD=('tech','soft','system','data','cyber','cloud','digital','logic','network','analytic',
 'integrat','hosting','server','firewall','telecom','database','security','solution','website',
 'webhost','webdesign','app','code','sys','net','encrypt')
# 2026-07-31: yield fix. The keyword WHEEL only surfaces 5-9 tokens/day; on days it lands on
# suffix tokens (associat/company/group/corp) the harvest is dominated by personal-name firms
# (juntoassociates, dreyeranddreyer, ...) that name_judge correctly rejects, starving the good
# IT-name pool and delivering 0-4/day. These CORE IT/abstract tokens now harvest EVERY day IN
# ADDITION to the wheel, so the high-yield IT inventory is always swept. The wheel still rotates
# for breadth. No quality gate relaxed — this only widens the candidate pool.
CORE_IT_TOKENS=('tech','soft','system','data','cyber','cloud','digital','logic','network',
 'hosting','security','app','code','sys')
FIRSTNAMES=('john','james','mary','jason','tyler','david','mike','michael','sarah','don','bob','tom','joe','jim','bill','steve','mark','paul','peter','frank','gary','larry','carol','susan','linda','nancy','karen','lisa','kevin','brian','jeff','scott','eric','ryan','amy','anna','emma','jack','sam','alex','chris','dan','matt','nick','tony','kirby','taylor','mann')
# 2026-07-17 sync: 12/18 of the 07-15 delivery (all fit=A, all passed every hard gate) came back
# "no"/"Invalid" from the user's own review — confirmed via direct question that both wordings
# mean the same thing (name/business judgment, not a data/listing problem). The automated IT-token
# check only verifies a tech WORD is present; it never checks whether the name reads as a real,
# distinctive brand vs. a generic feature description or a grammatically broken construction.
# These 3 patterns are concrete enough to catch in code (see [[feedback-domain-name-grading]] for
# the harder, judgment-only patterns that aren't safely mechanizable):
TRADEMARK_BAD=('cpanel','wordpress','shopify','plesk','godaddy','namecheap','salesforce','oracle',
 'microsoft','wix','squarespace','mailchimp','hubspot','cloudflare','akamai')
BAD_PLURAL=('hostings',)  # "hosting" as a service is never pluralized this way (asphostings.com)
ODD_SUFFIX=('istan',)  # evokes country names as a joke/pun, not a professional brand (cyberistan.com)

def _has_suffix(stem): return any(s in stem for s in NEUTRAL_SUFFIX)
def _is_it(stem): return any(t in stem for t in TOKENS_GOOD) or stem.startswith('it')
def _is_vertical_bad(stem):
    if any(t in stem for t in VERTICAL_BAD): return True
    if not _has_suffix(stem) and any(t in stem for t in VERTICAL_BAD_UNLESS_SUFFIXED): return True
    return False
def _is_taste_bad(stem):
    if any(t in stem for t in TRADEMARK_BAD): return True
    if any(stem.endswith(p) for p in BAD_PLURAL): return True
    if any(t in stem for t in ODD_SUFFIX): return True
    return False
def _abstract_ok(stem):
    # abstract = carries a neutral corporate wrapper and isn't already a named vertical
    # (VERTICAL_BAD is always checked first, so reaching here means it's clean of those).
    return _has_suffix(stem)

def namegrade(doms):
    out={}
    for d in doms:
        stem=d.lower().rsplit('.',1)[0]
        if any(a in stem for a in ADULT): out[d]={'fit':'C','reason':'adult token'}; continue
        if any(h in stem for h in HOBBY): out[d]={'fit':'C','reason':'consumer/hobby token'}; continue
        if _is_vertical_bad(stem): out[d]={'fit':'C','reason':'named non-IT vertical'}; continue
        if _is_taste_bad(stem): out[d]={'fit':'C','reason':'trademark/bad-plural/odd-suffix'}; continue
        v=sum(c in 'aeiouy' for c in stem)/max(1,len(stem))
        if v<0.25 or len(stem)>15: out[d]={'fit':'C','reason':'unbrandable/foreign'}; continue
        if _is_it(stem):
            out[d]={'fit':'A','reason':'IT-based'}
        elif _abstract_ok(stem):
            out[d]={'fit':'A','reason':'abstract + neutral suffix'}
        else:
            out[d]={'fit':'B','reason':'neutral/brandable, not clearly IT or abstract'}
    return out

def _dict_coverage(stem):
    covered=0; i=0
    while i<len(stem):
        best=0
        for j in range(len(stem),i+2,-1):
            if stem[i:j] in WORDS: best=j-i; break
        if best: covered+=best; i+=best
        else: i+=1
    return covered/max(1,len(stem))

def prescore(doms):
    out={}
    for d in doms:
        stem=d.lower().rsplit('.',1)[0]
        if not stem.isalpha() or len(stem)>15:
            out[d]={'score':0,'fit':'C','reason':'non-letters or too long'}; continue
        if any(a in stem for a in ADULT): out[d]={'score':0,'fit':'C','reason':'adult token'}; continue
        if any(h in stem for h in HOBBY): out[d]={'score':0,'fit':'C','reason':'consumer/hobby token'}; continue
        if _is_vertical_bad(stem): out[d]={'score':0,'fit':'C','reason':'named non-IT vertical'}; continue
        if _is_taste_bad(stem): out[d]={'score':0,'fit':'C','reason':'trademark/bad-plural/odd-suffix'}; continue
        v=sum(c in 'aeiouy' for c in stem)/len(stem)
        if v<0.25: out[d]={'score':0,'fit':'C','reason':'unbrandable/foreign'}; continue
        s=40
        reason=[]
        it_hit=_is_it(stem)
        abstract_hit=(not it_hit) and _abstract_ok(stem)
        if it_hit: s+=25; reason.append('IT-based')
        elif abstract_hit: s+=25; reason.append('abstract+neutral-suffix')
        cov=_dict_coverage(stem); s+=int(20*cov)
        if len(stem)<=8: s+=10
        elif len(stem)<=11: s+=5
        if any(stem.startswith(n) for n in FIRSTNAMES): s-=25; reason.append('personal-name lead')
        s=max(0,min(100,s))
        # hard gate: without an IT token or abstract+suffix pairing, cap fit at B regardless of score
        fit = ('A' if s>=60 else ('B' if s>=35 else 'C')) if (it_hit or abstract_hit) else ('B' if s>=35 else 'C')
        out[d]={'score':s,'fit':fit,'reason':'; '.join(reason) or 'neutral'}
    return out

_ET=re.compile(r'(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?(?:\s*\d+\s*s)?\s*$',re.I)
def parse_endtime(s, base_ts):
    """'10d 2h 22m' / '2D 11H' / '11h 22m left' / '22m' -> absolute unix ts, or None."""
    s=re.sub(r'(?i)\b(ends?|in|left)\b',' ',str(s)).strip()
    m=_ET.match(s)
    if not m or not any(m.groups()): return None
    dd,hh,mm=(int(x) if x else 0 for x in m.groups())
    return base_ts + dd*86400 + hh*3600 + mm*60

def merge_harvest(files, date, window=None):
    lo,hi=window or _window()
    seen=set(load_ledger().get('seen',[]))
    path=os.path.join(rundir(date),'harvest_new.json')
    # incremental: existing entries (already parsed+filtered) are preserved untouched, so
    # re-runs and pass-C top-ups never re-anchor endtimes or clobber earlier passes
    try: out=json.load(open(path))
    except (FileNotFoundError,json.JSONDecodeError): out={}
    merged={}
    for f in files:
        b=os.path.basename(f)
        if not b.startswith('harvest_') or b=='harvest_new.json': continue  # never ingest own output
        data=json.load(open(f))
        base=data.pop('_captured_at',None) or os.path.getmtime(f)  # harvest stamps capture time; mtime is fallback
        for d,meta in data.items():
            dl=d.lower()
            if dl in seen or dl in out: continue
            ends=parse_endtime(meta.get('Endtime',''),base)
            row=dict(meta); row['auction_ends_at']=ends
            if ends is None: row['no_endtime']=True  # buy-now/closeout or unparsed — agent judges
            if dl not in merged or _price(row.get('Price'))<_price(merged[dl].get('Price')):
                row['list']=meta.get('list',b.replace('harvest_','').replace('.json',''))
                merged[dl]=row
    now=time.time(); added=0
    for d,r in merged.items():
        if r['auction_ends_at'] is not None:
            hrs=(r['auction_ends_at']-now)/3600
            if hrs<lo or hrs>hi: continue
            r['hours_left']=round(hrs,1)
            r['auction_ends_local']=datetime.datetime.fromtimestamp(r['auction_ends_at']).strftime('%Y-%m-%d %H:%M')
        out[d]=r; added+=1
    wjson(path,out)
    return {"new":added,"total":len(out),"window":[lo,hi],"written":path}

def _price(p):
    try: return float(re.sub(r'[^0-9.]','',str(p)) or 'inf')
    except ValueError: return float('inf')

def tier(rows):
    # 2026-07-11 lesson: 2/2 domains delivered with no parseable auction Endtime (closeout-list
    # rows lacking a countdown) came back "not for auction"/"not for sale" from the user — a
    # missing countdown means the listing isn't a confirmed live auction, not just a formatting
    # gap. 2026-07-14 (Rule 1, user directive): this is now a HARD exclusion, not just a rank
    # penalty — a no_live_auction row gets tier=None unconditionally, so it can never be
    # delivered even as fallback on a shortfall day. Auction-only, no exceptions.
    tiers=cfg().get('tiers',[])
    out={}
    for d,r in rows.items():
        no_live=r.get('auction_ends_at') is None
        if no_live:
            out[d]=dict(r,tier=None,spamhaus=_sh(r.get('spamhaus',r.get('Spamhaus_Score',r.get('spamhaus_score',0)))),no_live_auction=True)
            continue
        try: wby=int(str(r.get('WBY','0'))[:4])
        except ValueError: wby=0
        sh=_sh(r.get('spamhaus',r.get('Spamhaus_Score',r.get('spamhaus_score',0))))
        pr=_price(r.get('Price'))
        t=next((t['id'] for t in tiers if wby and wby<=t['wby_max'] and sh>=t['sh_min'] and pr<=t['price_max']),None)
        out[d]=dict(r,tier=t,spamhaus=sh,no_live_auction=False)
    return out

LISTS_A=['godaddytdnam','godaddyexpired','dynadotexpired','namecheapauctions','gnameauctions']
LISTS_B=['godaddycloseouts','dynadotcloseouts','sedobargains']
def _edurl(lst,fwhoisage,fpriceto,extra=''):
    # 2026-07-12: user asked NOT to use their saved-search values (fmaxhost=12/fminhost=2/
    # fwhoisagemax=1990) — wants the agent's own filter design instead, tuned for volume. No
    # site-level char-length or age-floor param; code-level <=15 char check (namegrade/prescore)
    # is the sole length gate, applied uniformly across all lists.
    return (f"https://member.expireddomains.net/domains/{lst}/?ftlds[]=2&fonlycharhost=1"
            f"&fwhoisage={fwhoisage}&fpriceto={fpriceto}&o=endtime&r=a{extra}")

def plan_harvest():
    # 2026-07-12 redesign: 3 days of data (629/398/164 new) showed age<=2008 + same-list dedup
    # shrinks fast. Pass C (2009-2013) is now a STANDARD daily pass, not boost-gated — doubles
    # the sourcing pool while every safety gate (blocklist/URIBL/VT/history/name) stays identical;
    # only the tier label (T4/T5 vs T1/T2/T3) differs. Keyword tokens/day raised 2->5 (24-token
    # wheel completes in ~5 days instead of 12) — cheap, reaches inventory outside the top-N
    # endtime-sorted view. No site-level char-length filter (user's GoDaddy-only saved-search
    # cap intentionally not applied here; code-level <=15 char check is the sole length gate).
    c=cfg(); l=load_ledger()
    boost=min(2,max(0,l.get('harvest_boost',0)))
    wheel=c.get('keyword_wheel',[]); pos=l.get('keyword_wheel_pos',0)%max(1,len(wheel))
    ntok=5+2*boost
    tokens=[wheel[(pos+i)%len(wheel)] for i in range(ntok)] if wheel else []
    lo,hi=_window(l)
    price_max=cfg().get('price_ceiling_usd',5)
    passes=[{'pass':'A','list':x,'pages':(6+2*boost if x=='godaddytdnam' else 2),'url':_edurl(x,2008,1)} for x in LISTS_A]
    passes+=[{'pass':'B','list':x,'pages':2,'url':_edurl(x,2008,price_max)} for x in LISTS_B]
    pass_c=[{'pass':'C','list':x,'pages':(4+2*boost if x=='godaddytdnam' else 2),'url':_edurl(x,2013,price_max)}
            for x in ('godaddytdnam','godaddyexpired','godaddycloseouts')]
    # Core IT tokens sweep EVERY day; the wheel tokens add rotating breadth. Deduped, core first.
    kw_tokens=list(dict.fromkeys(list(CORE_IT_TOKENS)+list(tokens)))
    for t in kw_tokens:
        for x in ('godaddytdnam','godaddyexpired'):
            passes.append({'pass':'KW','list':x,'pages':2,'token':t,'url':_edurl(x,2008,1,f'&fdomain={t}')})
    out={'date':today(),'boost':boost,'tokens':tokens,'kw_tokens':kw_tokens,'window_hours':[lo,hi],
         'raw_new_target':c.get('raw_new_target_per_day',180),
         'include_pass_c':True,
         'passes':passes,'pass_c':pass_c,
         'note':'pass C (2009-2013, T4/T5) runs every day by default now; boost widens it further'}
    return out

def append_rows(path):
    # exit 0 = appended. exit 2 = definitely NOT appended -> agent pastes the TSV below.
    # exit 3 = UNKNOWN (timeout / unrecognized reply) -> agent must CHECK THE SHEET before
    # pasting, or rows get double-delivered.
    import urllib.request, urllib.error, socket
    c=cfg(); url=c.get('sheet_webapp_url'); tok=c.get('sheet_webapp_token')
    j=json.load(open(path))
    rows=j.get('rows') if isinstance(j,dict) else j
    if not isinstance(rows,list) or not all(isinstance(r,list) for r in rows):
        sys.stderr.write("shortlist must be {'rows':[[...]]} or [[...]]\n"); return 2
    def tsv():
        for r in rows: print('\t'.join(str(x) for x in r))
    if not url:
        sys.stderr.write("no sheet_webapp_url configured\n"); tsv(); return 2
    try:
        body=json.dumps({'token':tok,'tab':c.get('sheet_tab','Sheet1'),'rows':rows}).encode()
        req=urllib.request.Request(url,data=body,headers={'Content-Type':'application/json'})
        resp=urllib.request.urlopen(req,timeout=30)
        txt=resp.read().decode()[:300]
        try: ok=json.loads(txt).get('ok') is True
        except (json.JSONDecodeError,AttributeError): ok=None
        if resp.status==200 and ok is True:
            print(json.dumps({'appended':len(rows),'via':'webhook'})); return 0
        if ok is False:
            sys.stderr.write(f"webhook refused: {txt}\n"); tsv(); return 2
        sys.stderr.write(f"webhook ambiguous reply — CHECK SHEET before pasting: {txt}\n"); tsv(); return 3
    except (socket.timeout,TimeoutError):
        sys.stderr.write("webhook timeout — request may have landed. CHECK SHEET before pasting\n"); tsv(); return 3
    except urllib.error.URLError as e:
        sys.stderr.write(f"webhook unreachable: {e}\n"); tsv(); return 2

# shortlist.json row order (matches the sheet's live header): Domain, Payment Status,
# GoDaddy_Buy_Link, Name_Fit, Name_Note, Auction_Ends, Auction_Status, Spamhaus_Score,
# Domain_Age_Yrs, First_Archived, Blocklists_49, URIBL_web, VirusTotal, History_Review,
# MultiRBL_You, MultiRBL_Colleague, Backlinks, Price, Tier
def slack_post(path, note=None):
    # Best-effort notification only — never blocks or reverses the sheet delivery.
    # Returns 0 sent, 1 not configured/failed (caller should log and move on, not retry the sheet).
    import urllib.request, urllib.error, socket
    c=cfg(); url=c.get('slack_webhook_url')
    if not url:
        sys.stderr.write("no slack_webhook_url configured — skipping Slack post\n"); return 1
    j=json.load(open(path))
    rows=j.get('rows') if isinstance(j,dict) else j
    date=today()
    lines=[f"*Domain Hunt — {date}: {len(rows)} new domains*"]
    if note: lines.append(f"_{note}_")
    for r in rows:
        r=r+['']*(19-len(r))  # tolerate short rows
        dom,link,fit,note_,ends,price,tier = r[0],r[2],r[3],r[4],r[5],r[17],r[18]
        urgent=' :rotating_light: URGENT' if 'URGENT' in str(price) or 'URGENT' in str(note_) else ''
        lines.append(f"• <{link}|*{dom}*> — {note_ or 'n/a'} — Tier {tier or '?'} — {price or '?'} — ends {ends or 'n/a'} ({fit}){urgent}")
    text="\n".join(lines)
    try:
        body=json.dumps({'text':text,'channel':c.get('slack_channel')}).encode()
        req=urllib.request.Request(url,data=body,headers={'Content-Type':'application/json'})
        resp=urllib.request.urlopen(req,timeout=20)
        ok=resp.read().decode().strip()=='ok'
        if resp.status==200 and ok:
            print(json.dumps({'posted':len(rows),'via':'slack-webhook'})); return 0
        sys.stderr.write(f"slack webhook non-ok reply: {ok}\n"); return 1
    except (socket.timeout,TimeoutError):
        sys.stderr.write("slack webhook timeout\n"); return 1
    except urllib.error.URLError as e:
        sys.stderr.write(f"slack webhook unreachable: {e}\n"); return 1

STAGES=['precheck','harvest','merge','prescore','blocklist','spamhaus','uribl','archive','vt','deliver','commit']
def state_path(date): return os.path.join(rundir(date),'state.json')
def load_state(date):
    try: return json.load(open(state_path(date)))
    except FileNotFoundError:
        return {'date':date,'started':None,'finished':None,
                'stages':{s:{'status':'pending'} for s in STAGES},'shortfall':None,'notes':[]}
def save_state(st): wjson(state_path(st['date']),st)

def status_report(days=3,brief=False):
    if not os.path.isdir(RUNDIR): return ["no runs yet"]
    dirs=sorted((d for d in os.listdir(RUNDIR) if re.match(r'\d{4}-\d{2}-\d{2}$',d)),reverse=True)[:days]
    target=cfg().get('target_per_run',15); lines=[]
    for d in dirs:
        try: st=json.load(open(state_path(d)))
        except FileNotFoundError: continue
        dl=st['stages'].get('deliver',{}).get('count',0) or 0
        bad=[f"{s} {v['status']}" for s,v in st['stages'].items() if v.get('status') not in ('ok','pending')]
        lines.append(f"{d}: delivered {dl}/{target}"+(f" ({'; '.join(bad)})" if bad else ""))
        if brief: break
    return lines or ["no runs yet"]

PASS_CHECKS=[('blocklist',lambda v:v==[]),
             ('spamhaus',lambda v:_sh(v)>=9.0),
             ('uribl',lambda v:isinstance(v,dict) and v.get('listed') is False),
             ('archive',lambda v:isinstance(v,dict) and not v.get('error') and not v.get('flags')),
             ('vt',lambda v:isinstance(v,dict) and v.get('malicious')==0)]
def carryover(date=None):
    # scan up to 3 prior run dirs (newest wins) so a domain held two days running
    # doesn't silently vanish when the newest dir no longer carries it
    if not os.path.isdir(RUNDIR): return {}
    dirs=sorted((d for d in os.listdir(RUNDIR) if re.match(r'\d{4}-\d{2}-\d{2}$',d) and d<today()),reverse=True)
    dirs=[date] if date else dirs[:3]
    l=load_ledger()
    led_del=set(l.get('delivered',[]))
    # 2026-07-23 fix: a domain added to ledger.seen via commit --harvested (a taste
    # reject that never reached a PASS_CHECKS stage file, so it can't land in `failed`
    # either) was resurfacing here forever — carryover() only ever excluded `delivered`,
    # not the broader `seen` blacklist. Confirmed live: 16 of 52 A-fit carryover
    # candidates on 2026-07-23 were exact repeats of 2026-07-21's manual taste rejects.
    led_seen=set(w.lower() for w in l.get('seen',[]))
    min_h=cfg().get('min_hours_left_deliver',12); now=time.time()
    def rj_day(day,n):
        try: return json.load(open(os.path.join(RUNDIR,day,n+'.json')))
        except (FileNotFoundError,json.JSONDecodeError): return {}
    # A domain that FAILED a check on ANY scanned day is permanently excluded from carryover —
    # a later day's incomplete data (stage never run) must never resurrect a definitive failure
    # from an earlier day (2026-07-12 lesson: speedexvpn.com failed VT on 07-11 but 07-10's
    # independent, VT-blind pass on the same domain let it back in).
    failed=set()
    for day in dirs:
        for stage,pred in PASS_CHECKS:
            for dl,v in rj_day(day,stage).items():
                try:
                    if not pred(v): failed.add(dl.lower())
                except Exception: pass
    out={}
    for day in dirs:
        rd=os.path.join(RUNDIR,day)
        def rj(n,day=day): return rj_day(day,n)
        harvest=rj('harvest_new'); delivered=set(x.lower() for x in (rj('delivered') or []))
        for d,meta in harvest.items():
            dl=d.lower()
            if dl in out or dl in delivered or dl in led_del or dl in led_seen or dl in failed: continue
            ends=meta.get('auction_ends_at')
            if ends is None: continue  # Rule 1 (2026-07-14): auction-only, no exceptions
            if (ends-now)/3600 < min_h+2: continue  # +2h margin: must survive today's funnel too
            reached='harvest'; clean=True
            for stage,pred in PASS_CHECKS:
                data=rj(stage)
                if dl not in data and d not in data: break
                v=data.get(dl,data.get(d))
                if not pred(v): clean=False; break
                reached=stage
            if not clean: continue
            meta=dict(meta,stage_reached=reached,carryover_from=day)
            if ends:  # refresh time fields — yesterday's hours_left is ~24h stale
                meta['hours_left']=round((ends-now)/3600,1)
                meta['auction_ends_local']=datetime.datetime.fromtimestamp(ends).strftime('%Y-%m-%d %H:%M')
            out[dl]=meta
    return out

def load_ledger():
    try: return json.load(open(LEDGER))
    except FileNotFoundError:
        if os.path.exists(LEDGER+'.bak'):
            sys.exit("ledger.json is MISSING but ledger.json.bak exists — restore the backup before running anything")
        return {'seen':[],'delivered':[]}  # true first-run bootstrap only
    except json.JSONDecodeError:
        sys.exit("ledger.json is CORRUPT — restore from ledger.json.bak; do NOT commit")
def seen_filter(doms):
    s=set(load_ledger().get('seen',[]))
    return [d for d in doms if d.lower() not in s]

# 2026-07-15 fix: previously EVERY raw-harvested domain (hundreds-thousands/day) got marked
# seen, permanently blacklisting rows that were only scanned in bulk and never individually
# reviewed. This starved the pool — godaddytdnam returned 0 new domains by day 5. Now only
# domains that reached an actual verification stage today (evidence from the stage files
# themselves, not a manually-passed list) get added to seen. A domain that was merely one of
# 1000+ rows in a harvest sweep, never selected into the curated candidate set, can resurface
# on a later day instead of being locked out forever.
REVIEW_STAGES=('blocklist','spamhaus','uribl','archive','vt')
def reviewed_domains(date=None):
    date=date or today()
    reviewed=set()
    for stage in REVIEW_STAGES:
        try:
            data=json.load(open(os.path.join(RUNDIR,date,stage+'.json')))
            reviewed.update(k.lower() for k in data.keys() if '.' in k and len(k)<40)  # skip artifact keys
        except (FileNotFoundError,json.JSONDecodeError): pass
    return reviewed

def commit(harvested, delivered, carry=None, stats=None, advance_wheel=0, boost_delta=None):
    import fcntl
    with open(LEDGER+'.lock','w') as lk:
        fcntl.flock(lk,fcntl.LOCK_EX)
        l=load_ledger()
        if l.get('seen'): wjson(LEDGER+'.bak',l)  # pristine pre-commit copy
        rerun=l.get('last_commit_date')==today()  # resume-after-crash: unions are safe, scalars are not
        carry=set(x.lower() for x in (carry or []))
        # `harvested` is now an EXPLICIT extra-blacklist list (rarely needed — pass [] normally),
        # not "every domain scanned today". The real seen-additions come from reviewed_domains():
        # only domains that actually reached a verification stage, evidence-based.
        to_add=(reviewed_domains()|set(x.lower() for x in harvested))-carry
        l['seen']=sorted(set(l.get('seen',[]))|to_add|set(x.lower() for x in delivered))
        l['delivered']=sorted(set(l.get('delivered',[]))|set(x.lower() for x in delivered))
        l['updated']=today()
        if not rerun:
            wl=len(cfg().get('keyword_wheel') or [])
            if advance_wheel and wl: l['keyword_wheel_pos']=(l.get('keyword_wheel_pos',0)+advance_wheel)%wl
            elif advance_wheel: sys.stderr.write("keyword_wheel missing from config — wheel NOT advanced\n")
            if boost_delta is not None: l['harvest_boost']=min(2,max(0,l.get('harvest_boost',0)+boost_delta))
            if stats: l.setdefault('stats',[]).append(dict(stats,date=today()))
            l['stats']=l.get('stats',[])[-30:]  # keep last 30 runs
        l['last_commit_date']=today()
        wjson(LEDGER,l)
    return {'seen':len(l['seen']),'delivered':len(l['delivered']),'rerun_scalars_skipped':rerun,
            'added_to_seen':len(to_add),'wheel_pos':l.get('keyword_wheel_pos',0),'boost':l.get('harvest_boost',0)}

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else ''
    def rd(p): return json.load(open(p)) if p.endswith('.json') else [x.strip() for x in open(p) if x.strip()]
    if cmd=='blocklist': print(json.dumps(blocklist(rd(sys.argv[2]))))
    elif cmd=='vt': print(json.dumps(vt(rd(sys.argv[2]))))
    elif cmd=='archive': print(json.dumps(archive(rd(sys.argv[2]))))
    elif cmd=='namegrade': print(json.dumps(namegrade(rd(sys.argv[2]))))
    elif cmd=='prescore': print(json.dumps(prescore(rd(sys.argv[2]))))
    elif cmd=='tier': print(json.dumps(tier(rd(sys.argv[2]))))
    elif cmd=='seen': print(json.dumps(seen_filter(rd(sys.argv[2]))))
    elif cmd=='plan-harvest': print(json.dumps(plan_harvest(),indent=1))
    elif cmd=='merge-harvest':
        import argparse; a=argparse.ArgumentParser(); a.add_argument('files',nargs='+')
        a.add_argument('--date',default=today()); a.add_argument('--window')
        n=a.parse_args(sys.argv[2:])
        w=[float(x) for x in n.window.split(',')] if n.window else None
        print(json.dumps(merge_harvest(n.files,n.date,w)))
    elif cmd=='append': sys.exit(append_rows(sys.argv[2]))
    elif cmd=='slack-post':
        note=sys.argv[sys.argv.index('--note')+1] if '--note' in sys.argv else None
        sys.exit(slack_post(sys.argv[2], note))
    elif cmd=='state':
        import argparse; a=argparse.ArgumentParser()
        a.add_argument('--date',default=today()); a.add_argument('--stage'); a.add_argument('--status')
        a.add_argument('--count',type=int); a.add_argument('--note'); a.add_argument('--shortfall',type=int)
        a.add_argument('--start',action='store_true'); a.add_argument('--finish',action='store_true')
        n=a.parse_args(sys.argv[2:]); st=load_state(n.date)
        if n.start: st['started']=datetime.datetime.now().strftime('%H:%M')
        if n.finish: st['finished']=datetime.datetime.now().strftime('%H:%M')
        if n.stage:
            e=st['stages'].setdefault(n.stage,{})
            if n.status: e['status']=n.status
            if n.count is not None: e['count']=n.count
        if n.note: st['notes'].append(n.note)
        if n.shortfall is not None: st['shortfall']=n.shortfall
        save_state(st); print(json.dumps(st['stages'].get(n.stage,st)))
    elif cmd=='status':
        brief='--brief' in sys.argv
        days=int(sys.argv[sys.argv.index('--days')+1]) if '--days' in sys.argv else 3
        print('\n'.join(status_report(days,brief)))
    elif cmd=='carryover':
        date=sys.argv[sys.argv.index('--date')+1] if '--date' in sys.argv else None
        print(json.dumps(carryover(date)))
    elif cmd=='commit':
        import argparse; a=argparse.ArgumentParser()
        a.add_argument('--harvested'); a.add_argument('--delivered'); a.add_argument('--carryover')
        a.add_argument('--stats'); a.add_argument('--advance-wheel',type=int,default=0); a.add_argument('--boost',type=int)
        n=a.parse_args(sys.argv[2:])
        print(json.dumps(commit(rd(n.harvested) if n.harvested else [], rd(n.delivered) if n.delivered else [],
                                rd(n.carryover) if n.carryover else None, rd(n.stats) if n.stats else None,
                                n.advance_wheel, n.boost)))
    else: print(__doc__)
