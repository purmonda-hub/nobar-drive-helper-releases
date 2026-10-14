"""Public website metadata only. Runs on a hosted runner without Drive credentials."""
import concurrent.futures,html,json,pathlib,re,time,urllib.parse,urllib.request,urllib.error
from html.parser import HTMLParser

SOURCES={'pahe':('Pahe','https://pahe.ink'),'onlykdrama':('OnlyKDrama','https://onlykdrama.shop'),'blurayufr':('BluRayUFR','https://blurayufr.site')}
LIMIT=20000
class Headings(HTMLParser):
    def __init__(self):super().__init__();self.depth=0;self.anchor=None;self.rows=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('h2','h3'):self.depth+=1
        if tag=='a' and self.depth:self.anchor=[dict(attrs).get('href',''),'']
    def handle_data(self,data):
        if self.anchor is not None:self.anchor[1]+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.anchor:self.rows.append(self.anchor);self.anchor=None
        if tag in ('h2','h3'):self.depth=max(0,self.depth-1)

def entries(source,raw):
    name,base=SOURCES[source];host=urllib.parse.urlsplit(base).hostname;parser=Headings();parser.feed(raw)
    rows=[];seen=set()
    for link,label in parser.rows:
        try:
            url=urllib.parse.urljoin(base,link);p=urllib.parse.urlsplit(url)
            if p.scheme!='https' or p.hostname!=host or p.username or p.password or p.port not in (None,443):continue
        except ValueError:continue
        if p.path=='/' or p.path.startswith(('/tag/','/category/','/author/','/page/')):continue
        title=' '.join(html.unescape(label).split())[:300]
        if not title or url in seen:continue
        rows.append(dict(title=title,url=url,source=name,seen=time.time()));seen.add(url)
    return rows[:60]

class SameSite(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,message,headers,newurl):
        a=urllib.parse.urlsplit(request.full_url);b=urllib.parse.urlsplit(newurl)
        if b.scheme!='https' or b.hostname!=a.hostname or b.username or b.password or b.port not in (None,443):raise ValueError('Sumber berpindah domain; perlu ditinjau.')
        return super().redirect_request(request,fp,code,message,headers,newurl)

def fetch_page(source,page):
    base=SOURCES[source][1];url=base+('/' if page==1 else '/page/'+str(page)+'/')
    request=urllib.request.Request(url,headers={'User-Agent':'FilmArsipCatalog/0.10.1 (public title and link metadata)','Accept':'text/html'})
    with urllib.request.build_opener(SameSite).open(request,timeout=20) as response:
        raw=response.read(3000001)
        if len(raw)>3000000:raise ValueError('Halaman melebihi batas.')
        text=raw.decode('utf8','replace')
        if re.search(r'cf-chl-|<title>\s*Just a moment',text,re.I):raise ValueError('Situs meminta verifikasi browser.')
        return entries(source,text)

def scan_source(source,cursor):
    rows=[];status='ok';message='Judul dan tautan sumber; isi film belum diperiksa.'
    # Refresh recent posts and advance three older catalog pages each run.
    pages=list(dict.fromkeys([1]+list(range(max(2,cursor),max(2,cursor)+3))))
    for page in pages:
        try:
            batch=fetch_page(source,page)
            if not batch:
                if page>1:cursor=2
                break
            rows.extend(batch)
            if page>1:cursor=page+1
        except Exception as exc:
            status='unavailable';message=str(exc)[:200] if isinstance(exc,ValueError) else 'Situs belum dapat dibaca atau meminta login/verifikasi.'
            break
    return rows,min(cursor,1000),dict(source=SOURCES[source][0],status=status,message=message,indexed=len(rows))

def check_link(url):
    try:
        p=urllib.parse.urlsplit(url)
        if p.scheme!='https' or p.hostname not in {urllib.parse.urlsplit(base).hostname for _,base in SOURCES.values()} or p.username or p.password or p.port not in (None,443):return 'unavailable'
        opener=urllib.request.build_opener(SameSite)
        request=urllib.request.Request(url,method='HEAD',headers={'User-Agent':'FilmArsipCatalog/0.10.1'})
        try:
            with opener.open(request,timeout=12) as response:return 'ok' if response.status==200 else 'unavailable'
        except urllib.error.HTTPError as exc:
            if exc.code in (404,410):
                text=exc.read(8192).decode('utf8','replace')
                return 'unavailable' if re.search(r'cf-chl-|Just a moment|captcha',text,re.I) else 'gone'
            if exc.code not in (405,501):return 'unavailable'
        request=urllib.request.Request(url,headers={'User-Agent':'FilmArsipCatalog/0.10.1','Range':'bytes=0-8191'})
        try:
            with opener.open(request,timeout=12) as response:
                # Read a bounded HTML snippet only; never a movie/archive payload.
                if 'html' not in response.headers.get('Content-Type',''):return 'unavailable'
                text=response.read(8192).decode('utf8','replace')
                return 'unavailable' if re.search(r'cf-chl-|Just a moment|captcha',text,re.I) else 'ok'
        except urllib.error.HTTPError as exc:return 'gone' if exc.code in (404,410) and not re.search(r'cf-chl-|Just a moment|captcha',exc.read(8192).decode('utf8','replace'),re.I) else 'unavailable'
    except Exception:return 'unavailable'

def clean_dead_links(items,statuses,removed):
    now=time.time();healthy_hosts={urllib.parse.urlsplit(base).hostname for source,(_,base) in SOURCES.items() if any(s['source']==SOURCES[source][0] and s['status']=='ok' for s in statuses)}
    candidates=[row for row in items if urllib.parse.urlsplit(row['url']).hostname in healthy_hosts and now-row.get('checked',0)>=(86400 if row.get('deadChecks') else 604800)]
    candidates.sort(key=lambda row:(not bool(row.get('deadChecks')),row.get('checked') or row.get('seen',0)))
    selected=candidates[:20];gone=set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for row,status in zip(selected,pool.map(lambda row:check_link(row['url']),selected)):
            row['checked']=now;row['linkStatus']=status
            if status=='ok':row.pop('deadChecks',None);row.pop('deadSince',None)
            elif status=='gone':
                row['deadChecks']=row.get('deadChecks',0)+1;row.setdefault('deadSince',now)
                if row['deadChecks']>=2 and now-row['deadSince']>=86400:gone.add(row['url'])
            # Login, CAPTCHA, rate limits and transient outages never erase an entry.
    removed=[r for r in removed if now-r.get('removedAt',0)<604800]+[dict(url=url,removedAt=now) for url in gone]
    return [row for row in items if row['url'] not in gone],removed[-1000:],len(selected)

def update(path):
    path=pathlib.Path(path)
    state=json.loads(path.read_text('utf8')) if path.exists() else {'version':1,'items':[],'cursors':{}}
    existing={r['url']:r for r in state.get('items',[]) if set(r).issubset({'title','url','source','seen','checked','linkStatus','deadChecks','deadSince'})}
    removed=[r for r in state.get('removed',[]) if time.time()-r.get('removedAt',0)<604800];tombstones={r['url'] for r in removed}
    cursors=state.get('cursors',{});statuses=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs={source:pool.submit(scan_source,source,cursors.get(source,2)) for source in SOURCES}
        for source,job in jobs.items():
            rows,cursors[source],status=job.result();statuses.append(status)
            for row in rows:
                if row['url'] not in tombstones:existing[row['url']]={**existing.get(row['url'],{}),**row}
    items=sorted(existing.values(),key=lambda r:-r.get('seen',0))[:LIMIT]
    items,removed,checked=clean_dead_links(items,statuses,removed)
    result={'version':1,'updated':time.time(),'partial':True,'count':len(items),'cursors':cursors,'sources':statuses,'items':items,'removed':removed,'checkedThisRun':checked}
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf8');tmp.replace(path)
    print(json.dumps({'count':len(items),'sources':statuses}))
    return result

if __name__=='__main__':update(pathlib.Path(__file__).resolve().parent/'index.json')

