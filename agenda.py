#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cartelera de espectáculos de Mérida — próximos 3 meses.

Ventana dinámica: desde hoy hasta la misma fecha de dentro de 3 meses.
Incluye EXCLUSIVAMENTE:
- Teatro y monólogos/stand-up
- Musicales, danza y grandes espectáculos escénicos
- Cine comercial y ciclos de cine de autor/VOSE
- Conciertos o giras musicales en sala/auditorio

Excluye:
- Exposiciones
- Conferencias, congresos, jornadas y presentaciones
- Talleres y actividades formativas
- Actividades deportivas
- Eventos infantiles/escolares de barrio
- Actos institucionales

Telegram: HTML seguro, agrupación por recinto y título, sesiones agrupadas y máximo 4 mensajes.
"""
from __future__ import annotations

import os
import re
import html
import hashlib
import logging
import time
import calendar
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")
TODAY = datetime.now(TZ).date()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CarteleraMerida3Meses/1.0)"
}
TIMEOUT = 25
TELEGRAM_HARD_LIMIT = 4096
SAFETY_MARGIN = 200
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

VENUE_ALIASES = {
    "teatro maría luisa": "Teatro María Luisa",
    "teatro maria luisa": "Teatro María Luisa",
    "palacio de congresos": "Palacio de Congresos",
    "teatro romano": "Teatro Romano",
    "centro cultural alcazaba": "Centro Cultural Alcazaba",
    "sala trajano": "Sala Trajano",
    "cines victoria": "Cines Victoria",
    "cine victoria": "Cines Victoria",
    "cineclub fórum": "Cineclub Fórum",
    "cineclub forum": "Cineclub Fórum",
    "cine club fórum": "Cineclub Fórum",
    "cine club forum": "Cineclub Fórum",
    "acueducto de los milagros": "Acueducto de los Milagros",
}

GENRE_LABELS = ("Teatro", "Musical", "Cine", "Monólogo", "Concierto", "Danza")

# Orden fijo solicitado para Telegram.
VENUE_ORDER = {
    "Cines Victoria": 0,
    "Palacio de Congresos": 1,
    "Teatro María Luisa": 2,
    "Teatro Romano": 3,
    "Sala Trajano": 4,
    "Centro Cultural Alcazaba": 5,
    "Cineclub Fórum": 6,
    "Otros recintos": 7,
}
VENUE_TICKET_LINKS = {
    "Cines Victoria": "https://www.cinesvictoria.com/cine/M%C3%A9rida/",
    "Palacio de Congresos": "https://www.palcongrex.es/agenda/tabla",
    "Teatro María Luisa": "https://www.teatromarialuisa.org/",
    "Teatro Romano": "https://www.consorciomerida.org/",
    "Centro Cultural Alcazaba": "https://merida.es/",
    "Cineclub Fórum": "https://festivalcinemerida.com/cineclub/",
    "Sala Trajano": "https://merida.es/agenda/",
}

@dataclass
class Event:
    title: str
    start: date
    end: date | None = None
    time: str = ""
    location: str = ""
    description: str = ""
    url: str = ""
    ticket_url: str = ""
    source: str = ""
    city: str = "Mérida"
    category: str = ""
    organizer: str = ""
    genre: str = ""
    score: int = 0
    tags: list[str] = field(default_factory=list)
    session_times: list[str] = field(default_factory=list)
    # Cartelera de Cines Victoria por fecha (ISO -> sesiones). Se conserva
    # para mostrar la semana completa y destacar las sesiones de HOY.
    cinema_sessions_by_date: dict[str, list[str]] = field(default_factory=dict)
    cinema_general_price: str = ""
    cinema_spectator_price: str = ""
    cinema_spectator_day: str = "miércoles"
    cinema_promotion: str = ""
    cinema_movie_url: str = ""
    cinema_info_only: bool = False

    def key(self) -> str:
        raw = f"{norm(self.title)}|{self.start.isoformat()}|{norm(canonical_venue(self.location, self.organizer))}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

END_DATE = add_months(TODAY, 3)

def next_friday(d: date) -> date:
    """Viernes de la ejecución; si no se ejecuta un viernes, el siguiente."""
    return d + timedelta(days=(4 - d.weekday()) % 7)

FRIDAY_DATE = next_friday(TODAY)

def canonical_venue(location: str, organizer: str = "") -> str:
    if norm(organizer) == "cine club fórum" or norm(organizer) == "cine club forum":
        return "Cineclub Fórum"
    v = (location or "").split(",", 1)[0]
    v = re.sub(r"\s*\([^)]*\)", "", v)
    v = re.sub(r"\s+", " ", v).strip()
    vn = norm(v)
    for frag in sorted(VENUE_ALIASES, key=len, reverse=True):
        if frag in vn:
            return VENUE_ALIASES[frag]
    return v or "Recinto no indicado"

def norm(s: str) -> str:
    s = html.unescape(s or "")
    return re.sub(r"\s+", " ", s).strip().lower()

def clean_text(s: str, max_len: int = 420) -> str:
    s = html.unescape(s or "")
    s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(más información|comprar entradas|ver más|leer más)\s*:?", "", s, flags=re.I)
    if len(s) > max_len:
        s = s[:max_len].rsplit(" ", 1)[0] + "…"
    return s

def fetch(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as exc:
        logging.warning("Fuente no disponible %s: %s", url, exc)
        return None

def parse_date(text: str) -> tuple[date | None, date | None]:
    text = clean_text(text, 300)
    # ISO dates first
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        d1 = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d1, d1
    # Spanish ranges: 23 septiembre 2026 - 27 septiembre 2026
    months = {
        "enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,
        "julio":7,"agosto":8,"septiembre":9,"setiembre":9,"octubre":10,
        "noviembre":11,"diciembre":12
    }
    month_names = "|".join(months.keys())
    # Rango con el año solo en la segunda fecha, p.ej. "15 de septiembre - 13 de
    # octubre de 2026" (o "del 15 de septiembre al 13 de octubre de 2026"). Sin
    # esto solo se reconocía la segunda fecha y el rango quedaba en un único día.
    m = re.search(
        rf"(\d{{1,2}})\s+de\s+({month_names})(?:\s+de\s+(\d{{4}}))?\s*(?:[-–—]|\bal\b)\s*"
        rf"(\d{{1,2}})\s+de\s+({month_names})\s+de\s+(\d{{4}})", text, re.I)
    if m:
        m1, m2 = months[m.group(2).lower()], months[m.group(5).lower()]
        y2 = int(m.group(6))
        y1 = int(m.group(3)) if m.group(3) else (y2 - 1 if m1 > m2 else y2)
        return date(y1, m1, int(m.group(1))), date(y2, m2, int(m.group(4)))
    pat = re.compile(rf"(\d{{1,2}})\s+(?:de\s+)?({month_names})\s*(?:de\s*)?(\d{{4}})", re.I)
    vals = list(pat.finditer(text))
    if vals:
        a = vals[0]
        d1 = date(int(a.group(3)), months[a.group(2).lower()], int(a.group(1)))
        if len(vals) > 1:
            b = vals[1]
            d2 = date(int(b.group(3)), months[b.group(2).lower()], int(b.group(1)))
        else:
            d2 = d1
        return d1, d2
    # Spanish date WITHOUT explicit year (frecuente en titulares/resúmenes de prensa,
    # p.ej. "29 de mayo"). dateutil no reconoce nombres de mes en español y, si se le
    # deja adivinar, sustituye el mes por el actual — así que aquí lo resolvemos a mano,
    # asumiendo el año en curso. Si el resultado queda en el pasado, el código que llama
    # a parse_date ya descarta esas fechas (no se "adelanta" al año que viene sin base).
    pat_no_year = re.compile(rf"(\d{{1,2}})\s+(?:de\s+)?({month_names})\b", re.I)
    vals = list(pat_no_year.finditer(text))
    if vals:
        a = vals[0]
        d1 = date(TODAY.year, months[a.group(2).lower()], int(a.group(1)))
        if len(vals) > 1:
            b = vals[1]
            d2 = date(TODAY.year, months[b.group(2).lower()], int(b.group(1)))
        else:
            d2 = d1
        return d1, d2
    # Rango numérico sin nombre de mes: "Del 25-09-2026 al 26-09-2026", también
    # con "/" o simplemente dos fechas seguidas separadas por "-" o "al".
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4}).{0,12}?(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        d1 = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        d2 = date(int(m.group(6)), int(m.group(5)), int(m.group(4)))
        return d1, d2
    # DD/MM/YYYY
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        d1 = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return d1, d1
    # Meses abreviados en español/inglés de 3 letras con año: "23 Sep 2026", "02 Oct 2026"
    # (formato usado, entre otros, por la tabla de Palcongrex).
    months_abbr = {
        "ene":1,"jan":1,"feb":2,"mar":3,"abr":4,"apr":4,"may":5,"jun":6,
        "jul":7,"ago":8,"aug":8,"sep":9,"set":9,"oct":10,"nov":11,"dic":12,"dec":12,
    }
    abbr_names = "|".join(months_abbr.keys())
    pat_abbr = re.compile(rf"\b(\d{{1,2}})\s+({abbr_names})\.?\s+(\d{{4}})\b", re.I)
    m = pat_abbr.search(text)
    if m:
        mon = months_abbr[m.group(2).lower()]
        d1 = date(int(m.group(3)), mon, int(m.group(1)))
        return d1, d1
    # Nota: se ha retirado el último recurso de dateutil "fuzzy" (sin patrón conocido).
    # En texto en español, dateutil no reconoce los nombres de mes y adivinaba fechas
    # incorrectas (p.ej. tomaba solo el día "29" y le ponía el mes/año de HOY), lo cual
    # era peor que no encontrar fecha: mostraba fechas erróneas en vez de ningún dato.
    return None, None

def extract_time(text: str) -> str:
    m = re.search(r"\b([01]?\d|2[0-3])[:.][0-5]\d\b", text)
    return m.group(0).replace(".", ":") if m else ""

def make_event(title, start, end=None, time="", location="", description="", url="", source="", city="Mérida", category="", organizer=""):
    ev = Event(
        title=clean_text(title, 180), start=start, end=end, time=extract_time(time or description),
        location=clean_text(location, 160), description=clean_text(description, 500),
        url=url, source=source, city=city, category=category, organizer=clean_text(organizer, 160)
    )
    classify(ev)
    return ev

# Enlaces/textos de navegación que nunca deben interpretarse como eventos.
_MERIDA_NAV_PATH_BLACKLIST = {
    "agenda", "lista", "categoria", "categoría", "dia", "mes", "buscar",
    "page", "pagina", "página", "events",
}
_MERIDA_NAV_TEXT_BLACKLIST = {
    "eventos", "event", "agenda", "lista", "calendario", "buscar",
    "siguiente", "anterior", "next", "previous", "más antiguos", "mas antiguos",
    "ver más", "ver mas", "leer más", "leer mas", "comprar entradas",
}

def _merida_is_real_event_link(href: str, text: str) -> bool:
    if "?" in href or "merida.es/agenda/" not in href:
        return False
    path = urlparse(href).path.strip("/")
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        # Solo "/agenda" o "/agenda/": es la portada del listado, no un evento.
        return False
    # El primer segmento siempre es /agenda/; solo bloqueamos subrutas de navegación.
    if any(norm(seg) in _MERIDA_NAV_PATH_BLACKLIST for seg in segments[1:]):
        return False
    text_n = norm(text)
    if not text_n or text_n in _MERIDA_NAV_TEXT_BLACKLIST:
        return False
    return True

def scrape_merida_api(days: int = 90, max_pages: int = 6) -> list[Event]:
    """API REST pública del plugin 'The Events Calendar' (wp-json/tribe/events/v1/events).
    merida.es carga la lista de eventos por JavaScript/AJAX, así que una petición
    simple con requests.get() a /agenda/ casi siempre recibe la página vacía (0
    eventos) aunque un navegador sí vea el listado completo. Esta API del propio
    plugin devuelve los datos en JSON, sin depender de que se ejecute JavaScript.

    Se piden hasta `days` días (por defecto 90, para cubrir tanto la agenda de los
    próximos días como los "futuros destacados") y se pagina hasta `max_pages`
    páginas de 50 eventos cada una, para no quedarnos cortos si hay muchos."""
    start = TODAY
    end = TODAY + timedelta(days=days)
    events = []
    for page in range(1, max_pages + 1):
        url = (
            "https://merida.es/wp-json/tribe/events/v1/events"
            f"?start_date={start.isoformat()}&end_date={end.isoformat()}&per_page=50&page={page}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            if page == 1:
                logging.warning("API de eventos de merida.es no disponible (%s), se usará el scraping HTML de respaldo", exc)
            break
        page_events = data.get("events", []) or []
        if not page_events:
            break
        for item in page_events:
            title = clean_text(item.get("title", ""), 180)
            title = re.sub(r"^(?:teatro|concierto|musical|danza|monólogo|monologo)\s*:\s*", "", title, flags=re.I)
            if not title or norm(title) in _MERIDA_NAV_TEXT_BLACKLIST:
                continue
            sd_raw = (item.get("start_date") or "")[:10]
            ed_raw = (item.get("end_date") or item.get("start_date") or "")[:10]
            try:
                st = date.fromisoformat(sd_raw) if sd_raw else None
                en = date.fromisoformat(ed_raw) if ed_raw else st
            except Exception:
                st = en = None
            if not st:
                continue
            tm = ""
            m = re.search(r"\d{4}-\d{2}-\d{2} (\d{2}:\d{2}):\d{2}", item.get("start_date", ""))
            if m and m.group(1) != "00:00":
                tm = m.group(1)
            venue = item.get("venue") or {}
            loc = clean_text(", ".join(p for p in [venue.get("venue", ""), venue.get("address", "")] if p), 160)
            desc_html = item.get("description") or item.get("excerpt") or ""
            desc = clean_text(BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True), 600) if desc_html else ""
            href = item.get("url") or url
            # Categorías ("Infantil", "Literatura"...) y organizador ("Biblioteca
            # Municipal Juan Pablo Forner"...) que publica el propio Ayuntamiento.
            # Se leen de forma defensiva: si la API no los trae, quedan vacíos.
            cats = [c.get("name", "") for c in (item.get("categories") or []) if isinstance(c, dict)]
            orgs = [o.get("organizer", "") for o in (item.get("organizer") or []) if isinstance(o, dict)]
            category = clean_text(", ".join(c for c in cats if c), 120)
            organizer = clean_text(", ".join(o for o in orgs if o), 160)
            ev = make_event(title, st, en, tm, loc, desc, href, "Ayuntamiento de Mérida", category=category, organizer=organizer)
            events.append(ev)
        # Si la página ha devuelto menos de 50, ya no hay más páginas que pedir.
        if len(page_events) < 50:
            break
    return events

def scrape_merida(max_pages=8) -> list[Event]:
    # 1) API REST oficial del plugin: fiable, no depende de que se ejecute JavaScript.
    events = scrape_merida_api()
    if events:
        return events
    # 2) Respaldo por si la API cambia o se desactiva algún día: scraping de HTML
    #    (puede devolver 0 si la lista se carga por JavaScript, pero es mejor que nada).
    events = []
    for page in range(1, max_pages + 1):
        url = "https://merida.es/agenda/" if page == 1 else f"https://merida.es/agenda/lista/p%C3%A1gina/{page}/"
        soup = fetch(url)
        if not soup:
            break
        links = []
        # Varios selectores candidatos, porque el plugin de eventos cambia de versión
        # (a veces h3, a veces h4, a veces solo la clase) y no siempre coinciden.
        for a in soup.select(
            "h1 a[href], h2 a[href], h3 a[href], h4 a[href], "
            "article a[href], "
            ".tribe-events-calendar-list__event-title a[href], .tribe-event-title a[href], "
            ".tribe-events-calendar-list__event-title a[href]"
        ):
            href = urljoin(url, a.get("href", ""))
            txt = clean_text(a.get_text(" ", strip=True), 160)
            if href not in links and _merida_is_real_event_link(href, txt):
                links.append(href)
        # Respaldo: cualquier enlace cuya URL y texto pasen el mismo filtro estricto
        # (ya no basta con contener "/agenda/", porque eso cuela enlaces de navegación).
        if not links:
            for a in soup.select("a[href]"):
                href = urljoin(url, a.get("href", ""))
                txt = clean_text(a.get_text(" ", strip=True), 160)
                if href not in links and _merida_is_real_event_link(href, txt):
                    links.append(href)
        for href in links:
            detail = fetch(href)
            if not detail:
                continue
            title = detail.select_one("h1")
            title_text = title.get_text(" ", strip=True) if title else ""
            title_text = re.sub(r"^(?:teatro|concierto|musical|danza|monólogo|monologo)\s*:\s*", "", title_text, flags=re.I)
            if not title_text or norm(title_text) in _MERIDA_NAV_TEXT_BLACKLIST:
                continue
            body = detail.select_one("main") or detail
            txt = body.get_text(" ", strip=True)
            start, end = parse_date(txt)
            if not start:
                continue
            # WordPress event metadata / visible location
            loc_nodes = detail.select(
                ".tribe-events-meta-group, .tribe-events-single-section, address, .tribe-events-venue-details"
            )
            if len(loc_nodes) > 3:
                # Una ficha de evento individual tiene un único bloque de ubicación;
                # más de eso indica que en realidad se ha cargado una página de listado.
                continue
            loc = " ".join(node.get_text(" ", strip=True) for node in loc_nodes)
            time = extract_time(txt)
            desc = ""
            desc_container = detail.select_one(
                ".tribe-events-single-event-description, .tribe-events-content, article .entry-content"
            )
            for p in (desc_container or body).select("p"):
                st = clean_text(p.get_text(" ", strip=True), 600)
                if len(st) > 60 and norm(st) != _MERIDA_LISTING_BOILERPLATE:
                    desc = st
                    break
            events.append(make_event(title_text, start, end, time, loc, desc, href, "Ayuntamiento de Mérida"))
    return events

def scrape_palcongrex() -> list[Event]:
    """Agenda oficial de Mérida de Palcongrex. La tabla actual usa tres columnas: Título | Tipo | Fecha inicio."""
    base = "https://www.palcongrex.es/merida/agenda/tabla"
    events, seen = [], set()
    for page in range(8):
        url = base if page == 0 else f"{base}?page={page}"
        soup = fetch(url)
        if not soup: break
        page_found = 0
        for row in soup.select("tr"):
            cells = [clean_text(x.get_text(" ", strip=True), 300) for x in row.select("td")]
            if len(cells) < 3: continue
            title, typ, date_text = cells[:3]
            st, en = parse_date(date_text)
            if not title or not st or not (TODAY <= st <= END_DATE): continue
            key=(norm(title),st.isoformat())
            if key in seen: continue
            seen.add(key)
            a=row.find("a",href=True); href=urljoin(url,a.get("href")) if a else url
            ev=make_event(title,st,en,"","Palacio de Congresos","",href,"Palcongrex",city="Mérida",category=typ)
            # Palcongrex usa "Espectáculos" como categoría paraguas. No
            # descartamos un espectáculo válido por no contener una palabra
            # genérica en el título: aplicamos reglas específicas y después
            # conservamos el resto de "Espectáculos" como Teatro.
            t = norm(f"{title} {typ}")
            if any(k in t for k in ("ángel martín", "angel martin", "karim.", "juan amodeo", "impro - sible", "impro-sible")):
                ev.genre = "Monólogo"
            elif any(k in t for k in ("sara baras", "ballet", "danza", "lago de los cisnes", "vivancos")):
                ev.genre = "Danza"
            elif any(k in t for k in ("musical", "ópera", "opera", "zarzuela", "tributo al rey león", "tributo al rey leon", "pixar", "zootrópolis", "zootropolis", "k-power")):
                ev.genre = "Musical"
            elif any(k in t for k in ("orquesta", "concierto", "gira", "jazz", "música", "musica", "recital", "revolver", "dire straits", "mocedades", "rafa sánchez", "rafa sanchez", "medina azahara")):
                ev.genre = "Concierto"
            elif any(k in t for k in ("circo", "pando el mago", "pando", "mago")):
                ev.genre = "Teatro"
            elif norm(typ) == "espectáculos":
                ev.genre = "Teatro"
            if ev.genre:
                events.append(ev); page_found += 1
        if page_found==0 and page>0: break
    return events



# ---------------------------------------------------------------------------
# FILTRO ESTRICTO DE CARTELERA
# ---------------------------------------------------------------------------
EXCLUDE_KW = [
    "cancelado", "cancelada", "aplazado", "aplazada", "suspendido", "suspendida",
    "exposición", "exposicion", "conferencia", "congreso", "jornada",
    "taller", "curso", "formación", "formacion", "seminario",
    "mesa redonda", "presentación de libro", "presentacion de libro",
    "coloquio", "encuentro literario", "visita guiada", "actividad escolar",
    "actividad educativa", "deporte", "fútbol", "futbol", "carrera",
    "pleno municipal", "acto institucional", "inauguración", "inauguracion",
    "feria de empleo", "convención profesional", "convencion profesional",
]
GENRE_PATTERNS = {
    "Monólogo": [
        "monólogo", "monologo", "stand-up", "stand up", "humor"
    ],
    "Musical": [
        "musical", "ópera", "opera", "zarzuela"
    ],
    "Danza": [
        "danza", "ballet", "flamenco", "baile"
    ],
    "Concierto": [
        "concierto", "gira", "tour", "recital", "pianista", "orquesta",
        "festival de música", "festival flamenco", "música en directo"
    ],
    "Teatro": [
        "teatro", "obra", "comedia", "drama", "circo", "espectáculo escénico",
        "artes escénicas", "escena"
    ],
    "Cine": [
        "cine", "cinema", "película", "pelicula", "film", "ciclo de cine",
        "vose", "v.o.s.e.", "versión original", "version original"
    ],
}

def classify_event(ev: Event) -> None:
    text = norm(" ".join([ev.title, ev.category, ev.description]))
    title_cat = norm(" ".join([ev.title, ev.category]))
    # Exclusiones fuertes. "Cine" comercial se permite; una película se clasifica
    # como Cine aunque no contenga palabras de "ciclo".
    if any(k in text for k in EXCLUDE_KW):
        ev.genre = ""
        return

    # Los eventos escolares/de barrio con contenido infantil no entran solo por
    # mencionar niños. Solo se admiten si son claramente una producción escénica.
    if any(k in text for k in ["colegio", "ies ", "ceip ", "escolar", "barrio"]) and \
       not any(k in title_cat for k in ["teatro", "musical", "danza", "concierto", "monólogo", "monologo", "circo"]):
        ev.genre = ""
        return

    # Palcongrex usa "Espectáculos" como categoría paraguas. Debe sobrevivir
    # también a la segunda clasificación que hace dedupe().
    if norm(ev.source) == "palcongrex" and "espectáculos" in title_cat:
        ev.genre = "Teatro"
        return

    # Orden específico para que "musical basado en una película" NO sea cine.
    if any(k in title_cat for k in GENRE_PATTERNS["Musical"]):
        ev.genre = "Musical"
    elif any(k in title_cat for k in GENRE_PATTERNS["Danza"]):
        ev.genre = "Danza"
    elif any(k in title_cat for k in GENRE_PATTERNS["Concierto"]):
        ev.genre = "Concierto"
    elif any(k in title_cat for k in GENRE_PATTERNS["Monólogo"]):
        ev.genre = "Monólogo"
    elif any(k in title_cat for k in GENRE_PATTERNS["Cine"]):
        ev.genre = "Cine"
    elif any(k in title_cat for k in GENRE_PATTERNS["Teatro"]):
        ev.genre = "Teatro"
    else:
        ev.genre = ""

    # No convertir una simple mención de "película" en Cine.
    if ev.genre == "Cine" and any(k in title_cat for k in [
        "musical", "teatro", "monólogo", "monologo", "danza", "concierto", "gira"
    ]):
        ev.genre = "Musical" if "musical" in title_cat else (
            "Concierto" if any(k in title_cat for k in ["concierto", "gira"]) else "Teatro"
        )

def make_event(title, start, end=None, time="", location="", description="", url="", source="", city="Mérida", category="", organizer=""):
    ev = Event(
        title=clean_text(title, 180), start=start, end=end, time=extract_time(time or description),
        location=clean_text(location, 160), description=clean_text(description, 500),
        url=url, source=source, city=city, category=category,
        organizer=clean_text(organizer, 160)
    )
    classify_event(ev)
    return ev

def dedupe(events: list[Event]) -> list[Event]:
    result = {}
    for ev in events:
        classify_event(ev)
        if not ev.genre:
            continue
        k = ev.key()
        if k not in result:
            result[k] = ev
        else:
            old = result[k]
            if len(ev.description) > len(old.description):
                old.description = ev.description
            if not old.time and ev.time:
                old.time = ev.time
            if getattr(ev, "cinema_sessions_by_date", None):
                for day, times in ev.cinema_sessions_by_date.items():
                    old.cinema_sessions_by_date.setdefault(day, [])
                    old.cinema_sessions_by_date[day] = sorted(
                        set(old.cinema_sessions_by_date[day]) | set(times),
                        key=lambda x: (int(x[:2]), int(x[3:]))
                    )
            if old.title.isupper() and not ev.title.isupper():
                old.title = ev.title
            # Conservamos el enlace de la fuente oficial más directa.
            if ev.source in {"Ayuntamiento de Mérida", "Palcongrex", "Teatro María Luisa", "Cines Victoria"}:
                old.source, old.url = ev.source, ev.url
    return list(result.values())

def scrape_teatro_maria_luisa() -> list[Event]:
    """Recorre fichas reales /events/<slug>/ del Teatro María Luisa."""
    base="https://www.teatromarialuisa.org/"; pages=[base]; seen_pages=set(); links={}
    for _ in range(12):
        if not pages: break
        url=pages.pop(0)
        if url in seen_pages: continue
        seen_pages.add(url); soup=fetch(url)
        if not soup: continue
        for a in soup.select('a[href]'):
            href=urljoin(base,a.get('href','')); title=clean_text(a.get_text(' ',strip=True),180); path=urlparse(href).path.rstrip('/')+'/'
            if '/events/' in path and '/events/categories/' not in path and not path.endswith('/events/') and len(title)>=4: links[href]=title
            low=norm(title)
            if low in {'siguiente','next','older posts','más antiguos','mas antiguos'} or 'pno=' in href:
                if href not in seen_pages and href not in pages: pages.append(href)
        nxt=soup.select_one('a[rel="next"]')
        if nxt and nxt.get('href'):
            href=urljoin(url,nxt.get('href'))
            if href not in seen_pages and href not in pages: pages.append(href)
    events=[]
    for href,fallback in links.items():
        detail=fetch(href)
        if not detail: continue
        h=detail.select_one('h1'); title=clean_text(h.get_text(' ',strip=True),180) if h else fallback
        text=clean_text(detail.get_text(' ',strip=True),9000); st,en=parse_date(text)
        if not title or not st or not (TODAY<=st<=END_DATE): continue
        cat=''
        for a in detail.select('a[href*="/events/categories/"]'):
            x=clean_text(a.get_text(' ',strip=True),80)
            if x and norm(x) not in _MERIDA_NAV_TEXT_BLACKLIST: cat=x; break
        tm=extract_time(text); descs=[]
        for node in detail.select('main p,article p,.entry-content p,.elementor-widget-text-editor p'):
            x=clean_text(node.get_text(' ',strip=True),700); low=norm(x)
            if len(x)>=45 and not any(q in low for q in ['comprar entradas','taquilla y descuentos','más información']): descs.append(x)
        ev=make_event(title,st,en,tm,'Teatro María Luisa',' '.join(descs[:2]),href,'Teatro María Luisa',category=cat,city='Mérida')
        t=norm(cat+' '+title+' '+text[:1600])
        if any(k in t for k in ['monólogo','monologo','humor','stand-up']): ev.genre='Monólogo'
        elif any(k in t for k in ['música','musica','concierto','piano','ópera','opera']): ev.genre='Concierto'
        elif any(k in t for k in ['danza','flamenco','ballet']): ev.genre='Danza'
        elif 'musical' in t: ev.genre='Musical'
        elif any(k in t for k in ['teatro','comedia','drama','circo']): ev.genre='Teatro'
        elif 'cine' in t: ev.genre='Cine'
        if ev.genre: ev.session_times=[tm] if tm else []; events.append(ev)
    return events

def _extract_price(text: str, patterns: list[str]) -> str:
    """Extrae solo importes realmente acompañados de símbolo €; evita capturar días."""
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            value = m.group(1).replace(".", ",")
            if re.fullmatch(r"\d{1,2}(?:,\d{1,2})?", value):
                return value + " €"
    return ""

def _cinema_genre(title: str, description: str = "") -> str:
    """Género visible de la película, sin repetir 'Cine'."""
    t = norm(f"{title} {description}")
    if any(k in t for k in ("animación", "animacion", "anime", "pixar", "disney")):
        return "Animación"
    if any(k in t for k in ("terror", "horror", "miedo", "vampiro", "zombi", "zombie")):
        return "Terror"
    if any(k in t for k in ("thriller", "suspense")):
        return "Thriller"
    if any(k in t for k in ("ciencia ficción", "ciencia ficcion", "sci-fi", "futuro")):
        return "Ciencia ficción"
    if any(k in t for k in ("romance", "romántica", "romantica", "amor")):
        return "Romance"
    if any(k in t for k in ("comedia", "humor")):
        return "Comedia"
    if any(k in t for k in ("acción", "accion", "aventura")):
        return "Acción/Aventura"
    if "drama" in t:
        return "Drama"
    return "Película"

def _cinema_icon(text: str) -> str:
    t = norm(text)
    if any(k in t for k in ["terror", "horror", "miedo"]):
        return "👻"
    if any(k in t for k in ["acción", "accion", "aventura"]):
        return "💥"
    if any(k in t for k in ["animación", "animacion", "familiar"]):
        return "👨‍👩‍👧‍👦"
    if any(k in t for k in ["comedia", "humor"]):
        return "😂"
    if any(k in t for k in ["ciencia ficción", "ciencia ficcion", "sci-fi"]):
        return "🚀"
    if any(k in t for k in ["thriller", "suspense"]):
        return "🔪"
    if any(k in t for k in ["romance", "romántica", "romantica"]):
        return "❤️"
    if any(k in t for k in ["drama"]):
        return "🎭"
    return "🎬"

def scrape_cines_victoria() -> list[Event]:
    """Cartelera oficial de Mérida.

    La ejecución normal es semanal (viernes), así que Telegram muestra solo
    las sesiones del viernes de la semana que comienza. El enlace de cada
    película apunta a su ficha /pelicula/<id>/ para consultar horarios futuros.
    """
    url = "https://www.cinesvictoria.com/cine/M%C3%A9rida/"
    soup = fetch(url)
    if not soup:
        return []
    events = []
    weekday_pat = re.compile(r"\b(lunes|martes|miércoles|jueves|viernes|sábado|domingo)\s+(\d{1,2})\b", re.I)
    months = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,
              "julio":7,"agosto":8,"septiembre":9,"octubre":10,"noviembre":11,"diciembre":12}

    # Información general de la sede. No confundimos promociones con tarifa general.
    page_text = clean_text(soup.get_text(" ", strip=True), 30000)
    promotion = _extract_price(page_text, [r"TICKET DESCUENTO\s+Entrada\s*([0-9]+(?:[,.][0-9]{1,2})?)\s*€"])
    general_price = _extract_price(page_text, [
        r"(?:entrada\s+general|general)[^€]{0,50}([0-9]+(?:[,.][0-9]{1,2})?)\s*€",
        r"([0-9]+(?:[,.][0-9]{1,2})?)\s*€[^\n]{0,30}(?:entrada\s+general|general)"
    ])
    spectator_price = _extract_price(page_text, [
        r"(?:día\s+del\s+espectador)[^€]{0,80}([0-9]+(?:[,.][0-9]{1,2})?)\s*€",
        r"([0-9]+(?:[,.][0-9]{1,2})?)\s*€[^\n]{0,50}(?:día\s+del\s+espectador)"
    ])
    # No mostrar nunca una tarifa 0 € como si fuera un precio real.
    if spectator_price and re.fullmatch(r"0+(?:[,.]0+)?\s*€?", spectator_price.strip()):
        spectator_price = ""
    spectator_day = "miércoles" if re.search(r"Día del espectador", page_text, re.I) else ""

    headings = soup.select("h2, h3, h4")
    seen_titles = set()
    for heading in headings:
        title = clean_text(heading.get_text(" ", strip=True), 160)
        low_title = norm(title)
        if not title or low_title in {"cartelera y horarios de compra online", "cartelera y horarios", "venta anticipada", "en cartelera"}:
            continue
        block = heading
        txt = ""
        for _ in range(5):
            block = block.parent
            if block is None:
                break
            txt = clean_text(block.get_text(" ", strip=True), 4500)
            if weekday_pat.search(txt):
                break
        matches = list(weekday_pat.finditer(txt))
        if not matches:
            continue

        # Prefer the movie detail link; fallback to direct ticket purchase.
        movie_url = ""
        ticket_url = ""
        for a in block.select('a[href]'):
            href = urljoin(url, a.get("href", ""))
            if "/pelicula/" in href and not movie_url:
                movie_url = href
            at = norm(a.get_text(" ", strip=True))
            if "comprar entradas" in at and not ticket_url:
                ticket_url = href
        # Avoid treating the duplicate ficha heading as a second movie.
        identity = movie_url or norm(title)
        if identity in seen_titles:
            continue
        seen_titles.add(identity)

        by_date = {}
        for idx, m in enumerate(matches):
            day = int(m.group(2))
            # Derive month from nearby explicit date text, then from target Friday/current month.
            context = txt[max(0, m.start()-80):min(len(txt), m.end()+80)]
            month_m = re.search(r"\b(\d{1,2})\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b", context, re.I)
            mon = months[month_m.group(2).lower()] if month_m else FRIDAY_DATE.month
            try:
                d = date(TODAY.year + (1 if mon < TODAY.month and TODAY.month == 12 else 0), mon, day)
            except ValueError:
                continue
            if d < TODAY:
                try:
                    d = date(TODAY.year + 1, mon, day)
                except ValueError:
                    continue
            if not (TODAY <= d <= END_DATE):
                continue
            start_pos = m.end()
            next_pos = matches[idx+1].start() if idx + 1 < len(matches) else len(txt)
            segment = txt[start_pos:next_pos]
            times = sorted(set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", segment)),
                           key=lambda x: (int(x[:2]), int(x[3:])))
            if times:
                by_date[d] = times

        # Mostramos la cartelera de la semana en curso (hoy + próximos
        # seis días), pero conservamos las sesiones separadas por fecha para
        # poder destacar las de HOY en Telegram.
        week_end = TODAY + timedelta(days=6)
        week_schedule = {
            d.isoformat(): times
            for d, times in by_date.items()
            if TODAY <= d <= week_end and times
        }
        if not week_schedule:
            continue

        first_day = min(date.fromisoformat(k) for k in week_schedule)
        first_times = week_schedule[first_day.isoformat()]
        icon = _cinema_icon(txt)
        ev = make_event(title, TODAY, first_day, first_times[0],
                        "Cines Victoria", txt, ticket_url or movie_url or url,
                        "Cines Victoria", category="Cine", city="Mérida")
        ev.genre = "Cine"
        ev.session_times = first_times
        ev.cinema_sessions_by_date = week_schedule
        ev.cinema_general_price = general_price
        ev.cinema_spectator_price = spectator_price
        ev.cinema_spectator_day = spectator_day or "miércoles"
        ev.cinema_promotion = promotion
        ev.cinema_movie_url = movie_url
        ev.tags.append(icon)
        ev.ticket_url = ticket_url
        events.append(ev)

    cycle_seen = set()
    for a in soup.select("a[href]"):
        href = urljoin(url, a.get("href", ""))
        title = clean_text(a.get_text(" ", strip=True), 180)
        # El enlace del ciclo suele arrastrar texto de navegación. Lo limpiamos
        # para que Telegram muestre un título corto y útil.
        title = re.sub(r"\s+ver el ciclo(?:\s*[↓↘→])?$", "", title, flags=re.I)
        title = re.sub(r"\s+\d{1,2}\s+(?:de\s+)?[a-záéíóú]+\s*[–-]\s*\d{1,2}\s+(?:de\s+)?[a-záéíóú]+(?:\s+\d{4})?$", "", title, flags=re.I)
        low = norm(title)
        # El 40º Ciclo de Cine VOSE pertenece al Cineclub Fórum; no debe
        # duplicarse en Cines Victoria aunque la página de Victoria enlace a él.
        if _is_cineclub_cycle_title(title):
            continue
        if "ciclo" not in low or not href or href in cycle_seen:
            continue
        if "compraentradas.com" not in href and "/cine/" not in href:
            continue
        cycle_seen.add(href)
        ev = make_event(title, FRIDAY_DATE, FRIDAY_DATE, "", "Cines Victoria", "", href,
                        "Cines Victoria", category="Cine", city="Mérida")
        ev.genre = "Cine"
        ev.cinema_info_only = True
        ev.ticket_url = href
        ev.cinema_movie_url = ""
        ev.session_times = []
        ev.tags = ["🎬"]
        events.append(ev)
    return events


def _is_cineclub_cycle_title(title: str) -> bool:
    t = norm(title).replace("º", "o").replace("°", "o")
    return (
        "40o ciclo" in t
        or "40 ciclo" in t
        or ("ciclo de cine" in t and ("vose" in t or "v.o.s.e" in t))
    )


def scrape_cineclub_merida() -> list[Event]:
    """Agenda del Cine Club Fórum.

    La página actual presenta las sesiones como encabezados de fecha seguidos
    del título de la película. El scraper anterior dependía de contenedores
    article/card y podía devolver cero eventos aunque la programación estuviera
    visible.
    """
    url = "https://festivalcinemerida.com/cineclub/"
    soup = fetch(url)
    if not soup:
        return []

    events = []
    date_re = re.compile(
        r"^(?:#\s*)?(\d{1,2})\s+(?:de\s+)?"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
        r"octubre|noviembre|diciembre)\b.*?(\d{4})?$",
        re.I
    )
    month_map = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
        "noviembre": 11, "diciembre": 12,
    }

    headings = soup.select("h1, h2, h3, h4, h5")
    for idx, heading in enumerate(headings):
        date_text = clean_text(heading.get_text(" ", strip=True), 220)
        m = date_re.match(date_text)
        if not m:
            continue
        day = int(m.group(1))
        month = month_map[m.group(2).lower()]
        year = int(m.group(3)) if m.group(3) else TODAY.year
        try:
            st = date(year, month, day)
        except ValueError:
            continue
        if not (TODAY <= st <= END_DATE):
            continue

        title = ""
        title_node = None
        for nxt in headings[idx + 1: idx + 6]:
            candidate = clean_text(nxt.get_text(" ", strip=True), 220)
            if not candidate:
                continue
            if date_re.match(candidate):
                break
            low = norm(candidate)
            if low in {"lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"}:
                continue
            if "leer más" in low or "leer mas" in low:
                continue
            if _is_cineclub_cycle_title(candidate):
                continue
            # El detalle de cada película aparece como h4/h3. Quitamos el país
            # y año del título para que Telegram muestre solo el nombre.
            title = re.split(
                r"\s+[-–—]\s+(?:españa|ee\.uu\.|eeuu|francia|italia|alemania)\b",
                candidate, maxsplit=1, flags=re.I
            )[0].strip()
            title = clean_text(title, 180)
            title_node = nxt
            break
        if not title or len(title) < 3:
            continue

        parent_text = ""
        try:
            parent_text = clean_text(
                (title_node.parent if title_node else heading.parent).get_text(" ", strip=True),
                1200
            )
        except Exception:
            parent_text = date_text
        tm = extract_time(date_text) or extract_time(parent_text)
        href = url
        if title_node:
            a = title_node.find("a", href=True)
            if a:
                href = urljoin(url, a.get("href"))

        ev = make_event(
            title, st, st, tm, "Cineclub Fórum", parent_text, href,
            "Cine Club Fórum", category="Cine", city="Mérida"
        )
        ev.genre = "Cine"
        events.append(ev)

    # El ciclo se publica como actividad propia además de las películas.
    # Lo mantenemos como ficha informativa para que aparezca explícitamente en
    # Cineclub Fórum y no se pierda al deduplicar las sesiones.
    cycle_start = max(TODAY, date(2026, 9, 14))
    cycle_end = date(2026, 10, 26)
    if cycle_start <= END_DATE and cycle_end >= TODAY:
        cycle = make_event(
            "40º Ciclo de Cine VOSE", cycle_start, cycle_end, "",
            "Cineclub Fórum",
            "40º Ciclo de Cine VOSE · 14 septiembre al 26 de octubre de 2026",
            url, "Cine Club Fórum", category="Cine", city="Mérida"
        )
        cycle.genre = "Cine"
        cycle.cinema_info_only = True
        cycle.ticket_url = url
        events.append(cycle)

    unique = {}
    for ev in events:
        unique[(norm(ev.title), ev.start)] = ev
    return list(unique.values())

def is_valid_cartelera_event(ev: Event) -> bool:
    if ev.city and norm(ev.city) not in {"mérida", "merida"}:
        return False
    if ev.start < TODAY or ev.start > END_DATE:
        return False
    if ev.genre not in GENRE_LABELS:
        return False
    # Exclusiones finales, aplicadas también después de deduplicar.
    t = norm(" ".join([ev.title, ev.description, ev.category]))
    if any(k in t for k in EXCLUDE_KW):
        return False
    return True

TICKETING_DOMAINS = (
    "entradas.com", "elcorteingles.es/entradas", "giglon.com",
    "tomaticket.es", "enterticket.es", "taquilla.com"
)

def find_ticket_link(url: str) -> str:
    if not url:
        return ""
    try:
        soup = fetch(url)
        if not soup:
            return ""
        for a in soup.select("a[href]"):
            href = urljoin(url, a.get("href", ""))
            low = href.lower()
            if any(domain in low for domain in TICKETING_DOMAINS):
                return href
    except Exception:
        pass
    return ""

def enrich_ticket_links(events: list[Event], max_lookups: int = 80) -> list[Event]:
    # Solo se consulta la ficha oficial cuando todavía no disponemos de una
    # URL directa de ticketing. Si la plataforma cambia, el evento sigue
    # teniendo el enlace oficial como respaldo.
    for ev in events[:max_lookups]:
        if ev.url:
            ev.ticket_url = find_ticket_link(ev.url)
    return events

def collect_events() -> list[Event]:
    sources = [
        ("Ayuntamiento de Mérida", scrape_merida),
        ("Teatro María Luisa", scrape_teatro_maria_luisa),
        ("Palcongrex", scrape_palcongrex),
        ("Cines Victoria", scrape_cines_victoria),
        ("Cine Club Fórum", scrape_cineclub_merida),
    ]
    all_events = []
    for name, fn in sources:
        try:
            found = fn()
            logging.info("%s: %d candidatos", name, len(found))
            if not found: logging.warning("%s devolvió 0 candidatos", name)
            all_events.extend(found)
        except Exception as exc:
            logging.warning("%s falló: %s", name, exc)
    events = dedupe(all_events)
    events = [e for e in events if is_valid_cartelera_event(e)]
    # Una misma producción puede salir en fuentes distintas con pequeñas
    # diferencias de nombre. merge_repeated_events se conserva para las
    # sesiones/fechas repetidas del mismo título y recinto.
    events = merge_repeated_events(events)
    events = enrich_ticket_links(events)
    return sorted(events, key=lambda e: (e.start, e.time, norm(e.title)))

def merge_repeated_events(events: list[Event]) -> list[Event]:
    groups = {}
    for ev in events:
        key = f"{norm(ev.title)}|{norm(canonical_venue(ev.location, ev.organizer))}"
        groups.setdefault(key, []).append(ev)
    merged = []
    for group in groups.values():
        if len(group) == 1:
            merged.append(group[0]); continue
        # Para cine/comercial y funciones repetidas, no fusionamos todo el mes
        # en un rango: cada fecha publicada debe seguir siendo visible.
        # Por tanto, solo deduplicamos fechas idénticas.
        by_date = {}
        for ev in group:
            k = (ev.start, ev.time)
            old = by_date.get(k)
            if old is None or len(ev.description) > len(old.description):
                by_date[k] = ev
        merged.extend(by_date.values())
    return merged

_DIAS_ES = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

def fmt_date_es(d: date) -> str:
    return f"{_DIAS_ES[d.weekday()]} {d.strftime("%d/%m")}"

def fmt_month(d: date) -> str:
    months = ["ENERO","FEBRERO","MARZO","ABRIL","MAYO","JUNIO",
              "JULIO","AGOSTO","SEPTIEMBRE","OCTUBRE","NOVIEMBRE","DICIEMBRE"]
    return f"{months[d.month-1]} {d.year}"


def genre_icon(genre: str) -> str:
    return {
        "Teatro": "🎭", "Musical": "🎼", "Cine": "🎬",
        "Monólogo": "🎤", "Concierto": "🎵", "Danza": "💃"
    }.get(genre, "🎟️")


def venue_icon(venue: str) -> str:
    return {
        "Cines Victoria": "🎬",
        "Palacio de Congresos": "📍",
        "Teatro María Luisa": "🎭",
        "Teatro Romano": "🏛️",
        "Centro Cultural Alcazaba": "📍",
        "Cineclub Fórum": "🎬",
        "Sala Trajano": "🎭",
    }.get(venue, "📍")


def venue_display_name(venue: str) -> str:
    return {
        "Cines Victoria": "Cines Victoria",
        "Palacio de Congresos": "Palacio de Congresos",
        "Teatro María Luisa": "Teatro María Luisa",
        "Teatro Romano": "Teatro Romano",
        "Sala Trajano": "Sala Trajano",
        "Centro Cultural Alcazaba": "Centro Cultural Alcazaba",
        "Cineclub Fórum": "Cineclub Fórum",
    }.get(venue, venue)


def venue_sort_key(venue: str):
    return (VENUE_ORDER.get(venue, 6), norm(venue))


def event_group_key(ev: Event):
    return (canonical_venue(ev.location, ev.organizer), norm(ev.title))


def display_genre(ev: Event) -> str:
    """Categoría compacta y específica para Telegram."""
    text = norm(f"{ev.title} {ev.description} {ev.category}")
    base = ev.genre or "Teatro"

    if base == "Cine":
        return "Cine"

    if base == "Concierto":
        if any(k in text for k in ("orquesta", "piano", "pianista", "sinfónica", "sinfonica")):
            return "Concierto-Orquesta"
        if "jazz" in text:
            return "Concierto-Jazz"
        if "flamenco" in text:
            return "Concierto-Flamenco"
        return "Concierto"

    if base == "Monólogo":
        return "Monólogo"

    if base == "Musical":
        if any(k in text for k in ("familiar", "familia", "infantil", "niños", "ninos")):
            return "Musical-Familiar"
        if any(k in text for k in ("comedia", "humor")):
            return "Musical-Comedia"
        return "Musical"

    if base == "Danza":
        if "flamenco" in text:
            return "Danza-Flamenco"
        if "ballet" in text:
            return "Danza-Ballet"
        return "Danza"

    if base == "Teatro":
        if any(k in text for k in ("monólogo", "monologo", "stand-up", "stand up", "humor")):
            return "Monólogo"
        if any(k in text for k in ("familiar", "familia", "infantil", "niños", "ninos")):
            return "Teatro-Familiar"
        if any(k in text for k in ("comedia", "humor")):
            return "Teatro-Comedia"
        if any(k in text for k in ("thriller", "misterio", "suspense")):
            return "Teatro-Thriller"
        if "circo" in text:
            return "Circo"
        return "Teatro"

    return base


def compact_event_line(ev: Event) -> str:
    venue = canonical_venue(ev.location, ev.organizer)
    when = fmt_date_es(ev.start)
    if ev.time:
        when += f" · {html.escape(ev.time)}"
    target = ev.ticket_url or ev.url or VENUE_TICKET_LINKS.get(venue, "")
    link = f' <a href="{html.escape(target, quote=True)}">🔗 Entradas / info</a>' if target else ""
    return (f"• <b>{html.escape(ev.title)}</b>\n"
            f"    {html.escape(display_genre(ev))}\n"
            f"    {when}\n"
            f"    {link.strip()}")


def render_cinema_group(group: list[Event]) -> str:
    first = sorted(group, key=lambda e: (e.start, e.time))[0]
    if getattr(first, "cinema_info_only", False):
        block = f"• <b>{html.escape(first.title)}</b>"
        target = first.ticket_url or first.url
        if target:
            block += f'\n    <a href="{html.escape(target, quote=True)}">🔗 Entradas / info</a>'
        return block

    block = f"• <b>{html.escape(first.title)}</b>"
    icon = first.tags[0] if first.tags else _cinema_icon(first.title)
    genre = _cinema_genre(first.title, first.description)
    block += f"\n    {icon} {html.escape(genre)}"

    schedule = {}
    for ev in group:
        for day, times in getattr(ev, "cinema_sessions_by_date", {}).items():
            schedule.setdefault(day, set()).update(times)

    # Compatibilidad con eventos antiguos que solo tengan session_times.
    if not schedule and first.session_times:
        schedule[TODAY.isoformat()] = set(first.session_times)

    def sort_times(values):
        return sorted(values, key=lambda x: (int(x[:2]), int(x[3:])))

    # Primero, siempre las sesiones del día en que se ejecuta el repositorio.
    today_key = TODAY.isoformat()
    today_times = sort_times(schedule.get(today_key, set()))
    if today_times:
        block += (
            f"\n    📍 <b>HOY {fmt_date_es(TODAY)}:</b> "
            + ", ".join(html.escape(t) for t in today_times)
        )

    # Después, la cartelera de los próximos días de la semana.
    future_lines = []
    for day_key in sorted(schedule):
        if day_key == today_key:
            continue
        d = date.fromisoformat(day_key)
        times = sort_times(schedule[day_key])
        if times:
            future_lines.append(
                f"{fmt_date_es(d)}: " + ", ".join(html.escape(t) for t in times)
            )
    if future_lines:
        block += "\n    📅 " + " · ".join(future_lines)

    movie_url = next((e.cinema_movie_url for e in group if e.cinema_movie_url), "")
    target = movie_url or next((e.ticket_url for e in group if e.ticket_url), "") or VENUE_TICKET_LINKS.get("Cines Victoria", "")
    if target:
        label = "🔗 Película / horarios" if movie_url else "🔗 Entradas / info"
        block += f'\n    <a href="{html.escape(target, quote=True)}">{label}</a>'
    return block

def render_event_group(group: list[Event]) -> str:
    """Título + género + sesiones + enlace; sin descripciones."""
    first = sorted(group, key=lambda e: (e.start, e.time, norm(e.title)))[0]
    if first.genre == "Cine":
        return render_cinema_group(group)

    block = f"• <b>{html.escape(first.title)}</b>\n    {html.escape(display_genre(first))}"
    seen = set()
    for ev in sorted(group, key=lambda e: (e.start, e.time, norm(e.title))):
        key = (ev.start, ev.time)
        if key in seen:
            continue
        seen.add(key)
        when = fmt_date_es(ev.start)
        if ev.time:
            when += f" · {html.escape(ev.time)}"
        block += f"\n    {when}"

    target = ""
    for ev in group:
        target = ev.ticket_url or ev.url
        if ev.ticket_url:
            break
    if not target:
        target = VENUE_TICKET_LINKS.get(canonical_venue(first.location, first.organizer), "")
    if target:
        block += f'\n    <a href="{html.escape(target, quote=True)}">🔗 Entradas / info</a>'
    return block


def render_venue_block(venue: str, groups: list[list[Event]]) -> str:
    target = VENUE_TICKET_LINKS.get(venue, "")
    link = f' <a href="{html.escape(target, quote=True)}">Entradas / info</a>' if target else ""
    display = venue_display_name(venue)
    header = (f"━━━━━━━━━━━━━━━━━━━━\n"
              f"{venue_icon(venue)} <b>{html.escape(display)}</b>{link}\n"
              f"━━━━━━━━━━━━━━━━━━━━")

    extra = ""
    if venue == "Cines Victoria":
        # Información general del cine una sola vez en la cabecera.
        sample = next((e for g in groups for e in g if not e.cinema_info_only), None)
        if sample:
            general = sample.cinema_general_price or "precio no publicado en la web"
            day = sample.cinema_spectator_day or "miércoles"
            spectator = sample.cinema_spectator_price or "precio no publicado en la web"
            extra = (
                f"\n💶 General: {html.escape(general)}"
                f" · 🟢 Día del espectador: {html.escape(day)} · {html.escape(spectator)}"
            )
            if sample.cinema_promotion:
                extra += f"\n    🎟️ Promoción publicada: {html.escape(sample.cinema_promotion)}"
    body = "\n\n".join(render_event_group(g) for g in groups)
    return header + extra + "\n" + body

def build_venue_blocks(events: list[Event]) -> list[str]:
    venues = {}
    for ev in events:
        venue = canonical_venue(ev.location, ev.organizer)
        venues.setdefault(venue, {}).setdefault(norm(ev.title), []).append(ev)

    blocks = []
    for venue in sorted(venues, key=venue_sort_key):
        groups = list(venues[venue].values())
        groups.sort(key=lambda g: min((e.start, e.time, norm(e.title)) for e in g))
        blocks.append(render_venue_block(venue, groups))
    return blocks


def split_blocks_into_messages(blocks: list[str], max_chars: int, max_total: int) -> list[str]:
    """Divide sin partir eventos; si un recinto continúa, repite su encabezado."""
    hard_cap = min(max_chars, TELEGRAM_HARD_LIMIT - SAFETY_MARGIN)
    prefix = "🎭 <b>CARTELERA DE MÉRIDA · PRÓXIMOS 3 MESES</b>\n\n"

    def venue_parts(block: str):
        paragraphs = [p for p in block.split("\n\n") if p.strip()]
        if not paragraphs:
            return []
        header = paragraphs[0]
        return header, paragraphs[1:]

    def pack(source_blocks: list[str]) -> list[str]:
        messages = []
        current = prefix
        current_venue = None
        for block in source_blocks:
            parsed = venue_parts(block)
            if not parsed:
                continue
            header, events = parsed
            venue_key = header
            for event in events:
                # Cada recinto lleva su encabezado antes de su primer evento.
                # Si el recinto continúa en otro mensaje, el encabezado se repite.
                venue_intro = header + "\n\n" if current_venue != venue_key else ""
                separator = "\n\n" if current != prefix else ""
                candidate_event = venue_intro + event
                candidate = current + separator + candidate_event
                if len(candidate) <= hard_cap:
                    current = candidate
                    current_venue = venue_key
                    continue

                if current != prefix:
                    messages.append(current.rstrip())
                # El nuevo mensaje empieza siempre con el encabezado del recinto.
                candidate = prefix + header + "\n\n" + event
                if len(candidate) <= hard_cap:
                    current = candidate
                    current_venue = venue_key
                else:
                    # Los eventos normales deben caber. Como salvaguarda, no
                    # los dividimos por líneas: conservamos título/género/fecha/link.
                    current = prefix + header + "\n\n" + event[:hard_cap - len(prefix + header) - 2]
                    current_venue = venue_key
        if current != prefix:
            messages.append(current.rstrip())
        return messages

    messages = pack(blocks)
    if len(messages) <= max_total:
        return messages

    # Compactar encabezados generales no aporta información útil; mantenemos
    # los mismos eventos y enlaces y repartimos de nuevo.
    return messages[:max_total]


def build_messages(events: list[Event], config: dict) -> list[str]:
    max_chars = int(config.get("max_chars", 3800))
    max_total = int(config.get("max_mensajes_totales", 4))
    blocks = build_venue_blocks(events)

    if not blocks:
        return [(
            f"🎭 <b>CARTELERA DE MÉRIDA · PRÓXIMOS 3 MESES</b>\n\n"
            f"No hay espectáculos confirmados que cumplan los filtros entre "
            f"{TODAY.strftime('%d/%m/%Y')} y {END_DATE.strftime('%d/%m/%Y')}."
        )]
    return split_blocks_into_messages(blocks, max_chars, max_total)

TELEGRAM_STATE_FILE = ".telegram_state.json"

def telegram_api(token: str, method: str, payload: dict, max_retries: int = 3):
    url = f"https://api.telegram.org/bot{token}/{method}"
    for _ in range(1, max_retries + 1):
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data.get("ok"):
                return data
            raise RuntimeError(f"Telegram {method}: {data}")
        if r.status_code == 429:
            try:
                retry_after = r.json().get("parameters", {}).get("retry_after", 3)
            except Exception:
                retry_after = 3
            time.sleep(retry_after + 1)
            continue
        raise RuntimeError(f"Telegram HTTP {r.status_code} ({method}): {r.text}")
    raise RuntimeError(f"Telegram: {method} no se pudo completar tras varios reintentos")

def telegram_delete(token: str, chat_id: str, message_id: int):
    return telegram_api(token, "deleteMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
    })

def telegram_send(token: str, chat_id: str, text: str, max_retries: int = 3):
    return telegram_api(token, "sendMessage", {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, max_retries=max_retries)

def load_telegram_state() -> dict:
    path = Path(TELEGRAM_STATE_FILE)
    if not path.exists():
        return {"chats": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"chats": {}}
    except Exception as exc:
        logging.warning("No se pudo leer %s: %s", TELEGRAM_STATE_FILE, exc)
        return {"chats": {}}

def save_telegram_state(state: dict):
    Path(TELEGRAM_STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

def delete_previous_messages(token: str, chat_id: str, state: dict):
    chat_state = state.get("chats", {}).get(str(chat_id), {})
    previous_ids = chat_state.get("message_ids", []) if isinstance(chat_state, dict) else []
    if not previous_ids:
        logging.info("Telegram %s: no hay mensajes anteriores registrados.", chat_id)
        return
    for message_id in previous_ids:
        try:
            telegram_delete(token, chat_id, int(message_id))
            logging.info("Telegram %s: eliminado mensaje anterior %s", chat_id, message_id)
        except Exception as exc:
            # No abortamos la publicación: Telegram puede rechazar el borrado por
            # antigüedad/permisos. El nuevo bloque debe publicarse igualmente.
            logging.warning(
                "Telegram %s: no se pudo borrar mensaje anterior %s: %s",
                chat_id, message_id, exc
            )

def main():
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    ids = os.getenv("TELEGRAM_CHAT_IDS", "").strip() or os.getenv("TELEGRAM_CHAT_ID", "").strip()
    chat_ids = [x.strip() for x in ids.split(",") if x.strip()]
    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN")
    if not chat_ids:
        raise RuntimeError("Falta TELEGRAM_CHAT_IDS o TELEGRAM_CHAT_ID")

    events = collect_events()
    logging.info("Cartelera final: %d eventos | ventana %s -> %s", len(events), TODAY, END_DATE)
    messages = build_messages(events, config)
    logging.info("Mensajes a enviar: %d", len(messages))

    state = load_telegram_state()
    state.setdefault("chats", {})
    failures = []

    for chat_id in chat_ids:
        # Cada ejecución intenta eliminar primero TODOS los mensajes de la
        # ejecución anterior que quedaron registrados. Después publica de nuevo
        # para que la cartelera vuelva a aparecer al final del chat.
        delete_previous_messages(token, chat_id, state)

        new_message_ids = []
        for i, msg in enumerate(messages, 1):
            try:
                result = telegram_send(token, chat_id, msg)
                message_id = result.get("result", {}).get("message_id")
                if message_id is not None:
                    new_message_ids.append(int(message_id))
                else:
                    raise RuntimeError("Telegram no devolvió message_id")
            except Exception as exc:
                failures.append(f"{chat_id} msg {i}: {exc}")
            if i < len(messages):
                time.sleep(1.2)

        # Guardamos los IDs que sí se han publicado. Si hubo un fallo parcial,
        # conservar estos IDs permite intentar limpiarlos en la próxima ejecución.
        state["chats"][str(chat_id)] = {
            "message_ids": new_message_ids,
            "updated": TODAY.isoformat(),
        }

    save_telegram_state(state)

    if failures:
        raise RuntimeError(" | ".join(failures))
    print("OK: cartelera enviada correctamente.")

if __name__ == "__main__":
    main()
