"""seerr-bridge: envía las peticiones aprobadas de Seerr a la cola de XtreamFilter.

Solo usa la biblioteca estándar de Python. Ajustes y estado en /data.
"""
import json
import os
import re
import shutil
import subprocess
import sqlite3
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.2.12"

DATA_DIR = os.environ.get("DATA_DIR", "/data")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
DB_PATH = os.path.join(DATA_DIR, "bridge.db")
PORT = int(os.environ.get("PORT", "5056"))

DEFAULTS = {
    "seerr_url": "http://10.10.10.16:5055",
    "seerr_api_key": "",
    "jellyfin_url": "http://10.10.10.10:8096",
    "jellyfin_api_key": "",
    "xtreamfilter_url": "http://10.10.10.16:5000",
    "playlist_url": "http://10.10.10.16:5000/merged/player_api.php",
    "playlist_user": "USER",
    "playlist_pass": "PASS",
    "lang_prefixes": '"ES -","LA -","ESP","ES-"',   # títulos o grupos que empiezan así = idioma preferido
    "audio_langs": "SPA",          # códigos de idioma de la pista de audio (separados por comas)
    "audio_track": "any",          # any | main_first | main_only
    "sub_langs": "SPA",            # códigos de idioma de los subtítulos
    "exclude_4k": True,
    "retry_hours": 24,
    "poll_minutes": 5,
    "dry_run": True,
    "monitor_returning": True,
    "jellyfin_refresh": True,
    "prioritize": True,
    "auto_retry": True,
    "retry_max": 3,
    "probe_audio": True,
    "probe_subs": True,
    "decline_unavailable": True,
    "notify_jellyfin": True,
    "notify_days": 7,
    "msg_title": "Solicitud rechazada",
    "msg_not_found": "«{titulo}» no está disponible: no existe en el catálogo del proveedor.",
    "msg_no_language": "«{titulo}» no está disponible en español por el momento.",
    "shrinkerr_enabled": False,
    "shrinkerr_url": "http://10.10.10.16:6680",
    "shrinkerr_api_key": "",
    "shrinkerr_paths": "/downloads/movies = /media/hdd1/movies\n/downloads/series = /media/hdd2/series",
    "missing_enabled": False,
    "missing_autoadd": False,
    "missing_from": "17:05",
    "missing_to": "23:00",
}
SECRET_KEYS = ("seerr_api_key", "jellyfin_api_key", "shrinkerr_api_key", "playlist_pass")
VIRTUAL_ID_OFFSET = 10_000_000
RETRYABLE = ("failed", "cancelled", "move_failed")
MAX_PROBES = 8        # versiones que se analizan como máximo por petición
FREE_SETTLE = 3       # segundos tras cancelar la descarga en curso (se cierra su conexión)
CANCEL_WAIT = 30      # espera máxima a que XtreamFilter confirme la cancelación
PROBE_CACHE_HOURS = 24
MISSING_DELAY = 5     # segundos entre consulta y consulta al proveedor en la búsqueda de episodios
MISSING_SETTLE = 15   # segundos de espera tras pausar la cola y antes de reanudarla (conexión libre)
MISSING_RETRY = 30 * 60  # si la búsqueda se cancela, se vuelve a intentar a los 30 min (dentro de la franja)
NET_RETRY_WAIT = 30   # error de red/DNS al consultar al proveedor: se espera y se repite una vez
MISSING_MAX_ERRORS = 2  # fallos seguidos del proveedor que cancelan la búsqueda
_probe_cache = {}     # url -> (cuándo, audio, subtítulos)
AUDIO_TRACK_MODES = ("any", "main_first", "main_only")
# /merged/ de XtreamFilter: id = índice_de_fuente * 10.000.000 + id original
ENDED_STATUSES = ("Ended", "Canceled", "Cancelled")

# Estados de una petición en el puente
ST_QUEUED = "en_cola"        # añadida a XtreamFilter, esperando descarga
ST_WAITING = "esperando"     # sin versión válida en el proveedor; se reintenta
ST_DONE = "completada"       # disponible / ya descargada / nada que hacer
ST_ERROR = "error"           # fallo; se reintenta
ST_DRY = "prueba"            # modo prueba: solo se muestra lo que haría
ST_DECLINED = "rechazada"    # rechazada en Seerr por no estar disponible
ST_CONVERTING = "convirtiendo"  # ya en Jellyfin, shrinkerr aún no ha terminado

# Estado de cada archivo en shrinkerr (columna added.shrink)
SH_ACTIVE = ("enviado", "en cola", "convirtiendo")

_lock = threading.Lock()
_wake = threading.Event()
_status = {"last_run": None, "next_run": None, "running": False, "last_error": None, "version": VERSION}


# ---------------------------------------------------------------------------
# Ajustes y base de datos
# ---------------------------------------------------------------------------

def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            s.update(json.load(f))
    except FileNotFoundError:
        pass
    return s


def save_settings(new):
    s = load_settings()
    url = str(new.get("playlist_url") or "")
    if "?" in url:  # dirección completa pegada: separar usuario y contraseña
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        new = dict(new, playlist_url=url.split("?", 1)[0])
        if q.get("username"):
            new["playlist_user"] = q["username"][0]
        if q.get("password"):
            new["playlist_pass"] = q["password"][0]
    for k, default in DEFAULTS.items():
        if k not in new:
            continue
        v = new[k]
        if k in SECRET_KEYS and not v:
            continue  # vacío = conservar la clave guardada
        if isinstance(default, bool):
            v = bool(v)
        elif isinstance(default, int):
            v = max(1, int(v))
        else:
            v = str(v).strip().rstrip("/") if k.endswith("_url") else str(v).strip()
        s[k] = v
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS_PATH)
    return s


def public_settings(s):
    out = {k: v for k, v in s.items() if k not in SECRET_KEYS}
    for k in SECRET_KEYS:
        out[k + "_set"] = bool(s.get(k))
    return out


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY, media_type TEXT, tmdb_id TEXT, title TEXT,
            seasons TEXT, state TEXT, detail TEXT, choice TEXT,
            next_retry REAL DEFAULT 0, updated REAL);
        CREATE TABLE IF NOT EXISTS added (
            content_type TEXT, stream_id TEXT, request_id INTEGER, name TEXT,
            added REAL, refreshed INTEGER DEFAULT 0,
            PRIMARY KEY (content_type, stream_id));
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, msg TEXT);
        """)
        c.execute("""CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, jf_user TEXT,
            user_name TEXT, header TEXT, text TEXT, created REAL, delivered REAL)""")
        cols = {r[1] for r in c.execute("PRAGMA table_info(added)")}
        if "cart_id" not in cols:
            c.execute("ALTER TABLE added ADD COLUMN cart_id TEXT")
        cols = {r[1] for r in c.execute("PRAGMA table_info(requests)")}
        for col in ("user_name", "jf_user", "monitor_id"):
            if col not in cols:
                c.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")
        cols = {r[1] for r in c.execute("PRAGMA table_info(added)")}
        for col in ("shrink", "shrink_path"):
            if col not in cols:
                c.execute(f"ALTER TABLE added ADD COLUMN {col} TEXT")
        if "adopted" not in cols:
            c.execute("ALTER TABLE added ADD COLUMN adopted INTEGER DEFAULT 0")
        # dl: estado de la descarga según la cola de XtreamFilter (NULL = pendiente,
        # 'completado', 'no está'); file_path: ruta que da la cola al completarse
        if "retries" not in cols:
            c.execute("ALTER TABLE added ADD COLUMN retries INTEGER DEFAULT 0")
        # Reintentos de cualquier descarga con error en la cola (de Seerr o no)
        c.execute("""CREATE TABLE IF NOT EXISTS retries (
            cart_id TEXT PRIMARY KEY, name TEXT, count INTEGER DEFAULT 0,
            exhausted INTEGER DEFAULT 0, updated REAL)""")
        for col in ("dl", "file_path", "season", "episode"):
            if col not in cols:
                c.execute(f"ALTER TABLE added ADD COLUMN {col} TEXT")
                if col == "dl":  # lo ya enviado a shrinkerr se da por descargado; el resto lo comprueba la cola
                    c.execute("UPDATE added SET dl = 'completado' WHERE shrink IS NOT NULL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS missing (
            folder TEXT PRIMARY KEY, series_name TEXT, series_id TEXT, source_id TEXT,
            icon TEXT, grp TEXT, episodes TEXT, count INTEGER, state TEXT, detail TEXT, found REAL);
        CREATE TABLE IF NOT EXISTS ignored (folder TEXT PRIMARY KEY, added REAL);
        CREATE TABLE IF NOT EXISTS complete (folder TEXT PRIMARY KEY, tmdb TEXT, marked REAL, checked REAL);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)


def log(msg, level="info"):
    print(f"[{level}] {msg}", flush=True)
    with db() as c:
        c.execute("INSERT INTO log (ts, level, msg) VALUES (?, ?, ?)", (time.time(), level, msg))
        c.execute("DELETE FROM log WHERE id < (SELECT MAX(id) - 1000 FROM log)")


def set_request(rid, **fields):
    fields["updated"] = time.time()
    with db() as c:
        c.execute("INSERT OR IGNORE INTO requests (id) VALUES (?)", (rid,))
        cols = ", ".join(f"{k} = ?" for k in fields)
        c.execute(f"UPDATE requests SET {cols} WHERE id = ?", (*fields.values(), rid))


def get_request(rid):
    with db() as c:
        row = c.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http(method, url, headers=None, body=None, timeout=60):
    data = None
    hdrs = {"Accept": "application/json"}
    hdrs.update(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode(errors="replace")[:300]
    return status, payload


def is_net_error(st, payload):
    """Fallo pasajero de red o de DNS (p. ej. "No address associated with hostname"),
    no un bloqueo del proveedor ni un error de la petición."""
    text = str(payload or "")
    return st == 0 or "[Errno -" in text or "name resolution" in text.lower() or "No address associated" in text


def jf_auth(s):
    """Cabecera con la clave de Jellyfin. Jellyfin 12 ya no acepta X-Emby-Token; esta forma
    (Authorization: MediaBrowser Token=…) funciona en Jellyfin 10 y 12."""
    return {"Authorization": f'MediaBrowser Token="{s["jellyfin_api_key"]}"'}


class Seerr:
    def __init__(self, s):
        self.base = s["seerr_url"].rstrip("/") + "/api/v1"
        self.h = {"X-Api-Key": s["seerr_api_key"]}

    def get(self, path):
        st, p = http("GET", self.base + path, self.h)
        if st != 200:
            raise RuntimeError(f"Seerr GET {path} -> {st}: {p}")
        return p

    def requests(self, flt):
        out, skip = [], 0
        while True:
            p = self.get(f"/request?take=50&skip={skip}&filter={flt}&sort=added")
            out.extend(p.get("results", []))
            skip += 50
            if skip >= p.get("pageInfo", {}).get("results", 0):
                return sorted(out, key=lambda r: r["id"])  # de la más antigua a la más nueva

    def request(self, rid):
        st, p = http("GET", f"{self.base}/request/{rid}", self.h)
        return p if st == 200 else None

    def movie(self, tmdb):
        return self.get(f"/movie/{tmdb}")

    def tv(self, tmdb):
        return self.get(f"/tv/{tmdb}")

    def mark_processing(self, media_id):
        http("POST", f"{self.base}/media/{media_id}/processing", self.h)

    def set_status(self, rid, status):
        st, p = http("POST", f"{self.base}/request/{rid}/{status}", self.h)
        if st != 200:
            raise RuntimeError(f"Seerr no aceptó '{status}' para la petición {rid} ({st}): {p}")
        return p

    def delete_request(self, rid):
        st, p = http("DELETE", f"{self.base}/request/{rid}", self.h)
        return st in (200, 204, 404)


class XF:
    """XtreamFilter.

    - Catálogo (películas, series, episodios): lista de títulos en formato Xtream Codes
      (player_api.php), ya filtrada con las Filter Rules de XtreamFilter.
    - Cola, historial y seguimiento: API de gestión de XtreamFilter (xtreamfilter_url).
    """

    def __init__(self, s):
        self.base = s["xtreamfilter_url"].rstrip("/")
        self.playlist = s["playlist_url"].rstrip("/")
        self.creds = {"username": s["playlist_user"], "password": s["playlist_pass"]}
        self._route = None
        self._cart = None
        self._catalog = {}
        self._enabled = None
        self._eps = {}

    def xtream(self, action, **params):
        q = urllib.parse.urlencode({**self.creds, "action": action, **params})
        for attempt in (1, 2):
            try:
                st, p = http("GET", f"{self.playlist}?{q}", timeout=120)
            except OSError as e:  # sin conexión con XtreamFilter (red, DNS)
                st, p = 0, str(e)
            if st == 200:
                return p
            if attempt == 1 and is_net_error(st, p):
                log(f"Error de red/DNS al consultar al proveedor ({action}): {p}. Se repite en {NET_RETRY_WAIT} s",
                    "error")
                time.sleep(NET_RETRY_WAIT)
                continue
            raise RuntimeError(f"Lista de títulos ({action}) -> {st}: {p}")

    def source_of(self, virtual_id):
        """Id de la lista -> (source_id de XtreamFilter, id original)."""
        vid = int(virtual_id)
        if self._enabled is None:
            self._enabled = [str(x.get("id")) for x in self.sources() if x.get("enabled", True)]
        idx, orig = divmod(vid, VIRTUAL_ID_OFFSET)
        if idx >= len(self._enabled):
            raise RuntimeError(f"Fuente {idx} de la lista no encontrada en XtreamFilter")
        return self._enabled[idx], str(orig)

    def catalog(self, kind):
        """Todos los títulos de la lista (una sola descarga por revisión)."""
        if kind in self._catalog:
            return self._catalog[kind]
        if kind == "vod":
            cats = {str(c["category_id"]): c.get("category_name") for c in self.xtream("get_vod_categories")}
            raw, key, icon, added = self.xtream("get_vod_streams"), "stream_id", "stream_icon", "added"
        else:
            cats = {str(c["category_id"]): c.get("category_name") for c in self.xtream("get_series_categories")}
            raw, key, icon, added = self.xtream("get_series"), "series_id", "cover", "last_modified"
        items = []
        for r in raw or []:
            try:
                source_id, orig = self.source_of(r[key])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                when = int(r.get(added) or 0)
            except (TypeError, ValueError):
                when = 0
            items.append({"id": orig, "vid": str(r[key]), "source_id": source_id, "name": r.get("name") or "",
                          "group": cats.get(str(r.get("category_id")), ""), "icon": r.get(icon) or "",
                          "tmdb_id": str(r.get("tmdb") or "").strip(), "added": when,
                          "container_extension": r.get("container_extension") or "mp4"})
        self._catalog[kind] = items
        return items

    def get(self, path):
        st, p = http("GET", self.base + path, timeout=120)
        if st != 200:
            raise RuntimeError(f"XtreamFilter GET {path} -> {st}: {p}")
        return p

    def post(self, path, body):
        self._cart = None  # la cola cambia: volver a leerla la próxima vez
        return http("POST", self.base + path, body=body, timeout=180)

    def sources(self):
        p = self.get("/api/sources")
        return p.get("sources", p) if isinstance(p, dict) else p

    def route_for(self, source_id):
        if self._route is None:
            self._route = {str(x.get("id")): x.get("route") for x in self.sources()}
        if str(source_id) in self._route:
            return self._route[str(source_id)]
        raise RuntimeError(f"Fuente {source_id} no encontrada en XtreamFilter")

    def by_tmdb(self, kind, tmdb):
        tmdb = str(tmdb or "").strip()
        return [i for i in self.catalog(kind) if tmdb and i["tmdb_id"] == tmdb]

    def series_episodes(self, source_id, series_id):
        key = (str(source_id), str(series_id))
        if key not in self._eps:
            self._eps[key] = self._series_episodes(source_id, series_id)
        return self._eps[key]

    def _series_episodes(self, source_id, series_id):
        if self._enabled is None:
            self.source_of(0)
        vid = self._enabled.index(str(source_id)) * VIRTUAL_ID_OFFSET + int(series_id)
        p = self.xtream("get_series_info", series_id=vid)
        eps = []
        for season, lst in (p.get("episodes") or {}).items():
            for e in lst:
                eps.append({"id": self.source_of(e.get("id"))[1], "vid": str(e.get("id")),
                            "season": int(e.get("season") or season), "episode": int(e.get("episode_num") or 0),
                            "title": e.get("title", ""), "ext": e.get("container_extension") or "mkv"})
        return eps

    def cart(self):
        """Cola de descargas (una sola lectura por objeto): en espera, descargando,
        completado (con su ruta) o con error."""
        if self._cart is None:
            self._cart = self.get("/api/cart").get("items", [])
        return self._cart

    def fresh(self):
        self._cart = None

    def reorder(self, item_ids):
        return http("POST", self.base + "/api/cart/reorder", body={"item_ids": item_ids}, timeout=60)

    def status(self):
        return self.get("/api/cart/status")

    def pause(self):
        st, _ = http("POST", self.base + "/api/cart/pause")
        return st == 200

    def resume(self):
        st, _ = http("POST", self.base + "/api/cart/resume")
        return st == 200

    def retry_all(self):
        self._cart = None
        st, _ = http("POST", self.base + "/api/cart/retry-all")
        return st == 200

    def stream_url(self, kind, vid, ext):
        """Dirección estándar Xtream del archivo (a través de la lista de títulos)."""
        base = re.sub(r"/player_api\.php$", "", self.playlist)
        q = urllib.parse.quote
        return (f"{base}/{'movie' if kind == 'vod' else 'series'}/{q(self.creds['username'])}/"
                f"{q(self.creds['password'])}/{vid}.{ext or 'mkv'}")

    def cancel(self):
        """Cancela la descarga activa (/api/cart/cancel)."""
        self._cart = None
        st, _ = http("POST", self.base + "/api/cart/cancel")
        return st == 200

    def retry(self, item_id):
        self._cart = None
        st, p = http("POST", f"{self.base}/api/cart/{item_id}/retry")
        return st == 200

    def remove_cart_item(self, item_id):
        self._cart = None
        st, _ = http("DELETE", f"{self.base}/api/cart/{item_id}")
        return st in (200, 404)

    def remove_monitor(self, monitor_id):
        st, _ = http("DELETE", f"{self.base}/api/monitor/{monitor_id}")
        return st in (200, 404)

    def monitors(self):
        p = self.get("/api/monitor")
        return p.get("series", p) if isinstance(p, dict) else p


# ---------------------------------------------------------------------------
# Elección de versión
# ---------------------------------------------------------------------------

def is_4k(item):
    name = (item.get("name") or "").upper()
    group = item.get("group") or ""
    return name.startswith("4K") or "⁴ᴷ" in group or "4K" in group.upper()


def prefixes(s):
    """'"ES -","LA -","ESP","ES-"' -> ['ES -', 'LA -', 'ESP', 'ES-'] (tal cual, en orden).
    Sin comillas, se separa por comas."""
    raw = s.get("lang_prefixes") or ""
    found = re.findall(r'"([^"]*)"', raw)
    if not found:
        found = [x.strip() for x in raw.split(",")]
    return [x for x in found if x]


def codes(text):
    """'SPA, es' -> {'spa', 'es'} (sin distinguir mayúsculas)."""
    return {x.strip().lower() for x in (text or "").split(",") if x.strip()}


def prefix_match(item, s):
    """(posición, prefijo, "título"/"grupo") del primer prefijo por el que empieza el título
    o el grupo; None si ninguno."""
    name, group = item.get("name") or "", item.get("group") or ""
    for n, pre in enumerate(prefixes(s)):
        if name.startswith(pre):
            return n, pre, "título"
        if group.startswith(pre):
            return n, pre, "grupo"
    return None


def pick(items, s):
    """Paso 1: versión cuyo título o grupo empieza por un prefijo del idioma preferido.
    Si varias coinciden, gana el prefijo que está antes en la lista y después la más reciente."""
    ok = [(prefix_match(i, s), i) for i in items if not (s["exclude_4k"] and is_4k(i))]
    ok = [(m, i) for m, i in ok if m is not None]
    ok.sort(key=lambda x: (x[0][0], -(x[1].get("added") or 0)))
    return (ok[0][1], ok[0][0]) if ok else (None, None)


# ---------------------------------------------------------------------------
# Procesado de peticiones
# ---------------------------------------------------------------------------

@contextmanager
def free_connection(xf):
    """Libera la única conexión con el proveedor mientras el puente lo consulta.

    Si hay una descarga a medias (aunque esté en pausa, ocupa la conexión): pausa la cola
    para que no empiece la siguiente, cancela esa descarga, y al terminar la reintenta (vuelve
    a "en espera" en su sitio, sin contar como reintento) y reanuda la cola si la pausó el
    puente. Si no hay ninguna descarga a medias, no toca nada."""
    mine, cancelled = False, None
    try:
        st = xf.status() or {}
        if st.get("downloading") or st.get("current"):
            if not st.get("queue_paused") and xf.pause():
                mine = True
            xf.fresh()
            cur = next((i for i in xf.cart() if i.get("status") == "downloading"), None)
            if xf.cancel():
                cancelled = cur
                deadline = time.time() + CANCEL_WAIT
                while time.time() < deadline and (xf.status() or {}).get("downloading"):
                    time.sleep(1)
                log("Conexión con el proveedor liberada: descarga cancelada"
                    + (f" ({cur.get('name')})" if cur else "") + "; se reintentará al terminar")
            time.sleep(FREE_SETTLE)
        yield
    finally:
        if cancelled is not None and cancelled.get("id"):
            if xf.retry(cancelled["id"]):
                log(f"Descarga cancelada reintentada: {cancelled.get('name')}")
            else:
                xf.fresh()
                now = next((i for i in xf.cart() if i.get("id") == cancelled["id"]), {})
                if now.get("status") in ("queued", "downloading"):
                    log(f"Descarga cancelada ya en espera (XtreamFilter la reintentó): {cancelled.get('name')}")
        if mine:
            xf.resume()
            log("Cola de XtreamFilter reanudada")


def probe(url, s):
    """(pista principal en un idioma de audio, alguna pista en un idioma de audio,
    subtítulos completos en un idioma de subtítulos), según los códigos de los ajustes.
    Las pistas del archivo se recuerdan PROBE_CACHE_HOURS para no volver a pausar la cola."""
    hit = _probe_cache.get(url)
    if hit and time.time() - hit[0] < PROBE_CACHE_HOURS * 3600:
        streams = hit[1]
    else:
        streams = _probe(url)
        _probe_cache[url] = (time.time(), streams)
    return evaluate(streams, s)


def evaluate(streams, s):
    lang = lambda st: ((st.get("tags") or {}).get("language") or "").lower()  # noqa: E731
    audio_codes, sub_codes = codes(s["audio_langs"]), codes(s["sub_langs"])
    audios = [st for st in streams if st.get("codec_type") == "audio"]
    main = next((st for st in audios if (st.get("disposition") or {}).get("default")), audios[0] if audios else None)
    audio_main = main is not None and lang(main) in audio_codes
    audio_any = any(lang(st) in audio_codes for st in audios)
    subs = any(st.get("codec_type") == "subtitle" and lang(st) in sub_codes
               and not (st.get("disposition") or {}).get("forced")
               and not any(w in ((st.get("tags") or {}).get("title") or "").lower() for w in ("forced", "forzad"))
               for st in streams)
    return audio_main, audio_any, subs


def cached_probe(url):
    hit = _probe_cache.get(url)
    return hit is not None and time.time() - hit[0] < PROBE_CACHE_HOURS * 3600


def _probe(url):
    """Pistas (audio, subtítulos…) del archivo con su idioma, con ffprobe."""
    r = subprocess.run(["ffprobe", "-v", "error", "-user_agent", "Mozilla/5.0",
                        "-show_entries", "stream=codec_type:stream_disposition=forced,default:stream_tags=language,title",
                        "-of", "json", url], capture_output=True, text=True, timeout=45)
    return json.loads(r.stdout or "{}").get("streams", [])


def year_ok(v, year):
    """Protección contra errores del catálogo (otra serie con el mismo TMDB): si el nombre
    lleva año y no coincide (±1) con el de TMDB, no se acepta."""
    years = [int(y) for y in re.findall(r"\((\d{4})\)", v.get("name") or "")]
    return not year or not years or any(abs(y - year) <= 1 for y in years)


def choose_series(versions, s, xf, tmdb, seasons, title, year=None):
    """Elige la versión de una serie pedida en Seerr. Devuelve (versión o None, motivo, nota).

    1) Prefijo del idioma preferido, pero solo entre las versiones que TIENEN algo de lo
       pedido que falta (no está en Jellyfin ni en la cola).
    2) Si ninguna versión con prefijo lo tiene: las demás versiones que lo tienen, sea cual
       sea su prefijo (y con el año correcto), por audio y después subtítulos.
    3) Si ninguna versión tiene nada nuevo: como antes (prefijo → audio → subtítulos)."""
    key = ("series_choice", str(tmdb), tuple(sorted(v["vid"] for v in versions)), tuple(seasons or ()))
    if key not in _cycle:
        _cycle[key] = _choose_series(versions, s, xf, tmdb, seasons, title, year)
    return _cycle[key]


def _choose_series(versions, s, xf, tmdb, seasons, title, year):
    ok4k = [v for v in versions if not (s["exclude_4k"] and is_4k(v))]
    jf = jf_index(s)
    have = set(jf["series"].get(str(tmdb), set())) if jf is not None else set()
    ids = {str(v["id"]) for v in versions}
    have |= {(int(i.get("season") or 0), int(i.get("episode_num") or 0)) for i in xf.cart()
             if i.get("content_type") == "series" and str(i.get("series_id")) in ids}
    seasons = set(seasons or ())

    def new_eps(v):
        eps = [e for e in xf.series_episodes(v["source_id"], v["id"])
               if e["season"] > 0 and (not seasons or e["season"] in seasons)
               and (e["season"], e["episode"]) not in have]
        return sorted(eps, key=lambda e: (e["season"], e["episode"]))

    pre = sorted([(prefix_match(v, s), v) for v in ok4k if prefix_match(v, s) is not None],
                 key=lambda x: (x[0][0], -(x[1].get("added") or 0)))
    rest = [v for v in ok4k if prefix_match(v, s) is None]
    with free_connection(xf):
        for m, v in pre:
            if new_eps(v):
                return v, f"prefijo «{m[1]}» en el {m[2]}", ""
        useful, skipped = {}, []
        for v in sorted(rest, key=lambda v: -(v.get("added") or 0)):
            if not year_ok(v, year):
                skipped.append(v["name"])
                continue
            eps = new_eps(v)
            if eps:
                useful[v["vid"]] = eps[0]
        if skipped:
            log(f"«{title}»: versiones descartadas por no coincidir el año ({year}): " + ", ".join(skipped))
        if useful:
            cands = [v for v in rest if v["vid"] in useful]
            prefix_note = ("Ninguna versión con prefijo del idioma preferido tiene lo pedido" if pre
                           else "Sin versión con prefijo del idioma preferido")
            c, why, note = _choose(cands, s, xf, "series", seasons, title, first_ep=useful, nopre=prefix_note)
            if c or not pre:
                return c, why, note
            # Ninguna sirve: la versión con prefijo (seguirá esperando lo que falta)
            c, why, _ = _choose(ok4k, s, xf, "series", seasons, title)
            return c, why, note
        # Ninguna versión tiene nada nuevo de lo pedido: se elige como siempre
        # (después se indicará "ya descargado" o "el proveedor aún no tiene")
        return _choose(ok4k, s, xf, "series", seasons, title)


def choose(versions, s, xf, kind, seasons=None, title=""):
    """Elige la versión: 1) prefijo del idioma preferido  2) audio en los idiomas indicados
    3) subtítulos completos en los idiomas indicados. Devuelve (versión o None, motivo, nota).
    Se calcula una vez por revisión."""
    key = ("choice", kind, tuple(sorted(v["vid"] for v in versions)), tuple(seasons or ()))
    if key not in _cycle:
        _cycle[key] = _choose(versions, s, xf, kind, seasons, title)
    return _cycle[key]


@contextmanager
def _no_pause():
    yield


def _analysis_note(results, nopre=None):
    n = len(results)
    head = f"Analizada 1 versión" if n == 1 else f"Analizadas {n} versiones"
    return f"{nopre or 'Sin versión con prefijo del idioma preferido'}. {head}: " + ", ".join(results)


def _choose(versions, s, xf, kind, seasons, title, first_ep=None, nopre=None):
    """first_ep: {vid: episodio} que se analiza de cada versión (series): el primero de lo pedido
    que falta. nopre: texto del Detalle cuando no se usa una versión con prefijo."""
    ok4k = lambda v: not (s["exclude_4k"] and is_4k(v))  # noqa: E731
    c, m = pick(versions, s)  # 1) prefijo: si coincide, se deja de buscar
    if c:
        return c, f"prefijo «{m[1]}» en el {m[2]}", ""
    nopre = nopre or "Sin versión con prefijo del idioma preferido"
    if not (s["probe_audio"] or s["probe_subs"]):
        return None, "", f"{nopre} (el análisis de audio y subtítulos está desactivado)"
    if not shutil.which("ffprobe"):
        log("No se pueden analizar otras versiones: ffprobe no está instalado en el puente", "error")
        return None, "", f"{nopre} (no se puede analizar: falta ffprobe en el puente)"
    others = sorted([v for v in versions if ok4k(v)], key=lambda v: -(v.get("added") or 0))[:MAX_PROBES]
    if not others:
        return None, "", f"{nopre} (solo hay versiones 4K)"
    alang, slang = s["audio_langs"].upper(), s["sub_langs"].upper()
    mode = s["audio_track"] if s["audio_track"] in AUDIO_TRACK_MODES else "any"
    log(f"Analizando {len(others)} {'versión' if len(others) == 1 else 'versiones'} de «{title}» "
        f"(audio {alang} / subtítulos {slang})…")
    with_audio, with_subs, results = None, None, []
    urls = {}
    if kind == "vod":  # si todo está ya analizado (memoria de 24 h), no hace falta pausar
        urls = {v["vid"]: xf.stream_url("vod", v["vid"], v.get("container_extension")) for v in others}
    need_pause = kind != "vod" or not all(cached_probe(u) for u in urls.values())
    with (free_connection(xf) if need_pause else _no_pause()):
        for v in others:
            try:
                if kind == "vod":
                    url = xf.stream_url("vod", v["vid"], v.get("container_extension"))
                elif first_ep and v["vid"] in first_ep:
                    ep = first_ep[v["vid"]]
                    url = xf.stream_url("series", ep["vid"], ep["ext"])
                else:
                    eps = [e for e in xf.series_episodes(v["source_id"], v["id"])
                           if not seasons or e["season"] in seasons] or xf.series_episodes(v["source_id"], v["id"])
                    if not eps:
                        results.append(f"{v['name']} (sin episodios)")
                        continue
                    eps.sort(key=lambda e: (e["season"], e["episode"]))
                    url = xf.stream_url("series", eps[0]["vid"], eps[0]["ext"])
                audio_main, audio_any, subs = probe(url, s)
            except Exception as e:  # noqa: BLE001
                log(f"No se pudo analizar {v['name']}: {e}", "error")
                results.append(f"{v['name']} (no se pudo analizar)")
                continue
            log(f"   {v['name']} | {v.get('group')}: audio principal {alang} {'sí' if audio_main else 'no'}, "
                f"alguna pista {alang} {'sí' if audio_any else 'no'}, subtítulos {slang} {'sí' if subs else 'no'}")
            if s["probe_audio"]:
                # 2) audio: según qué pista cuenta
                if (mode == "any" and audio_any) or (mode != "any" and audio_main):
                    results.append(f"{v['name']} (audio {alang} ✓)")
                    return v, f"audio {alang}", _analysis_note(results, nopre)
                if mode == "main_first" and audio_any:
                    results.append(f"{v['name']} (pista secundaria {alang})")
                    with_audio = with_audio or v
                    continue
            if subs:
                results.append(f"{v['name']} (solo subtítulos {slang})")
                with_subs = with_subs or v
            else:
                results.append(f"{v['name']} (sin audio ni subtítulos {alang}/{slang})")
    if with_audio is not None:
        return with_audio, f"pista de audio {alang}", _analysis_note(results, nopre)
    if with_subs is not None and s["probe_subs"]:  # 3) subtítulos
        return with_subs, f"subtítulos {slang}", _analysis_note(results, nopre)
    return None, "", _analysis_note(results, nopre)


def remember_added(content_type, cart_items, rid, name, adopted=False):
    """Guarda lo añadido a la cola (stream_id + id del elemento en la cola).

    adopted=True: ya estaba en la cola antes de la petición; se sigue igual
    (prioridad, shrinkerr) pero "Cancelar" no lo quita de la cola.
    """
    with db() as c:
        c.executemany(
            "INSERT OR IGNORE INTO added (content_type, stream_id, request_id, name, added, cart_id, adopted,"
            " season, episode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(content_type, str(i.get("stream_id")), rid, name, time.time(), i.get("id"), int(adopted),
              i.get("season"), i.get("episode_num")) for i in cart_items])


def prioritize(xf, s):
    """Pone lo pedido en Seerr por delante del resto de la cola, en el orden en que se pidió."""
    if not s["prioritize"]:
        return
    with db() as c:
        ours = {r[0] for r in c.execute("SELECT cart_id FROM added WHERE cart_id IS NOT NULL")}
    for attempt in range(3):
        xf.fresh()
        queued = [str(i["id"]) for i in xf.cart() if i.get("status") == "queued"]
        order = [i for i in queued if i in ours] + [i for i in queued if i not in ours]
        if order == queued:
            return
        st, p = xf.reorder(order)
        if st == 200:
            log(f"Cola reordenada: {sum(1 for i in queued if i in ours)} elementos de Seerr al principio")
            return
        time.sleep(2)
    log(f"No se pudo reordenar la cola (la cola cambiaba): {p}", "error")


_cycle = {}  # datos que se cargan una vez por revisión (se vacía al empezar cada una)


def jf_snapshot(s):
    """Lo que hay en el disco según Jellyfin.

    series: {tmdb de la serie: {(temporada, episodio)}} de todas sus carpetas.
    movies: {tmdb} de todas las películas.
    Devuelve None si no hay clave de Jellyfin.
    """
    if not s["jellyfin_api_key"]:
        return None
    series, folders = {}, {}
    for folder, info in jellyfin_series(s).items():
        if info["tmdb"]:
            tmdb = str(info["tmdb"])
            series.setdefault(tmdb, set()).update(info["have"])
            folders.setdefault(tmdb, []).append((len(info["have"]), folder))
    st, p = http("GET", s["jellyfin_url"].rstrip("/") + "/Items?IncludeItemTypes=Movie&Recursive=true"
                 "&Fields=ProviderIds&EnableImages=false&EnableUserData=false",
                 jf_auth(s), timeout=300)
    if st != 200:
        raise RuntimeError(f"Jellyfin respondió {st} al pedir las películas")
    movies = {str((i.get("ProviderIds") or {}).get("Tmdb")) for i in p.get("Items", [])
              if (i.get("ProviderIds") or {}).get("Tmdb")}
    # Carpeta de cada serie (si hay varias, la que tiene más episodios)
    folders = {t: max(lst)[1] for t, lst in folders.items()}
    return {"series": series, "movies": movies, "folders": folders}


def jf_index(s):
    """jf_snapshot cargado una sola vez por revisión."""
    if "jf" not in _cycle:
        _cycle["jf"] = jf_snapshot(s)
    return _cycle["jf"]


def process_movie(req, s, seerr, xf, dry):
    rid, media = req["id"], req["media"]
    tmdb = str(media["tmdbId"])
    title = seerr.movie(tmdb).get("title") or f"TMDB {tmdb}"
    set_request(rid, media_type="movie", tmdb_id=tmdb, title=title)
    if media.get("status") == 5:
        return ST_DONE, "Ya está disponible en Jellyfin", None
    versions = xf.by_tmdb("vod", tmdb)
    if not versions:
        return ST_WAITING, "No existe en el catálogo del proveedor", None
    ids = {str(v["id"]) for v in versions}
    jf = jf_index(s)
    if jf is not None and tmdb in jf["movies"]:
        return ST_DONE, "Ya está en Jellyfin", None
    done = [it for it in xf.cart() if it.get("content_type") == "vod" and str(it.get("stream_id")) in ids
            and it.get("status") == "completed"]
    if done:
        return ST_DONE, f"Ya descargada, esperando a que Jellyfin la detecte ({done[0].get('name')})", None
    failed = [it for it in xf.cart() if it.get("content_type") == "vod" and str(it.get("stream_id")) in ids
              and it.get("status") not in ("queued", "downloading", "completed")]
    if failed:
        return ST_QUEUED, f"Ya está en la cola con error ({failed[0].get('name')}); reinténtala en XtreamFilter", None
    already = [it for it in xf.cart() if it.get("content_type") == "vod" and str(it.get("stream_id")) in ids
               and it.get("status") in ("queued", "downloading")]
    if already:
        it = already[0]
        moved = s["prioritize"] and it.get("status") == "queued"
        if dry:
            return ST_DRY, f"Ya está en la cola: {it.get('name')}" + (" (la subiría al principio)" if moved else ""), it.get("name")
        remember_added("vod", [it], rid, it.get("name"), adopted=True)
        prioritize(xf, s)
        if media.get("status", 0) < 3:
            seerr.mark_processing(media["id"])
        log(f"Película que ya estaba en la cola, ahora de Seerr: {it.get('name')} (petición {rid})")
        return ST_QUEUED, f"Ya estaba en la cola: {it.get('name')}" + (" (subida al principio)" if moved else " (descargándose)"), it.get("name")
    choice, why, note = choose(versions, s, xf, "vod", title=title)
    if not choice:
        found = ", ".join(sorted({v["name"].split(" - ")[0] for v in versions}))
        return ST_WAITING, (f"{note or f'Sin versión en el idioma preferido (hay: {found})'}. "
                            f"Se volverá a buscar en {s['retry_hours']} h"), None
    label = f"{choice['name']} | {choice.get('group')}" + (f" ({why})" if why else "")
    intro = f"{note}. " if note else ""
    if dry:
        return ST_DRY, f"{intro}Añadiría a la cola{' (al principio)' if s['prioritize'] else ''}: {label}", label
    st, p = xf.post("/api/cart", {
        "content_type": "vod", "source_id": choice["source_id"], "stream_id": str(choice["id"]),
        "name": choice["name"], "icon": choice.get("icon", ""), "group": choice.get("group", ""),
        "container_extension": choice.get("container_extension") or "mp4",
    })
    if st not in (200, 409):
        raise RuntimeError(f"XtreamFilter no aceptó la película ({st}): {p}")
    remember_added("vod", (p or {}).get("items", []) if st == 200 else [], rid, choice["name"])
    prioritize(xf, s)
    if media.get("status", 0) < 3:
        seerr.mark_processing(media["id"])
    log(f"Película añadida a la cola: {label} (petición {rid})")
    return ST_QUEUED, f"{intro}Añadida a la cola: {label}", label


def ensure_monitor(choice, tmdb, xf, dry, folder=None):
    for m in xf.monitors():
        refs = {str(m.get("series_id"))} | {str(x.get("series_ref")) for x in m.get("monitor_sources") or []}
        if str(m.get("tmdb_id") or "") == str(tmdb) or str(choice["id"]) in refs:
            return "ya estaba en seguimiento", None
    if dry:
        return "se añadiría al seguimiento de XtreamFilter", None
    st, p = xf.post("/api/monitor", {
        "series_name": choice["name"], "series_id": str(choice["id"]), "source_id": choice["source_id"],
        "source_name": choice.get("source_name"), "source_category": choice.get("group"),
        "cover": choice.get("icon", ""), "tmdb_id": str(tmdb), "canonical_name": folder or choice["name"],
        "scope": "new_only", "action": "download", "backfill": "none",
    })
    if st != 200:
        raise RuntimeError(f"XtreamFilter no aceptó el seguimiento ({st}): {p}")
    log(f"Serie añadida al seguimiento de XtreamFilter: {choice['name']}")
    return "añadida al seguimiento de XtreamFilter", ((p or {}).get("entry") or {}).get("id")


def first_year(info):
    try:
        return int(str(info.get("firstAirDate") or "")[:4])
    except ValueError:
        return None


def process_tv(req, s, seerr, xf, dry):
    rid, media = req["id"], req["media"]
    tmdb = str(media["tmdbId"])
    info = seerr.tv(tmdb)
    title = info.get("name") or f"TMDB {tmdb}"
    wanted = sorted({int(x["seasonNumber"]) for x in req.get("seasons", [])} - {0})
    set_request(rid, media_type="tv", tmdb_id=tmdb, title=title, seasons=",".join(map(str, wanted)))
    available = {int(x["seasonNumber"]) for x in (info.get("mediaInfo") or {}).get("seasons", []) if x.get("status") == 5}
    wanted_left = [n for n in wanted if n not in available]
    ended = info.get("status") in ENDED_STATUSES
    if not wanted_left and ended:
        return ST_DONE, "Todas las temporadas pedidas ya están en Jellyfin", None
    versions = xf.by_tmdb("series", tmdb)
    if not versions:
        return ST_WAITING, "No existe en el catálogo del proveedor", None
    choice, why, note = choose_series(versions, s, xf, tmdb, wanted, title, first_year(info))
    if not choice:
        found = ", ".join(sorted({v["name"].split(" - ")[0] for v in versions}))
        return ST_WAITING, (f"{note or f'Sin versión en el idioma preferido (hay: {found})'}. "
                            f"Se volverá a buscar en {s['retry_hours']} h"), None
    label = f"{choice['name']} | {choice.get('group')}" + (f" ({why})" if why else "")

    if (str(choice["source_id"]), str(choice["id"])) in xf._eps:
        all_eps = xf.series_episodes(choice["source_id"], choice["id"])  # ya consultada al elegir
    else:
        with free_connection(xf):
            all_eps = xf.series_episodes(choice["source_id"], choice["id"])
    eps = [e for e in all_eps if e["season"] in wanted_left]
    version_ids = {str(v["id"]) for v in versions}
    # Lo que ya tienes: en Jellyfin (cualquier carpeta de esta serie) o completado en la
    # cola (Jellyfin aún no lo ha visto). Lo borrado se vuelve a descargar (se ha pedido).
    jf = jf_index(s)
    have = set(jf["series"].get(tmdb, set())) if jf is not None else set()
    # Si la serie ya está en el disco, lo nuevo va a SU carpeta (aunque sea de otra versión)
    folder = (jf or {}).get("folders", {}).get(tmdb) or choice["name"]
    if norm(folder) != norm(choice["name"]):
        label += f" → carpeta «{folder}»"
    have |= {(int(i.get("season") or 0), int(i.get("episode_num") or 0)) for i in xf.cart()
             if i.get("content_type") == "series" and str(i.get("series_id")) in version_ids
             and i.get("status") == "completed"}
    # En la cola en cualquier estado salvo completado (incluido con error: se reintenta en XtreamFilter)
    cart_eps = [i for i in xf.cart() if i.get("content_type") == "series" and str(i.get("series_id")) in version_ids
                and i.get("status") != "completed"]
    in_cart = {(int(i.get("season") or 0), int(i.get("episode_num") or 0)) for i in cart_eps}
    adopt = [i for i in cart_eps if int(i.get("season") or 0) in wanted_left
             and i.get("status") in ("queued", "downloading")]
    todo = [e for e in eps if (e["season"], e["episode"]) not in have | in_cart]
    missing_seasons = [n for n in wanted_left if n not in {e["season"] for e in eps}]

    parts = []
    if todo:
        seasons_txt = ", ".join(f"T{n}" for n in sorted({e['season'] for e in todo}))
        if dry:
            parts.append(f"Añadiría {len(todo)} episodios ({seasons_txt}) de {label}"
                         + (" al principio de la cola" if s["prioritize"] else ""))
        else:
            st, p = xf.post("/api/cart", {
                "content_type": "series", "add_mode": "episodes", "source_id": choice["source_id"],
                "series_id": str(choice["id"]), "series_name": folder,
                "icon": choice.get("icon", ""), "group": choice.get("group", ""),
                "episode_ids": [e["id"] for e in todo],
            })
            if st != 200:
                raise RuntimeError(f"XtreamFilter no aceptó los episodios ({st}): {p}")
            remember_added("series", p.get("items", []), rid, choice["name"])
            parts.append(f"Añadidos {p.get('added', len(todo))} episodios ({seasons_txt}) de {label}")
            log(f"Serie añadida a la cola: {label}, {len(todo)} episodios (petición {rid})")
            if media.get("status", 0) < 3:
                seerr.mark_processing(media["id"])
    if adopt:
        if dry:
            parts.append(f"{len(adopt)} episodios pedidos ya están en la cola"
                         + (" (los subiría al principio)" if s["prioritize"] else ""))
        else:
            remember_added("series", adopt, rid, choice["name"], adopted=True)
            parts.append(f"{len(adopt)} episodios pedidos ya estaban en la cola"
                         + (" (subidos al principio)" if s["prioritize"] else ""))
    if not dry and (todo or adopt):
        prioritize(xf, s)  # una sola vez: episodios en el orden en que están en la cola
    if not todo and not adopt and eps:
        parts.append("Los episodios pedidos ya están descargados")
    if missing_seasons:
        parts.append("El proveedor aún no tiene: " + ", ".join(f"T{n}" for n in missing_seasons))
    if not ended and s["monitor_returning"]:
        txt, monitor_id = ensure_monitor(choice, tmdb, xf, dry, folder)
        parts.append("En emisión: " + txt)
        if monitor_id:
            set_request(rid, monitor_id=monitor_id)
    elif ended:
        parts.append("Serie terminada")

    if note:
        parts.insert(0, note)
    detail = ". ".join(parts)
    if dry:
        return ST_DRY, detail, label
    if missing_seasons and not (not ended and s["monitor_returning"]):
        return ST_WAITING, detail, label
    return (ST_QUEUED if todo or in_cart else ST_DONE), detail, label


def availability(req, s, seerr, xf):
    """Comprueba si una petición se puede atender. Devuelve (título, motivo o None)."""
    tmdb = str(req["media"]["tmdbId"])
    if req["type"] == "movie":
        title = seerr.movie(tmdb).get("title") or f"TMDB {tmdb}"
        versions = xf.by_tmdb("vod", tmdb)
        seasons = None
    else:
        info = seerr.tv(tmdb)
        title = info.get("name") or f"TMDB {tmdb}"
        versions = xf.by_tmdb("series", tmdb)
        seasons = sorted({int(x["seasonNumber"]) for x in req.get("seasons", [])} - {0})
    if not versions:
        return title, "not_found", "no existe en el catálogo del proveedor"
    if req["type"] == "movie":
        choice, _, note = choose(versions, s, xf, "vod", seasons, title)
    else:
        choice, _, note = choose_series(versions, s, xf, tmdb, seasons, title, first_year(info))
    if not choice:
        return title, "no_language", note or "no hay versión en el idioma preferido"
    return title, None, ""


def render(template, req, title):
    user = (req.get("requestedBy") or {}).get("displayName") or ""
    values = {"titulo": title, "usuario": user, "tipo": "película" if req["type"] == "movie" else "serie"}
    return re.sub(r"\{(titulo|usuario|tipo)\}", lambda m: values[m.group(1)], template)


def queue_notice(req, s, title, text):
    user = req.get("requestedBy") or {}
    if not (s["notify_jellyfin"] and user.get("jellyfinUserId")):
        return
    with db() as c:
        c.execute("INSERT INTO notices (request_id, jf_user, user_name, header, text, created) VALUES (?, ?, ?, ?, ?, ?)",
                  (req["id"], user["jellyfinUserId"].replace("-", "").lower(), user.get("displayName"),
                   render(s["msg_title"], req, title), text, time.time()))


def process_pending(req, s, seerr, xf, dry):
    """Petición pendiente: aprobarla si está disponible o rechazarla si no."""
    rid = req["id"]
    title, reason, note = availability(req, s, seerr, xf)
    set_request(rid, media_type="movie" if req["type"] == "movie" else "tv",
                tmdb_id=str(req["media"]["tmdbId"]), title=title)
    if reason is None:
        if dry:
            return None, f"Aprobaría la petición en Seerr"
        seerr.set_status(rid, "approve")
        log(f"Petición {rid} aprobada: «{title}» está disponible en el proveedor")
        req = dict(req, status=2)
        return req, None
    motive = note
    if not s["decline_unavailable"]:
        return None, f"Pendiente: {motive}"
    text = render(s["msg_not_found"] if reason == "not_found" else s["msg_no_language"], req, title)
    if dry:
        return None, f"Rechazaría ({motive}) y avisaría: {text}"
    seerr.set_status(rid, "decline")
    queue_notice(req, s, title, text)
    log(f"Petición {rid} rechazada ({motive}): «{title}»")
    return None, f"Rechazada: {motive}. Aviso al cliente: {text}"


def deliver_notices():
    """Muestra en Jellyfin los avisos pendientes a los usuarios conectados."""
    s = load_settings()
    if not s["jellyfin_api_key"]:
        return
    with db() as c:
        c.execute("DELETE FROM notices WHERE delivered IS NULL AND created < ?", (time.time() - s["notify_days"] * 86400,))
        pending = [dict(r) for r in c.execute("SELECT * FROM notices WHERE delivered IS NULL")]
    if not pending:
        return
    base, h = s["jellyfin_url"].rstrip("/"), jf_auth(s)
    st, sessions = http("GET", base + "/Sessions?activeWithinSeconds=900", h, timeout=20)
    if st != 200:
        return
    capable = {}
    for se in sessions or []:
        if "DisplayMessage" in ((se.get("Capabilities") or {}).get("SupportedCommands") or []):
            capable.setdefault((se.get("UserId") or "").replace("-", "").lower(), []).append(se["Id"])
    for n in pending:
        sent = False
        for sid in capable.get(n["jf_user"], []):
            st, _ = http("POST", f"{base}/Sessions/{sid}/Message", h,
                         {"Header": n["header"], "Text": n["text"], "TimeoutMs": 60000}, timeout=20)
            sent = sent or st in (200, 204)
        if sent:
            with db() as c:
                c.execute("UPDATE notices SET delivered = ? WHERE id = ?", (time.time(), n["id"]))
            log(f"Aviso mostrado en Jellyfin a {n['user_name']}: {n['text']}")


def notifier():
    while True:
        try:
            deliver_notices()
        except Exception as e:  # noqa: BLE001
            print(f"[error] avisos: {e}", flush=True)
        try:
            track_downloads(load_settings())
        except Exception as e:  # noqa: BLE001
            log(f"Error siguiendo las descargas: {e}", "error")
        try:
            shrinkerr_step()
        except Exception as e:  # noqa: BLE001
            log(f"Error con shrinkerr: {e}", "error")
        time.sleep(60)


class Shrinkerr:
    def __init__(self, s):
        self.base = s["shrinkerr_url"].rstrip("/")
        self.h = {"X-Api-Key": s["shrinkerr_api_key"]} if s.get("shrinkerr_api_key") else {}

    def call(self, method, path, body=None):
        st, p = http(method, self.base + path, self.h, body, timeout=120)
        if st != 200:
            raise RuntimeError(f"Shrinkerr {method} {path} -> {st}: {p}")
        return p

    def add(self, paths):
        return self.call("POST", "/api/jobs/add-by-path", {"file_paths": paths, "priority": 1})

    def jobs(self, status):
        return self.call("GET", f"/api/jobs/?status={status}")

    def to_top(self, job_ids):
        self.call("POST", "/api/jobs/bulk-update-settings", {"job_ids": job_ids, "priority": 2})
        self.call("POST", "/api/jobs/bulk-move", {"job_ids": job_ids, "position": "top"})


def map_path(path, s):
    """Traduce la ruta de XtreamFilter a la de shrinkerr según la tabla de rutas."""
    for line in s["shrinkerr_paths"].splitlines():
        if "=" not in line:
            continue
        src, dst = (x.strip().rstrip("/") for x in line.split("=", 1))
        if src and (path == src or path.startswith(src + "/")):
            return dst + path[len(src):]
    return None


def retry_failed(s, xf):
    """En cada revisión: reintenta cualquier descarga con error, cancelada o con fallo al
    mover en la cola de XtreamFilter (de Seerr o no), hasta retry_max veces por elemento."""
    if not s["auto_retry"] or s["dry_run"]:
        return
    xf.fresh()
    cart = xf.cart()
    in_cart = {i.get("id") for i in cart}
    with db() as c:
        counts = {r["cart_id"]: dict(r) for r in c.execute("SELECT * FROM retries")}
        gone = [k for k in counts if k not in in_cart]
        c.executemany("DELETE FROM retries WHERE cart_id = ?", [(k,) for k in gone])
        ours = {r[0]: r[1] for r in c.execute("SELECT cart_id, request_id FROM added WHERE cart_id IS NOT NULL")}
    limit, retried = max(1, int(s["retry_max"])), 0
    for item in cart:
        if item.get("status") not in RETRYABLE:
            continue
        cid, name = item.get("id"), item.get("name") or "?"
        rec = counts.get(cid) or {"count": 0, "exhausted": 0}
        if rec["count"] >= limit:
            if not rec["exhausted"]:  # avisar una sola vez
                with db() as c:
                    c.execute("UPDATE retries SET exhausted = 1, updated = ? WHERE cart_id = ?", (time.time(), cid))
                note = f"{name} falló {limit} veces; revísalo en XtreamFilter"
                log(f"Reintentos agotados: {note} ({item.get('error') or 'sin detalle'})", "error")
                if cid in ours:
                    req = get_request(ours[cid]) or {}
                    set_request(ours[cid], detail=((req.get("detail") + ". ") if req.get("detail") else "") + note)
            continue
        if xf.retry(cid):
            retried += 1
            with db() as c:
                c.execute("INSERT INTO retries (cart_id, name, count, updated) VALUES (?, ?, 1, ?) "
                          "ON CONFLICT(cart_id) DO UPDATE SET count = count + 1, updated = excluded.updated",
                          (cid, name, time.time()))
            log(f"Reintento {rec['count'] + 1}/{limit}: {name} ({(item.get('error') or 'error')[:120]})")
    if retried:
        prioritize(xf, s)


def track_downloads(s):
    """Sigue en la cola de XtreamFilter lo añadido por el puente.

    completado -> descargado (se guarda la ruta). Si ya no está en la cola (XtreamFilter
    la vacía al terminarla), se comprueba en Jellyfin. Al completarse algo se pide a
    Jellyfin que actualice la biblioteca.
    """
    with db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT a.*, r.tmdb_id FROM added a LEFT JOIN requests r ON r.id = a.request_id "
            "WHERE a.dl IS NULL AND a.cart_id IS NOT NULL")]
    if not rows:
        return
    xf = XF(s)
    by_id = {i.get("id"): i for i in xf.cart()}
    jf, updates, newly = None, [], 0
    for r in rows:
        item = by_id.get(r["cart_id"])
        if item is not None:
            if r.get("season") is None and item.get("season") is not None:
                with db() as c:  # completar temporada/episodio de filas antiguas
                    c.execute("UPDATE added SET season = ?, episode = ? WHERE content_type = ? AND stream_id = ?",
                              (item.get("season"), item.get("episode_num"), r["content_type"], r["stream_id"]))
            if item.get("status") == "completed":
                updates.append(("completado", item.get("file_path"), r))
                newly += 1
            continue  # en espera / descargando / error: seguir esperando
        # Ya no está en la cola: ¿está en Jellyfin?
        if jf is None:
            jf = jf_snapshot(s) or {"series": {}, "movies": set()}
        tmdb = str(r.get("tmdb_id") or "")
        if r["content_type"] == "vod":
            present = tmdb in jf["movies"]
        else:
            try:
                present = (int(r.get("season") or 0), int(r.get("episode") or 0)) in jf["series"].get(tmdb, set())
            except ValueError:
                present = False
        updates.append(("completado" if present else "no está", None, r))
        if present:
            newly += 1
    with db() as c:
        for dl, path, r in updates:
            c.execute("UPDATE added SET dl = ?, file_path = ? WHERE content_type = ? AND stream_id = ?",
                      (dl, path, r["content_type"], r["stream_id"]))
            if dl == "completado" and not path:
                # Terminó sin que el puente viera la ruta: no se puede enviar a shrinkerr arriba
                c.execute("UPDATE added SET shrink = COALESCE(shrink, 'fuera de cola') "
                          "WHERE content_type = ? AND stream_id = ?", (r["content_type"], r["stream_id"]))
    if newly and not s["dry_run"] and s["jellyfin_refresh"] and s["jellyfin_api_key"]:
        st, p = http("POST", s["jellyfin_url"].rstrip("/") + "/Library/Refresh",
                     jf_auth(s))
        if st in (200, 204):
            log(f"Jellyfin: biblioteca actualizada ({newly} descargas terminadas)")
        else:
            log(f"Jellyfin no aceptó la actualización ({st}): {p}", "error")


def shrinkerr_step():
    """Envía a shrinkerr lo pedido en Seerr que ya terminó de descargarse y lo sube arriba."""
    s = load_settings()
    if not s["shrinkerr_enabled"] or s["dry_run"]:
        return
    with db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM added WHERE shrink IS NULL AND dl = 'completado' AND file_path IS NOT NULL ORDER BY rowid")]
    sh = Shrinkerr(s)
    new = []
    for r in rows:  # en el orden en que el puente vio terminar las descargas
        target = map_path(r["file_path"], s)
        if not target:
            with db() as c:
                c.execute("UPDATE added SET shrink = ? WHERE content_type = ? AND stream_id = ?",
                          ("sin ruta", r["content_type"], r["stream_id"]))
            log(f"Shrinkerr: no sé traducir la ruta {r['file_path']}", "error")
            continue
        new.append((None, target, r))
    if new:
        result = sh.add([t for _, t, _ in new])
        with db() as c:
            c.executemany("UPDATE added SET shrink = 'enviado', shrink_path = ? WHERE content_type = ? AND stream_id = ?",
                          [(t, r["content_type"], r["stream_id"]) for _, t, r in new])
        log(f"Shrinkerr: {len(new)} archivos enviados ({result.get('added', 0)} nuevos en su cola"
            + (f", avisos: {'; '.join(result.get('errors') or [])}" if result.get("errors") else "") + ")")

    with db() as c:
        active = [dict(r) for r in c.execute(
            f"SELECT * FROM added WHERE shrink IN ({','.join('?' * len(SH_ACTIVE))})", SH_ACTIVE)]
    if not active:
        return
    pending = sh.jobs("pending")
    running = {j["file_path"] for j in sh.jobs("running")}
    pending_paths = {j["file_path"] for j in pending}

    # Estado de cada archivo en shrinkerr
    converted = 0
    for r in active:
        path = r["shrink_path"]
        if path in running:
            new = "convirtiendo"
        elif path in pending_paths:
            new = "en cola"
        else:
            name = os.path.basename(path)
            if any(j["file_path"] == path for j in sh.jobs(f"completed&search={urllib.parse.quote(name)}")):
                new = "convertido"
            elif any(j["file_path"] == path for j in sh.jobs(f"failed&search={urllib.parse.quote(name)}")):
                new = "fallido"
            else:
                new = "sin conversión"  # shrinkerr no vio trabajo que hacer (p. ej. ya en HEVC)
        if new != r["shrink"]:
            with db() as c:
                c.execute("UPDATE added SET shrink = ? WHERE content_type = ? AND stream_id = ?",
                          (new, r["content_type"], r["stream_id"]))
            if new == "convertido":
                converted += 1
            if new in ("convertido", "fallido", "sin conversión"):
                log(f"Shrinkerr: {r['name']} → {new}")
    if converted and s["jellyfin_refresh"] and s["jellyfin_api_key"]:
        st, _ = http("POST", s["jellyfin_url"].rstrip("/") + "/Library/Refresh",
                     jf_auth(s))
        if st in (200, 204):
            log(f"Jellyfin: biblioteca actualizada ({converted} conversiones terminadas)")
    update_converting_states()

    # Subir arriba todo lo nuestro que siga pendiente en shrinkerr, en su orden actual
    ours = {r["shrink_path"] for r in active}
    mine = [j for j in pending if j["file_path"] in ours]
    if not mine:
        return
    mine.sort(key=lambda j: j.get("queue_order") or 0)
    ids = [j["id"] for j in mine]
    if [j["id"] for j in pending[:len(ids)]] != ids:
        sh.to_top(ids)
        log(f"Shrinkerr: {len(ids)} archivos pedidos en Seerr subidos al principio de la cola")


def conversion_pending(rid):
    with db() as c:
        return c.execute(
            f"SELECT COUNT(*) FROM added WHERE request_id = ? AND shrink IN ({','.join('?' * len(SH_ACTIVE))})",
            (rid, *SH_ACTIVE)).fetchone()[0] > 0


def update_converting_states():
    """Completada <-> Convirtiendo según lo que falte por convertir en shrinkerr."""
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT id, state FROM requests WHERE state IN (?, ?)",
                                           (ST_DONE, ST_CONVERTING))]
    for r in rows:
        busy = conversion_pending(r["id"])
        if busy and r["state"] == ST_DONE:
            set_request(r["id"], state=ST_CONVERTING)
        elif not busy and r["state"] == ST_CONVERTING:
            set_request(r["id"], state=ST_DONE)


def cancel_request(rid):
    """Cancela una petición: Seerr, cola de XtreamFilter y lista del puente."""
    s = load_settings()
    seerr, xf = Seerr(s), XF(s)
    done = []
    full = seerr.request(rid)
    if full is not None:
        if full.get("status") == 1:
            seerr.set_status(rid, "decline")
            done.append("rechazada en Seerr")
        elif seerr.delete_request(rid):
            done.append("borrada en Seerr")
    with db() as c:
        ours = [dict(r) for r in c.execute(
            "SELECT cart_id FROM added WHERE request_id = ? AND cart_id IS NOT NULL AND COALESCE(adopted, 0) = 0", (rid,))]
        row = c.execute("SELECT title, monitor_id FROM requests WHERE id = ?", (rid,)).fetchone()
    ids = {r["cart_id"] for r in ours}
    removed = 0
    if ids:
        for it in xf.cart():
            if it.get("id") in ids and it.get("status") == "queued" and xf.remove_cart_item(it["id"]):
                removed += 1
    if removed:
        done.append(f"{removed} elementos quitados de la cola")
    if row and row["monitor_id"] and xf.remove_monitor(row["monitor_id"]):
        done.append("quitada del seguimiento de XtreamFilter")
    with db() as c:
        c.execute("DELETE FROM requests WHERE id = ?", (rid,))
        c.execute("DELETE FROM added WHERE request_id = ?", (rid,))
        c.execute("DELETE FROM notices WHERE request_id = ? AND delivered IS NULL", (rid,))
    title = row["title"] if row else f"petición {rid}"
    log(f"Cancelada «{title}»: " + (", ".join(done) or "solo borrada de la lista"))
    return done


def meta_get(key, default=None):
    with db() as c:
        row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(key, value):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))


def norm(name):
    """Normaliza un nombre de carpeta/serie como lo hace XtreamFilter al crear carpetas."""
    name = unicodedata.normalize("NFKD", name or "")
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return unicodedata.normalize("NFC", name).lower()


def swap_year_country(name):
    return re.sub(r"(\([A-Za-z]{2}\)) (\(\d{4}\))$", r"\2 \1", name)


def episodes_text(eps):
    """[(1,1),(1,2),(1,3),(2,5)] -> 'T1: E01–E03 · T2: E05'"""
    out = []
    for season in sorted({se for se, _ in eps}):
        nums = sorted(ep for se, ep in eps if se == season)
        parts, start, prev = [], nums[0], nums[0]
        for n in nums[1:] + [None]:
            if n is not None and n == prev + 1:
                prev = n
                continue
            parts.append(f"E{start:02d}" if start == prev else f"E{start:02d}–E{prev:02d}")
            if n is not None:
                start = prev = n
        out.append(f"T{season}: " + ", ".join(parts))
    return " · ".join(out)


def jellyfin_series(s):
    """Series de Jellyfin: {carpeta: {"tmdb": id, "have": {(temporada, episodio)}}}."""
    base, h = s["jellyfin_url"].rstrip("/"), jf_auth(s)
    st, p = http("GET", base + "/Items?IncludeItemTypes=Series&Recursive=true&Fields=Path,ProviderIds", h, timeout=120)
    if st != 200:
        raise RuntimeError(f"Jellyfin respondió {st} al pedir las series")
    series = {}
    by_id = {}
    for it in p.get("Items", []):
        folder = os.path.basename((it.get("Path") or "").rstrip("/"))
        if not folder:
            continue
        series[folder] = {"tmdb": (it.get("ProviderIds") or {}).get("Tmdb"), "have": set(), "name": it.get("Name")}
        by_id[it["Id"]] = folder
    st, p = http("GET", base + "/Items?IncludeItemTypes=Episode&Recursive=true&Fields=Path"
                 "&EnableImages=false&EnableUserData=false", h, timeout=300)
    if st != 200:
        raise RuntimeError(f"Jellyfin respondió {st} al pedir los episodios")
    for e in p.get("Items", []):
        folder = by_id.get(e.get("SeriesId"))
        if folder and e.get("ParentIndexNumber") is not None and e.get("IndexNumber") is not None:
            series[folder]["have"].add((int(e["ParentIndexNumber"]), int(e["IndexNumber"])))
    return series


def base_title(name):
    """'ES - After Life (GB) (2019)' -> 'es - after life' (sin año ni país)."""
    return re.sub(r"\s*\([^)]*\)", "", norm(name)).strip()


def find_version(folder, tmdb, xf):
    """Busca en el catálogo la misma serie y versión que la carpeta.

    Solo acepta un nombre igual al de la carpeta, o igual salvo el año/país entre
    paréntesis. Nunca adivina: si Jellyfin tiene mal identificada la serie, no se
    elige otra distinta.
    """
    exact = {norm(folder), norm(swap_year_country(folder))}
    base = base_title(folder)
    candidates = xf.by_tmdb("series", tmdb) if tmdb else []
    candidates += [v for v in xf.catalog("series") if norm(v["name"]) in exact or base_title(v["name"]) == base]
    for v in candidates:
        if norm(v.get("name")) in exact:
            return v
    same = [v for v in candidates if base_title(v.get("name")) == base_title(folder) and not is_4k(v)]
    if same:
        years = re.findall(r"\((\d{4})\)", folder)
        same.sort(key=lambda v: (0 if not years or f"({years[0]})" in (v.get("name") or "") else 1,
                                 -(v.get("added") or 0)))
        return same[0]
    return None


def add_missing(row, xf):
    eps = json.loads(row["episodes"])
    st, p = xf.post("/api/cart", {
        "content_type": "series", "add_mode": "episodes", "source_id": row["source_id"],
        "series_id": row["series_id"], "series_name": row["folder"],
        "icon": row.get("icon") or "", "group": row.get("grp") or "",
        "episode_ids": [e["id"] for e in eps],
    })
    if st != 200:
        raise RuntimeError(f"XtreamFilter no aceptó los episodios ({st}): {p}")
    with db() as c:
        c.execute("UPDATE missing SET state = 'añadido', found = ? WHERE folder = ?", (time.time(), row["folder"]))
    log(f"Episodios que faltan añadidos a la cola: {row['folder']} ({row['count']}: {row['detail']})")
    return p.get("added", len(eps))


def missing_scan(s):
    """Busca episodios que faltan en las series de Jellyfin (disco) y opcionalmente los añade.

    Solo consulta al proveedor (una serie cada MISSING_DELAY s) las series que Seerr marca
    como parcialmente disponibles; las completas según Seerr no se consultan. Las series sin
    TMDB no se procesan (aparecen como "FALTA TMDB"). Mientras dura, la cola de XtreamFilter
    se pausa para que no arranque descargas (no se cancela nada) y se reanuda al terminar."""
    if not s["jellyfin_api_key"]:
        raise RuntimeError("Falta la clave de API de Jellyfin")
    if not s["seerr_api_key"]:
        raise RuntimeError("Falta la clave de API de Seerr")
    xf = XF(s)
    # Pausar la cola mientras dura la búsqueda: XtreamFilter no arranca descargas nuevas
    # (una descarga a medias queda en pausa, no se cancela). Solo se reanuda si la pausó el puente.
    mine, was_downloading = False, False
    st = xf.status() or {}
    if not st.get("queue_paused") and (st.get("queued") or st.get("downloading")) and xf.pause():
        mine, was_downloading = True, bool(st.get("downloading") or st.get("current"))
        log("Episodios que faltan: cola de XtreamFilter en pausa durante la búsqueda"
            + (" (había una descarga en curso)" if was_downloading else ""))
        time.sleep(MISSING_SETTLE)  # que la conexión quede libre
    try:
        _missing_scan(s, xf)
    finally:
        if mine:
            time.sleep(MISSING_SETTLE)  # que el proveedor libere la conexión de la búsqueda
            if was_downloading:
                try:
                    retry_failed(s, xf)  # según "Reintentar las descargas con error" y "Reintentos por descarga"
                except Exception as e:  # noqa: BLE001
                    log(f"Error al reintentar descargas tras la búsqueda: {e}", "error")
            xf.resume()
            log("Episodios que faltan: cola de XtreamFilter reanudada")


def update_queued_rows(series):
    """Antes de consultar al proveedor (no depende de él):
    1) se borran de la lista las filas "Completada" (las marcó la búsqueda anterior);
    2) las filas "En cola" cuyos episodios ya están todos en Jellyfin pasan a "Completada"."""
    have_by_tmdb = {}
    for info in series.values():
        if info["tmdb"]:
            have_by_tmdb.setdefault(str(info["tmdb"]), set()).update(info["have"])
    with db() as c:
        removed = [r[0] for r in c.execute("SELECT folder FROM missing WHERE state = 'completada'")]
        c.execute("DELETE FROM missing WHERE state = 'completada'")
        done = []
        for r in c.execute("SELECT folder, episodes FROM missing WHERE state = 'añadido'").fetchall():
            info = series.get(r[0]) or {}
            have = have_by_tmdb.get(str(info.get("tmdb")), set()) | info.get("have", set())
            eps = {(e["season"], e["episode"]) for e in json.loads(r[1] or "[]")}
            if eps and eps <= have:
                done.append(r[0])
        c.executemany("UPDATE missing SET state = 'completada' WHERE folder = ?", [(f,) for f in done])
    if removed:
        log("Episodios que faltan: quitadas de la lista (completadas en la búsqueda anterior): " + ", ".join(removed))
    if done:
        log("Episodios que faltan: completadas (todos sus episodios ya están en Jellyfin): " + ", ".join(done))


def _missing_scan(s, xf):
    started = time.time()
    series = jellyfin_series(s)
    if not series:
        raise RuntimeError("Jellyfin no devolvió ninguna serie; búsqueda cancelada sin tocar las listas")
    # Series que ya no existen (no están en Jellyfin = borradas del disco): fuera de las listas
    with db() as c:
        gone_ign = [r[0] for r in c.execute("SELECT folder FROM ignored") if r[0] not in series]
        gone_mis = [r[0] for r in c.execute("SELECT folder FROM missing") if r[0] not in series]
        c.executemany("DELETE FROM ignored WHERE folder = ?", [(f,) for f in gone_ign])
        c.executemany("DELETE FROM missing WHERE folder = ?", [(f,) for f in gone_mis])
        ignored = {r[0] for r in c.execute("SELECT folder FROM ignored")}
    if gone_ign:
        log("Series ignoradas que ya no existen, quitadas de la lista: " + ", ".join(gone_ign))
    update_queued_rows(series)
    # Series parcialmente disponibles según Seerr (compara Jellyfin con TMDB)
    seerr = Seerr(s)
    partial = {str(m.get("tmdbId")) for m in seerr.get("/media?filter=partial&take=5000").get("results", [])
               if m.get("mediaType") == "tv"}
    # Todo lo que esté en la cola de XtreamFilter (en espera, descargando, con error, o
    # completado que Jellyfin aún no ha visto)
    cart = [i for i in xf.cart() if i.get("content_type") == "series"]
    found, total, not_found, no_tmdb = {}, 0, [], []
    complete, queried, errors_in_row = 0, 0, 0
    for folder, info in sorted(series.items()):
        if folder in ignored or not info["have"]:
            continue  # ignorada o carpeta vacía
        tmdb = str(info["tmdb"] or "")
        if not tmdb:
            no_tmdb.append(folder)
            continue
        if tmdb not in partial:
            complete += 1
            continue  # completa según Seerr: no se consulta al proveedor
        try:
            version = find_version(folder, tmdb, xf)
            if not version:
                not_found.append(folder)
                continue
            # Solo la cola de esta carpeta y esta versión (no de otros idiomas)
            ids = {str(version["id"])}
            names = {norm(folder), norm(swap_year_country(folder)), norm(version.get("name"))}
            queued = {(int(i.get("season") or 0), int(i.get("episode_num") or 0)) for i in cart
                      if str(i.get("series_id")) in ids or norm(i.get("series_name")) in names}
            if queried:
                time.sleep(MISSING_DELAY)  # despacio, para no provocar un bloqueo del proveedor
            queried += 1
            eps = [e for e in xf.series_episodes(version["source_id"], version["id"])
                   if e["season"] > 0 and (e["season"], e["episode"]) not in info["have"] | queued]
        except Exception as e:  # noqa: BLE001
            log(f"Episodios que faltan: error con {folder}: {e}", "error")
            errors_in_row += 1
            if errors_in_row >= MISSING_MAX_ERRORS:
                break
            continue
        errors_in_row = 0
        if eps:
            eps.sort(key=lambda e: (e["season"], e["episode"]))
            found[folder] = (version, eps)
            total += len(eps)
    if errors_in_row >= MISSING_MAX_ERRORS:
        again = time.time() + MISSING_RETRY
        meta_set("missing_retry_at", again)  # se vuelve a intentar a los 30 min, dentro de la franja horaria
        when = time.strftime("%I:%M %p", time.localtime(again)).lstrip("0")
        meta_set("missing_note", f"Búsqueda cancelada: el proveedor falló {MISSING_MAX_ERRORS} veces seguidas. "
                                 f"Se volverá a intentar a las {when} (dentro de la franja horaria)")
        log(f"Episodios que faltan: búsqueda cancelada (el proveedor falló {MISSING_MAX_ERRORS} veces seguidas); "
            f"las listas no se han tocado. Se volverá a intentar a las {when} (dentro de la franja horaria)", "error")
        return

    with db() as c:
        old = {r["folder"]: dict(r) for r in c.execute("SELECT * FROM missing")}
        c.execute("DELETE FROM missing WHERE state IN ('pendiente', 'falta_tmdb')")
        for folder, (v, eps) in found.items():
            c.execute("INSERT OR REPLACE INTO missing (folder, series_name, series_id, source_id, icon, grp, episodes,"
                      " count, state, detail, found) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?, ?)",
                      (folder, v["name"], str(v["id"]), v["source_id"], v.get("icon"), v.get("group"),
                       json.dumps([{"id": e["id"], "season": e["season"], "episode": e["episode"]} for e in eps]),
                       len(eps), episodes_text([(e["season"], e["episode"]) for e in eps]),
                       (old.get(folder) or {}).get("found") or time.time()))
        for folder in no_tmdb:
            if (old.get(folder) or {}).get("state") in ("añadido", "completada"):
                continue
            c.execute("INSERT OR REPLACE INTO missing (folder, series_name, count, state, detail, found) "
                      "VALUES (?, ?, 0, 'falta_tmdb', 'FALTA TMDB', ?)",
                      (folder, folder, (old.get(folder) or {}).get("found") or time.time()))
    meta_set("missing_last_scan", time.time())
    meta_set("missing_retry_at", 0)
    meta_set("missing_not_found", json.dumps(sorted(not_found), ensure_ascii=False))
    meta_set("missing_complete", complete)
    meta_set("missing_note", "")
    result = (f"{queried} series consultadas, {complete} completas según Seerr, {len(no_tmdb)} sin TMDB, "
              f"{total} episodios en {len(found)} series")
    meta_set("missing_result", result)  # se muestra en la página junto a "Última búsqueda"
    log(f"Episodios que faltan: {queried} series consultadas (parciales según Seerr), {complete} completas "
        f"según Seerr, {len(no_tmdb)} sin TMDB, {total} episodios en {len(found)} series"
        + (f", {len(not_found)} sin versión en el catálogo" if not_found else "")
        + f" ({int(time.time() - started)} s)")
    if s["missing_autoadd"] and not s["dry_run"] and found:
        added = 0
        with db() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM missing WHERE state = 'pendiente'")]
        for row in rows:
            try:
                added += add_missing(row, xf)
            except Exception as e:  # noqa: BLE001
                log(f"Episodios que faltan: no se pudo añadir {row['folder']}: {e}", "error")
        log(f"Episodios que faltan: {added} episodios añadidos a la cola automáticamente")
        meta_set("missing_result", f"{result} · {added} añadidos a la cola")


def hhmm(text, default):
    try:
        h, m = (int(x) for x in str(text).strip().split(":"))
        return h * 60 + m
    except (ValueError, TypeError):
        return default


def missing_due(s):
    """¿Toca la búsqueda diaria de episodios que faltan? Una vez al día, dentro de la
    ventana horaria (hora local del puente)."""
    now = time.localtime()
    minute = now.tm_hour * 60 + now.tm_min
    start, end = hhmm(s["missing_from"], 17 * 60 + 5), hhmm(s["missing_to"], 23 * 60)
    inside = start <= minute < end if start <= end else (minute >= start or minute < end)
    if not inside:
        return False
    if float(meta_get("missing_retry_at", 0) or 0) > time.time():
        return False  # búsqueda cancelada hace poco: se espera a la hora del reintento
    last = float(meta_get("missing_last_scan", 0) or 0)
    if not last:
        return True
    # "Hoy" empieza en el inicio de la ventana (por si la ventana cruza la medianoche)
    started = time.mktime(now) - ((minute - start) % (24 * 60)) * 60 - now.tm_sec
    return last < started


def run_cycle():
    s = load_settings()
    if not s["seerr_api_key"]:
        _status["last_error"] = "Falta la clave de API de Seerr"
        return
    seerr, xf, dry = Seerr(s), XF(s), s["dry_run"]
    _cycle.clear()
    now = time.time()
    try:
        retry_failed(s, xf)
    except Exception as e:  # noqa: BLE001
        log(f"Error al reintentar descargas: {e}", "error")
    seen = set()
    reqs = []
    for req in seerr.requests("pending"):
        if req.get("is4k"):
            continue
        rid = req["id"]
        seen.add(rid)
        user = req.get("requestedBy") or {}
        set_request(rid, user_name=user.get("displayName"), jf_user=user.get("jellyfinUserId"))
        prev = get_request(rid)
        if prev and prev["state"] == ST_WAITING and (prev["next_retry"] or 0) > now and not _force.get("all"):
            continue
        try:
            approved, detail = process_pending(req, s, seerr, xf, dry)
        except Exception as e:  # noqa: BLE001
            log(f"Error con la petición pendiente {rid}: {e}", "error")
            set_request(rid, state=ST_ERROR, detail=str(e)[:500], next_retry=now + s["retry_hours"] * 3600)
            continue
        if approved:
            reqs.append(approved)
        elif detail.startswith("Rechazada"):
            set_request(rid, state=ST_DECLINED, detail=detail, next_retry=0)
        elif detail.startswith("Pendiente"):
            set_request(rid, state=ST_WAITING, detail=detail, next_retry=now + s["retry_hours"] * 3600)
        else:
            set_request(rid, state=ST_DRY, detail=detail, next_retry=0)
    reqs += [r for r in seerr.requests("approved") if not r.get("is4k") and r["id"] not in seen]
    reqs.sort(key=lambda r: r["id"])  # el primero que pidió, primero en la cola
    for req in reqs:
        rid = req["id"]
        seen.add(rid)
        user = req.get("requestedBy") or {}
        set_request(rid, user_name=user.get("displayName"), jf_user=user.get("jellyfinUserId"))
        prev = get_request(rid)
        if prev:
            if prev["state"] in (ST_QUEUED, ST_DONE, ST_CONVERTING):
                continue
            if prev["state"] in (ST_WAITING, ST_ERROR) and (prev["next_retry"] or 0) > now and not _force.get("all"):
                continue
        try:
            if req["type"] == "movie":
                state, detail, choice = process_movie(req, s, seerr, xf, dry)
            else:
                state, detail, choice = process_tv(req, s, seerr, xf, dry)
        except Exception as e:  # noqa: BLE001
            state, detail, choice = ST_ERROR, str(e)[:500], None
            log(f"Error con la petición {rid}: {e}", "error")
            traceback.print_exc()
        retry = now + s["retry_hours"] * 3600 if state in (ST_WAITING, ST_ERROR) else 0
        if prev is None or prev["state"] != state or prev["detail"] != detail:
            if state == ST_WAITING:
                log(f"Petición {rid} en espera: {detail}")
            elif state == ST_DRY:
                log(f"[PRUEBA] Petición {rid}: {detail}")
        set_request(rid, state=state, detail=detail, choice=choice, next_retry=retry)

    # Peticiones en cola que ya no están "aprobadas": completadas o borradas en Seerr
    with db() as c:
        pending = [dict(r) for r in c.execute("SELECT id, state FROM requests WHERE state IN (?, ?, ?)",
                                              (ST_QUEUED, ST_WAITING, ST_DRY))]
    for r in pending:
        if r["id"] in seen:
            continue
        full = seerr.request(r["id"])
        if full is None:
            set_request(r["id"], state=ST_DONE, detail="Petición eliminada en Seerr")
        elif full.get("status") == 5 or (full.get("media") or {}).get("status") == 5:
            set_request(r["id"], state=ST_CONVERTING if conversion_pending(r["id"]) else ST_DONE,
                        detail="Disponible en Jellyfin")


_force = {}


def worker():
    while True:
        s = load_settings()
        _status["running"] = True
        try:
            with _lock:
                run_cycle()
                if s["missing_enabled"] or _force.get("missing"):
                    if _force.get("missing") or missing_due(s):
                        _status["missing_running"] = True
                        try:
                            missing_scan(s)
                        finally:
                            _status["missing_running"] = False
            _status["last_error"] = None
        except Exception as e:  # noqa: BLE001
            _status["last_error"] = str(e)[:300]
            log(f"Error en la revisión: {e}", "error")
            traceback.print_exc()
        finally:
            _force.clear()
            _status["running"] = False
            _status["last_run"] = time.time()
            _status["next_run"] = time.time() + s["poll_minutes"] * 60
        _wake.wait(s["poll_minutes"] * 60)
        _wake.clear()


# ---------------------------------------------------------------------------
# Pruebas de conexión
# ---------------------------------------------------------------------------

def test_connection(which, s):
    try:
        if which == "seerr":
            st, p = http("GET", s["seerr_url"] + "/api/v1/settings/about", {"X-Api-Key": s["seerr_api_key"]}, timeout=15)
            if st == 200:
                return True, f"Conectado a Seerr {p.get('version', '')}"
            return False, f"Seerr respondió {st} (¿clave de API correcta?)"
        if which == "jellyfin":
            st, p = http("GET", s["jellyfin_url"] + "/System/Info", jf_auth(s), timeout=15)
            if st == 200:
                return True, f"Conectado a {p.get('ServerName', 'Jellyfin')} {p.get('Version', '')}"
            return False, f"Jellyfin respondió {st} (¿clave de API correcta?)"
        if which == "playlist":
            xf = XF(s)
            return True, (f"Conectado: {len(xf.catalog('vod'))} películas y {len(xf.catalog('series'))} series "
                          "(ya filtradas por XtreamFilter)")
        if which == "shrinkerr":
            st, p = http("GET", s["shrinkerr_url"].rstrip("/") + "/api/jobs/stats",
                         {"X-Api-Key": s["shrinkerr_api_key"]} if s.get("shrinkerr_api_key") else {}, timeout=15)
            if st == 200:
                return True, f"Conectado a shrinkerr ({p.get('pending', '?')} pendientes)" if isinstance(p, dict) else "Conectado a shrinkerr"
            return False, f"Shrinkerr respondió {st}"
        if which == "xtreamfilter":
            srcs = XF(s).sources()
            names = ", ".join(f"{x.get('name')} (/{x.get('route')})" for x in srcs)
            return True, f"Conectado. Fuentes: {names}"
    except Exception as e:  # noqa: BLE001
        return False, f"No se pudo conectar: {e}"
    return False, "Servicio desconocido"


# ---------------------------------------------------------------------------
# Servidor web
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(APP_DIR, "index.html"), "rb") as f:
                html = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif path == "/health":
            self.send_json({"ok": True})
        elif path == "/api/settings":
            self.send_json(public_settings(load_settings()))
        elif path == "/api/status":
            self.send_json(_status)
        elif path == "/api/requests":
            with db() as c:
                rows = [dict(r) for r in c.execute("SELECT * FROM requests ORDER BY updated DESC LIMIT 300")]
                counts = {}
                for r in c.execute("SELECT request_id, shrink, COUNT(*) n FROM added WHERE shrink IS NOT NULL GROUP BY 1, 2"):
                    counts.setdefault(r["request_id"], {})[r["shrink"]] = r["n"]
            for r in rows:
                r["shrink"] = counts.get(r["id"])
            self.send_json(rows)
        elif path == "/api/missing":
            with db() as c:
                rows = [dict(r) for r in c.execute(
                    "SELECT folder, series_name, grp, count, state, detail, found FROM missing ORDER BY state DESC, folder")]
                ign = [r[0] for r in c.execute("SELECT folder FROM ignored ORDER BY folder")]
                ncomplete = int(meta_get("missing_complete", 0) or 0)
            last = meta_get("missing_last_scan")
            self.send_json({"items": rows, "ignored": ign, "last_scan": float(last) if last else None,
                            "not_found": json.loads(meta_get("missing_not_found", "[]")),
                            "running": bool(_status.get("missing_running")),
                            "complete": ncomplete, "note": meta_get("missing_note", "") or "",
                            "result": meta_get("missing_result", "") or ""})
        elif path == "/api/log":
            with db() as c:
                rows = [dict(r) for r in c.execute("SELECT * FROM log ORDER BY id DESC LIMIT 200")]
            self.send_json(rows)
        else:
            self.send_json({"error": "no encontrado"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/settings":
            s = save_settings(self.body())
            log("Ajustes guardados" + (" (modo prueba)" if s["dry_run"] else " (modo real)"))
            _wake.set()
            self.send_json(public_settings(s))
        elif path.startswith("/api/test/"):
            s = load_settings()
            s.update({k: v for k, v in self.body().items() if v not in ("", None)})
            ok, msg = test_connection(path.rsplit("/", 1)[1], s)
            self.send_json({"ok": ok, "message": msg})
        elif path == "/api/run":
            _force["all"] = True
            _wake.set()
            self.send_json({"ok": True})
        elif path == "/api/reset":
            rid = self.body().get("id")
            with db() as c:
                c.execute("DELETE FROM requests WHERE id = ?", (rid,))
            _wake.set()
            self.send_json({"ok": True})
        elif path == "/api/clear-completed":
            with db() as c:
                n = c.execute("DELETE FROM requests WHERE state IN (?, ?)", (ST_DONE, ST_DECLINED)).rowcount
            log(f"Borradas {n} peticiones completadas y rechazadas de la lista")
            self.send_json({"ok": True, "deleted": n})
        elif path == "/api/missing/recheck":
            with db() as c:
                n = c.execute("DELETE FROM complete").rowcount
            log(f"Episodios que faltan: {n} series completas se revisarán en la próxima búsqueda")
            self.send_json({"ok": True, "cleared": n})
        elif path == "/api/missing/scan":
            _force["missing"] = True
            _wake.set()
            self.send_json({"ok": True})
        elif path in ("/api/missing/add", "/api/missing/ignore", "/api/missing/unignore"):
            folder = self.body().get("folder")
            try:
                with _lock:
                    if path.endswith("/add"):
                        with db() as c:
                            row = c.execute("SELECT * FROM missing WHERE folder = ?", (folder,)).fetchone()
                        if not row:
                            raise RuntimeError("Serie no encontrada en la lista")
                        add_missing(dict(row), XF(load_settings()))
                    elif path.endswith("/ignore"):
                        with db() as c:
                            c.execute("INSERT OR IGNORE INTO ignored (folder, added) VALUES (?, ?)", (folder, time.time()))
                            c.execute("DELETE FROM missing WHERE folder = ?", (folder,))
                        log(f"Episodios que faltan: serie ignorada {folder}")
                    else:
                        with db() as c:
                            c.execute("DELETE FROM ignored WHERE folder = ?", (folder,))
                        log(f"Episodios que faltan: serie ya no ignorada {folder}")
                self.send_json({"ok": True})
            except Exception as e:  # noqa: BLE001
                self.send_json({"ok": False, "message": str(e)}, 500)
        elif path == "/api/clear-log":
            with db() as c:
                n = c.execute("DELETE FROM log").rowcount
            self.send_json({"ok": True, "deleted": n})
        elif path == "/api/cancel":
            rid = self.body().get("id")
            try:
                with _lock:
                    done = cancel_request(int(rid))
                self.send_json({"ok": True, "done": done})
            except Exception as e:  # noqa: BLE001
                log(f"Error al cancelar la petición {rid}: {e}", "error")
                self.send_json({"ok": False, "message": str(e)}, 500)
        elif path == "/webhook":
            p = self.body()
            log(f"Aviso de Seerr: {p.get('notification_type', '?')} {p.get('subject', '')}".strip())
            _wake.set()
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "no encontrado"}, 404)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    init_db()
    if not os.path.exists(SETTINGS_PATH):
        save_settings({})
    log(f"seerr-bridge {VERSION} iniciado en el puerto {PORT}")
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=notifier, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
