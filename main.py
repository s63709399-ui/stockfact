"""StockFact API : stockage PostgreSQL (tables relationnelles)."""
import base64
import json
import os
import re
import time
from contextlib import asynccontextmanager

import httpx
import psycopg
from psycopg.rows import dict_row
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

DSN = os.getenv("DATABASE_URL", "postgresql://stockfact:stockfact@db:5432/stockfact")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(id INT PRIMARY KEY DEFAULT 1, company TEXT, mf TEXT, addr TEXT, tel TEXT,
  tva NUMERIC(6,2), timbre NUMERIC(12,3), seq INT);
CREATE TABLE IF NOT EXISTS categories(name TEXT PRIMARY KEY, pos INT);
CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY, pos INT, ref TEXT, name TEXT NOT NULL, cat TEXT,
  price NUMERIC(14,3), cost NUMERIC(14,3), stock NUMERIC(14,3), min_stock NUMERIC(14,3));
CREATE TABLE IF NOT EXISTS clients(id TEXT PRIMARY KEY, pos INT, name TEXT NOT NULL, phone TEXT, addr TEXT, mf TEXT);
CREATE TABLE IF NOT EXISTS invoices(id TEXT PRIMARY KEY, pos INT, num TEXT, date DATE, cid TEXT,
  discount NUMERIC(6,2), tva NUMERIC(6,2), timbre NUMERIC(12,3));
CREATE TABLE IF NOT EXISTS invoice_lines(invoice_id TEXT REFERENCES invoices(id) ON DELETE CASCADE, pos INT,
  pid TEXT, name TEXT, qty NUMERIC(14,3), price NUMERIC(14,3), cost NUMERIC(14,3));
CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY, pos INT, cid TEXT, iid TEXT, date DATE,
  amount NUMERIC(14,3), mode TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS stock_moves(id TEXT PRIMARY KEY, pos INT, date DATE, pid TEXT, qty NUMERIC(14,3), note TEXT);
CREATE TABLE IF NOT EXISTS suppliers(id TEXT PRIMARY KEY, pos INT, name TEXT NOT NULL, mf TEXT, phone TEXT, addr TEXT);
CREATE TABLE IF NOT EXISTS purchases(id TEXT PRIMARY KEY, pos INT, num TEXT, date DATE, sid TEXT, remise NUMERIC(8,4),
  fodec NUMERIC(14,3), tva NUMERIC(6,2), timbre NUMERIC(12,3), total NUMERIC(14,3), paid NUMERIC(14,3));
CREATE TABLE IF NOT EXISTS purchase_lines(purchase_id TEXT REFERENCES purchases(id) ON DELETE CASCADE, pos INT,
  pid TEXT, code TEXT, name TEXT, qty NUMERIC(14,3), pu NUMERIC(14,3), cost NUMERIC(14,3));
CREATE INDEX IF NOT EXISTS ix_inv_cid ON invoices(cid);
CREATE INDEX IF NOT EXISTS ix_pay_cid ON payments(cid);
"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    for _ in range(30):  # attend que PostgreSQL soit prêt
        try:
            with psycopg.connect(DSN) as c:
                c.execute(SCHEMA)
            break
        except psycopg.OperationalError as e:
            print("PostgreSQL non joignable :", e, flush=True)
            time.sleep(1)
    yield


app = FastAPI(title="StockFact API", lifespan=lifespan)


def n(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/db")
def get_db():
    with psycopg.connect(DSN, row_factory=dict_row) as c:
        q = lambda sql: c.execute(sql).fetchall()
        s = q("SELECT company,mf,addr,tel,tva::float8 AS tva,timbre::float8 AS timbre,seq FROM settings WHERE id=1")
        lines = {}
        for l in q("SELECT invoice_id,pid,name,qty::float8 AS qty,price::float8 AS price,cost::float8 AS cost "
                   "FROM invoice_lines ORDER BY invoice_id,pos"):
            lines.setdefault(l.pop("invoice_id"), []).append(l)
        invoices = q("SELECT id,num,date::text AS date,cid,discount::float8 AS discount,tva::float8 AS tva,"
                     "timbre::float8 AS timbre FROM invoices ORDER BY pos")
        for i in invoices:
            i["lines"] = lines.get(i["id"], [])
        plines = {}
        for l in q("SELECT purchase_id,pid,code,name,qty::float8 AS qty,pu::float8 AS pu,cost::float8 AS cost "
                   "FROM purchase_lines ORDER BY purchase_id,pos"):
            plines.setdefault(l.pop("purchase_id"), []).append(l)
        purchases = q("SELECT id,num,date::text AS date,sid,remise::float8 AS remise,fodec::float8 AS fodec,"
                      "tva::float8 AS tva,timbre::float8 AS timbre,total::float8 AS total,paid::float8 AS paid "
                      "FROM purchases ORDER BY pos")
        for a in purchases:
            a["lines"] = plines.get(a["id"], [])
        return {
            "suppliers": q("SELECT id,name,mf,phone,addr FROM suppliers ORDER BY pos"),
            "purchases": purchases,
            "settings": s[0] if s else None,
            "categories": [r["name"] for r in q("SELECT name FROM categories ORDER BY pos")],
            "products": q("SELECT id,ref,name,cat,price::float8 AS price,cost::float8 AS cost,"
                          "stock::float8 AS stock,min_stock::float8 AS min FROM products ORDER BY pos"),
            "clients": q("SELECT id,name,phone,addr,mf FROM clients ORDER BY pos"),
            "invoices": invoices,
            "payments": q("SELECT id,cid,iid,date::text AS date,amount::float8 AS amount,mode,note "
                          "FROM payments ORDER BY pos"),
            "moves": q("SELECT id,date::text AS date,pid,qty::float8 AS qty,note FROM stock_moves ORDER BY pos"),
        }


@app.put("/api/db")
def put_db(d: dict = Body(...)):
    """Remplace tout l'état dans UNE transaction (tout ou rien)."""
    with psycopg.connect(DSN) as c:
        cur = c.cursor()
        for t in ("purchase_lines", "purchases", "suppliers", "invoice_lines", "invoices", "payments", "stock_moves",
                  "products", "clients", "categories", "settings"):
            cur.execute(f"DELETE FROM {t}")
        s = d.get("settings")
        if s:
            cur.execute("INSERT INTO settings VALUES (1,%s,%s,%s,%s,%s,%s,%s)",
                        (s.get("company"), s.get("mf"), s.get("addr"), s.get("tel"),
                         n(s.get("tva"), 19), n(s.get("timbre"), 1), int(n(s.get("seq")))))
        cats = list(dict.fromkeys(d.get("categories") or []))
        cur.executemany("INSERT INTO categories VALUES (%s,%s)", [(x, k) for k, x in enumerate(cats)])
        cur.executemany(
            "INSERT INTO products VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(p["id"], k, p.get("ref"), p.get("name") or "", p.get("cat"), n(p.get("price")),
              n(p.get("cost")), n(p.get("stock")), n(p.get("min"))) for k, p in enumerate(d.get("products") or [])])
        cur.executemany(
            "INSERT INTO clients VALUES (%s,%s,%s,%s,%s,%s)",
            [(x["id"], k, x.get("name") or "", x.get("phone"), x.get("addr"), x.get("mf"))
             for k, x in enumerate(d.get("clients") or [])])
        for k, i in enumerate(d.get("invoices") or []):
            cur.execute("INSERT INTO invoices VALUES (%s,%s,%s,%s::date,%s,%s,%s,%s)",
                        (i["id"], k, i.get("num"), i.get("date"), i.get("cid"),
                         n(i.get("discount")), n(i.get("tva")), n(i.get("timbre"))))
            cur.executemany(
                "INSERT INTO invoice_lines VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [(i["id"], j, l.get("pid"), l.get("name"), n(l.get("qty")), n(l.get("price")), n(l.get("cost")))
                 for j, l in enumerate(i.get("lines") or [])])
        cur.executemany(
            "INSERT INTO payments VALUES (%s,%s,%s,%s,%s::date,%s,%s,%s)",
            [(p["id"], k, p.get("cid"), p.get("iid"), p.get("date"), n(p.get("amount")), p.get("mode"), p.get("note"))
             for k, p in enumerate(d.get("payments") or [])])
        cur.executemany(
            "INSERT INTO stock_moves VALUES (%s,%s,%s::date,%s,%s,%s)",
            [(m["id"], k, m.get("date"), m.get("pid"), n(m.get("qty")), m.get("note"))
             for k, m in enumerate(d.get("moves") or [])])
        cur.executemany(
            "INSERT INTO suppliers VALUES (%s,%s,%s,%s,%s,%s)",
            [(x["id"], k, x.get("name") or "", x.get("mf"), x.get("phone"), x.get("addr"))
             for k, x in enumerate(d.get("suppliers") or [])])
        for k, a in enumerate(d.get("purchases") or []):
            cur.execute("INSERT INTO purchases VALUES (%s,%s,%s,%s::date,%s,%s,%s,%s,%s,%s,%s)",
                        (a["id"], k, a.get("num"), a.get("date"), a.get("sid"), n(a.get("remise")),
                         n(a.get("fodec")), n(a.get("tva")), n(a.get("timbre")), n(a.get("total")), n(a.get("paid"))))
            cur.executemany(
                "INSERT INTO purchase_lines VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                [(a["id"], j, l.get("pid"), l.get("code"), l.get("name"), n(l.get("qty")), n(l.get("pu")), n(l.get("cost")))
                 for j, l in enumerate(a.get("lines") or [])])
    return {"ok": True}


SCAN_PROMPT = '''Tu es un assistant comptable tunisien. L’image est une facture ou un bon de livraison-facture d’un FOURNISSEUR (en français, montants en dinars tunisiens à 3 décimales, virgule décimale).
Extrais les données et réponds UNIQUEMENT par un objet JSON valide (sans texte autour, sans markdown) de cette forme :
{"fournisseur":{"nom":"","mf":"","adresse":"","tel":""},"numero":"","date":"AAAA-MM-JJ","lignes":[{"code":"","designation":"","qte":0,"pu_ht":0,"montant_ht":0}],"total_ht":0,"fodec":0,"base_tva":0,"tva_taux":19,"tva_montant":0,"timbre":0,"net_a_payer":0,"net_en_lettres":""}
Règles : nombres JSON avec point décimal (ex. 452.220), sans séparateur de milliers. Le fournisseur est l’entreprise qui ÉMET le document (logo et en-tête), pas le client destinataire. "numero" = numéro du bon ou de la facture. "date" = date du document au format AAAA-MM-JJ. "pu_ht" = PU HTVA de la ligne ; "montant_ht" = Mont. HTVA de la ligne. "total_ht" = TOTAL HTVA du pied de page (après remise éventuelle). Si une valeur est illisible ou absente : "" pour un texte, 0 pour un nombre. "base_tva" = Base TVA du tableau en bas à gauche ; "net_en_lettres" = le montant écrit EN TOUTES LETTRES après « Arrêtée la présente à la somme de » (recopie-le exactement). Lis chaque chiffre avec soin : 3, 5, 6, 8 et 9 se ressemblent sur une impression matricielle. N’invente rien.'''


@app.exception_handler(httpx.HTTPError)
async def _net_error(_, e):
    if isinstance(e, httpx.TimeoutException) and not isinstance(e, httpx.ConnectTimeout):
        msg = ("le service de lecture a mis trop de temps à répondre (ReadTimeout) : réessayez, "
               "ou utilisez Groq (GROQ_API_KEY), beaucoup plus rapide")
    else:
        msg = f"connexion impossible à l'API de lecture ({type(e).__name__}) : vérifiez l'accès internet du serveur"
    return JSONResponse({"detail": msg}, status_code=504)


UNITS = {"zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
         "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
         "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
         "quatrevingt": 80, "quatrevingts": 80}


def _words_int(t):
    total = cur = 0
    for w in t.split():
        if w in UNITS:
            cur += UNITS[w]
        elif w in ("cent", "cents"):
            cur = (cur or 1) * 100
        elif w in ("mille", "milles"):
            total += (cur or 1) * 1000
            cur = 0
        elif w in ("million", "millions"):
            total += (cur or 1) * 1_000_000
            cur = 0
    return total + cur


def words_amount(text):
    """« mille cent soixante trois dinars soixante trois millimes » -> 1163.063"""
    t = re.sub(r"[^a-zéèêàçùû\- ]", " ", (text or "").lower()).replace("-", " ")
    t = t.replace("é", "e").replace("è", "e").replace("ê", "e").replace("quatre vingt", "quatrevingt")
    m = re.search(r"(.*?)dinars?(.*)", t, re.S)
    if not m:
        return None
    dinars = _words_int(m.group(1))
    millimes = _words_int(re.sub(r"millimes?", " ", m.group(2)))
    return dinars + millimes / 1000 if (dinars or millimes) else None


def reconcile(d):
    """Recoupe le net à payer : chiffres lus, montant en lettres, base TVA + TVA + timbre."""
    try:
        N, W = n(d.get("net_a_payer")), words_amount(d.get("net_en_lettres"))
        B, V, T = n(d.get("base_tva")), n(d.get("tva_montant")), n(d.get("timbre"))
        C = B + V + T if (B and V) else 0
        cands = [x for x in (N, W, C) if x]
        best = next((x for x in cands if sum(abs(x - y) <= 0.011 for y in cands) >= 2), None) or W
        if best:
            d["net_a_payer"] = round(best, 3)
    except Exception:
        pass
    return d


async def _groq(h, key, model, media, b64):
    return await h.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "temperature": 0, "max_tokens": 2000,
              "messages": [{"role": "user", "content": [
                  {"type": "text", "text": SCAN_PROMPT},
                  {"type": "image_url", "image_url": {"url": f"data:{media};base64,{b64}"}}]}]})


@app.post("/api/scan")
async def scan(request: Request):
    """Lit une photo de facture fournisseur : Gemini (offre gratuite) ou Claude, selon la clé configurée."""
    qkey, gkey, akey = os.getenv("GROQ_API_KEY"), os.getenv("GEMINI_API_KEY"), os.getenv("ANTHROPIC_API_KEY")
    prov = (os.getenv("SCAN_PROVIDER") or "").lower()  # forcer un fournisseur : groq | gemini | anthropic
    if prov == "anthropic":
        qkey = gkey = None
    elif prov == "gemini":
        qkey = akey = None
    elif prov == "groq":
        gkey = akey = None
    if not (qkey or gkey or akey):
        raise HTTPException(503, "aucune clé configurée sur le serveur (GROQ_API_KEY, GEMINI_API_KEY ou ANTHROPIC_API_KEY)")
    img = await request.body()
    if not img:
        raise HTTPException(400, "image vide")
    media = request.headers.get("content-type", "image/jpeg").split(";")[0]
    b64 = base64.b64encode(img).decode()
    # retries=3 : réessaie seul en cas d'échec de connexion ; local_address=0.0.0.0 : force IPv4 (évite les lenteurs IPv6 de Docker)
    async with httpx.AsyncClient(timeout=90, transport=httpx.AsyncHTTPTransport(retries=3, local_address="0.0.0.0")) as h:
        if qkey:  # Groq (offre gratuite)
            model = os.getenv("GROQ_MODEL") or "meta-llama/llama-4-scout-17b-16e-instruct"
            r = await _groq(h, qkey, model, media, b64)
            if r.status_code in (400, 404) and not os.getenv("GROQ_MODEL"):
                # modèle retiré : on essaie les modèles « vision » disponibles pour cette clé
                lm = await h.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {qkey}"})
                ids = [x["id"] for x in (lm.json().get("data", []) if lm.status_code == 200 else [])]
                bad = ("whisper", "tts", "orpheus", "guard", "safeguard", "embed", "compound", "gpt-oss", "distill")
                pref = ("maverick", "scout", "vision", "vl", "qwen")
                cands = sorted((i for i in ids if i != model and not any(x in i for x in bad) and any(p in i for p in pref)),
                               key=lambda i: min(k for k, p in enumerate(pref) if p in i))
                for cand in cands[:5]:
                    r = await _groq(h, qkey, cand, media, b64)
                    if r.status_code == 200:
                        print("Groq : modèle de vision utilisé =", cand, flush=True)
                        break
            if r.status_code == 429:
                raise HTTPException(429, "limite gratuite atteinte, réessayez dans une minute")
            if r.status_code != 200:
                raise HTTPException(502, f"erreur Groq ({r.status_code}) : {r.text[:150]}")
            text = r.json()["choices"][0]["message"].get("content") or ""
        elif gkey:  # Google Gemini (offre gratuite)
            model = os.getenv("GEMINI_MODEL") or "gemini-flash-latest"
            hdr = {"x-goog-api-key": gkey}
            body = {"contents": [{"parts": [{"inline_data": {"mime_type": media, "data": b64}},
                                            {"text": SCAN_PROMPT}]}],
                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                         "thinkingConfig": {"thinkingBudget": 0}}}
            base = "https://generativelanguage.googleapis.com/v1beta/models"
            r = await h.post(f"{base}/{model}:generateContent", headers=hdr, json=body)
            if r.status_code == 400 and "thinking" in r.text.lower():  # modèle sans option « réflexion »
                body["generationConfig"].pop("thinkingConfig")
                r = await h.post(f"{base}/{model}:generateContent", headers=hdr, json=body)
            if r.status_code == 404:  # modèle retiré : on prend un modèle « flash » disponible pour cette clé
                lm = await h.get(f"{base}?pageSize=100", headers=hdr)
                names = sorted(
                    x["name"].split("/")[-1] for x in (lm.json().get("models", []) if lm.status_code == 200 else [])
                    if "generateContent" in x.get("supportedGenerationMethods", []) and "flash" in x["name"]
                    and not any(t in x["name"] for t in ("image", "tts", "live", "audio", "lite", "embedding")))
                if names:
                    r = await h.post(f"{base}/{names[-1]}:generateContent", headers=hdr, json=body)
            if r.status_code == 429:
                raise HTTPException(429, "limite gratuite atteinte, réessayez plus tard")
            if r.status_code != 200:
                raise HTTPException(502, f"erreur Gemini ({r.status_code}) : {r.text[:150]}")
            parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
        else:  # Anthropic Claude
            r = await h.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": akey, "anthropic-version": "2023-06-01"},
                json={"model": os.getenv("SCAN_MODEL") or "claude-sonnet-5-5", "max_tokens": 2000,
                      "messages": [{"role": "user", "content": [
                          {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                          {"type": "text", "text": SCAN_PROMPT}]}]})
            if r.status_code != 200:
                raise HTTPException(502, f"erreur API Anthropic ({r.status_code})")
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return reconcile(json.loads(m.group(0)))
    except Exception:
        raise HTTPException(502, "réponse illisible, réessayez avec une photo plus nette")
