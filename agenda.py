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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse

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

def _merida_is_real_event_link(href: str, text: str) -> bool:
    if "?" in href or "merida.es/agenda/" not in href:
        return False
    path = urlparse(href).path.strip("/")
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        # Solo "/agenda" o "/agenda/": es la portada del listado, no un evento.
        return False
    if any(norm(seg) in _MERIDA_NAV_PATH_BLACKLIST for seg in segments):
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
            "article h1 a[href], article h2 a[href], article h3 a[href], article h4 a[href], "
            ".tribe-events-calendar-list__event-title a[href], .tribe-event-title a[href]"
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
    """Agenda específica de Mérida de Palcongrex.

    La tabla general mezcla Mérida, Badajoz, Cáceres y Plasencia; la versión
    anterior podía descartar eventos de Mérida porque guardaba el recinto como
    ciudad. Aquí usamos la agenda /merida/ y filtramos explícitamente Mérida.
    """
    base = "https://www.palcongrex.es/merida/agenda/tabla"
    events = []
    seen = set()
    for page in range(0, 8):
        url = base if page == 0 else f"{base}?page={page}"
        soup = fetch(url)
        if not soup:
            break
        rows = soup.select("tr")
        page_found = 0
        for row in rows:
            cells = [clean_text(x.get_text(" ", strip=True), 300) for x in row.select("td, th")]
            if len(cells) < 4:
                continue
            # Tabla Mérida: Título | Palacio de congresos | Tipo | Fecha inicio
            title, palace, typ, date_text = cells[:4]
            if not title or norm(title) in {"título", "titulo"}:
                continue
            if norm(palace) not in {"mérida", "merida", "palacio de congresos"}:
                continue
            if not re.search(r"\d{1,2}\s+[A-Za-zÁÉÍÓÚáéíóúñÑ]+\s+\d{4}", date_text):
                continue
            st, en = parse_date(date_text)
            if not st or not (TODAY <= st <= END_DATE):
                continue
            key = (norm(title), st.isoformat())
            if key in seen:
                continue
            seen.add(key)
            href = ""
            # Enlace específico de la ficha si la tabla lo proporciona.
            a = row.find("a", href=True)
            if a:
                href = urljoin(url, a.get("href", ""))
            ev = make_event(title, st, en, "", "Palacio de Congresos", "", href or url,
                            "Palcongrex", city="Mérida", category=typ)
            # Palcongrex usa "Espectáculos" como categoría paraguas para teatro,
            # humor, magia, circo y grandes shows. Si el título no contiene una
            # palabra suficiente para clasificarlo, sigue siendo un espectáculo
            # escénico válido para esta cartelera.
            if not ev.genre and norm(typ) == "espectáculos":
                ev.genre = "Teatro"
            if ev.genre:
                events.append(ev)
                page_found += 1
        if page_found == 0 and page > 0:
            break
    return events


# ---------------------------------------------------------------------------
# FILTRO ESTRICTO DE CARTELERA
# ---------------------------------------------------------------------------
EXCLUDE_KW = [
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
            if old.title.isupper() and not ev.title.isupper():
                old.title = ev.title
            # Conservamos el enlace de la fuente oficial más directa.
            if ev.source in {"Ayuntamiento de Mérida", "Palcongrex", "Teatro María Luisa", "Cines Victoria"}:
                old.source, old.url = ev.source, ev.url
    return list(result.values())

def scrape_teatro_maria_luisa() -> list[Event]:
    """Programa oficial completo, recorriendo las páginas de programación.

    No usamos enlaces de /events/categories/ como eventos. Las fichas reales
    /events/<slug>/ son las únicas que entran en la cartelera.
    """
    base = "https://www.teatromarialuisa.org/"
    links = {}
    for page in range(0, 8):
        url = base if page == 0 else f"{base}?pno={page+1}"
        soup = fetch(url)
        if not soup:
            break
        before = len(links)
        for a in soup.select('a[href*="/events/"]'):
            href = urljoin(base, a.get("href", ""))
            title = clean_text(a.get_text(" ", strip=True), 180)
            path = urlparse(href).path.rstrip("/") + "/"
            if not href or "/events/categories/" in href or len(title) < 4:
                continue
            if path.endswith("/events/"):
                continue
            links[href] = title
        if len(links) == before and page > 1:
            break

    events = []
    for href, fallback_title in links.items():
        detail = fetch(href)
        if not detail:
            continue
        title_node = detail.select_one("h1")
        title = clean_text(title_node.get_text(" ", strip=True), 180) if title_node else fallback_title
        if not title or norm(title) in _MERIDA_NAV_TEXT_BLACKLIST:
            continue
        page_text = clean_text(detail.get_text(" ", strip=True), 9000)
        st, en = parse_date(page_text)
        if not st or not (TODAY <= st <= END_DATE):
            continue
        tm = extract_time(page_text)
        category = ""
        for a in detail.select('a[href*="/events/categories/"]'):
            txt = clean_text(a.get_text(" ", strip=True), 80)
            if txt and norm(txt) not in _MERIDA_NAV_TEXT_BLACKLIST:
                category = txt
                break
        paragraphs = []
        for node in detail.select("main p, article p, .entry-content p, .elementor-widget-text-editor p"):
            txt = clean_text(node.get_text(" ", strip=True), 700)
            low = norm(txt)
            if len(txt) >= 45 and not any(x in low for x in ["comprar entradas", "taquilla y descuentos", "más información"]):
                paragraphs.append(txt)
        desc = " ".join(paragraphs[:2])
        ev = make_event(title, st, en, tm, "Teatro María Luisa", desc, href,
                        "Teatro María Luisa", category=category, city="Mérida")
        # Clasificación basada también en el texto real de la ficha.
        if not ev.genre:
            cat = norm(category + " " + title + " " + page_text[:1200])
            if any(k in cat for k in ["monólogo", "monologo", "humor", "stand-up"]):
                ev.genre = "Monólogo"
            elif any(k in cat for k in ["música", "musica", "concierto", "piano", "ópera", "opera"]):
                ev.genre = "Concierto"
            elif "danza" in cat or "flamenco" in cat or "ballet" in cat:
                ev.genre = "Danza"
            elif "musical" in cat:
                ev.genre = "Musical"
            elif any(k in cat for k in ["teatro", "comedia", "drama", "circo"]):
                ev.genre = "Teatro"
            elif "cine" in cat:
                ev.genre = "Cine"
        if ev.genre:
            ev.session_times = [tm] if tm else []
            events.append(ev)
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

        # Solo se muestran las sesiones del viernes de la ejecución.
        friday_times = by_date.get(FRIDAY_DATE, [])
        if not friday_times:
            continue
        icon = _cinema_icon(txt)
        ev = make_event(title, FRIDAY_DATE, FRIDAY_DATE, friday_times[0],
                        "Cines Victoria", txt, ticket_url or movie_url or url,
                        "Cines Victoria", category="Cine", city="Mérida")
        ev.genre = "Cine"
        ev.session_times = friday_times
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
        low = norm(title)
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


def scrape_cineclub_merida() -> list[Event]:
    """Fuente complementaria del Cine Club Fórum. Se usa como fuente de
    confirmación/enlace, no para inventar fechas que no estén publicadas."""
    url = "https://festivalcinemerida.com/cineclub/"
    soup = fetch(url)
    if not soup:
        return []
    events = []
    for node in soup.select("article, li, .event, .evento, .card, .elementor-widget-container"):
        txt = clean_text(node.get_text(" ", strip=True), 1200)
        if len(txt) < 20:
            continue
        st, en = parse_date(txt)
        if not st or not (TODAY <= st <= END_DATE):
            continue
        a = node.find("a", href=True)
        title = clean_text(a.get_text(" ", strip=True), 180) if a else ""
        if not title or len(title) < 3:
            continue
        ev = make_event(title, st, en, extract_time(txt), "Cineclub Fórum",
                        txt, urljoin(url, a.get("href")) if a else url,
                        "Cine Club Fórum", category="Cine", city="Mérida")
        ev.genre = "Cine"
        events.append(ev)
    return events

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


def venue_sort_key(venue: str):
    return (VENUE_ORDER.get(venue, 6), norm(venue))


def event_group_key(ev: Event):
    return (canonical_venue(ev.location, ev.organizer), norm(ev.title))


def compact_event_line(ev: Event) -> str:
    """Compatibilidad con tests/consumidores antiguos; la salida nueva usa grupos."""
    venue = canonical_venue(ev.location, ev.organizer)
    when = fmt_date_es(ev.start)
    if ev.time:
        when += f" · {html.escape(ev.time)}"
    target = ev.ticket_url or ev.url or VENUE_TICKET_LINKS.get(venue, "")
    link = f' <a href="{html.escape(target, quote=True)}">🎟️ Entradas / info</a>' if target else ""
    return (f"• {genre_icon(ev.genre)} <b>{html.escape(ev.title)}</b> · {when}\n"
            f"  {html.escape(ev.genre)} · 📍 {html.escape(venue)}{link}")


def render_cinema_group(group: list[Event]) -> str:
    first = sorted(group, key=lambda e: (e.start, e.time))[0]
    if getattr(first, "cinema_info_only", False):
        block = f"• 🎬 <b>{html.escape(first.title)}</b>"
        target = first.ticket_url or first.url
        if target:
            block += f'\n    <a href="{html.escape(target, quote=True)}">🔗 Entradas / info</a>'
        return block

    icon = first.tags[0] if first.tags else "🎬"
    block = f"• {icon} <b>{html.escape(first.title)}</b>"
    times = []
    for ev in group:
        times.extend(ev.session_times or ([ev.time] if ev.time else []))
    times = sorted(set(times), key=lambda x: (int(x[:2]), int(x[3:])))
    if times:
        block += f"\n    {fmt_date_es(FRIDAY_DATE)} · " + ", ".join(html.escape(t) for t in times)

    movie_url = next((e.cinema_movie_url for e in group if e.cinema_movie_url), "")
    target = movie_url or next((e.ticket_url for e in group if e.ticket_url), "") or VENUE_TICKET_LINKS.get("Cines Victoria", "")
    if target:
        label = "🔗 Película / horarios" if movie_url else "🔗 Entradas / info"
        block += f'\n    <a href="{html.escape(target, quote=True)}">{label}</a>'
    return block

def render_event_group(group: list[Event]) -> str:
    """Renderiza un título/producción una sola vez y agrupa todas sus sesiones."""
    first = sorted(group, key=lambda e: (e.start, e.time, norm(e.title)))[0]
    title = html.escape(first.title)
    genre = first.genre

    sessions = []
    seen = set()
    for ev in sorted(group, key=lambda e: (e.start, e.time, norm(e.title))):
        key = (ev.start, ev.time)
        if key in seen:
            continue
        seen.add(key)
        when = fmt_date_es(ev.start)
        if ev.time:
            when += f" · {html.escape(ev.time)}"
        sessions.append(f"    {when}")

    if genre == "Cine":
        return render_cinema_group(group)

    block = f"• {genre_icon(genre)} <b>{title}</b>\n" + "\n".join(sessions)

    # Para el resto añadimos una descripción breve cuando existe.
    if genre != "Cine":
        desc = ""
        for ev in group:
            if ev.description and len(ev.description.strip()) > len(desc):
                desc = ev.description.strip()
        if desc:
            desc = clean_text(desc, 280)
            block += f"\n    {genre_icon(genre)} {html.escape(desc)}"

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
    header = f"{venue_icon(venue)} <b>{html.escape(venue)}</b>{link}"

    if venue == "Cines Victoria":
        general = next((e.cinema_general_price for g in groups for e in g if e.cinema_general_price), "")
        spectator_day = next((e.cinema_spectator_day for g in groups for e in g if e.cinema_spectator_day), "miércoles")
        spectator = next((e.cinema_spectator_price for g in groups for e in g if e.cinema_spectator_price), "")
        promotion = next((e.cinema_promotion for g in groups for e in g if e.cinema_promotion), "")
        info = f"💶 General: {html.escape(general) if general else 'precio no publicado en la web'}"
        info += f" · 🟢 Día del espectador: {html.escape(spectator_day) if spectator_day else 'no publicado'} · "
        info += f"{html.escape(spectator) if spectator else 'precio no publicado en la web'}"
        if promotion:
            info += f"\n    🎟️ Promoción publicada: {html.escape(promotion)}"
        body = "\n\n".join(render_event_group(g) for g in groups)
        return header + "\n" + info + "\n\n" + body

    body = "\n\n".join(render_event_group(g) for g in groups)
    return header + "\n" + body

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
    """Divide en orden estricto, permitiendo cortar un recinto entre mensajes."""
    hard_cap = min(max_chars, TELEGRAM_HARD_LIMIT - SAFETY_MARGIN)
    prefix = "🎭 <b>CARTELERA DE MÉRIDA · PRÓXIMOS 3 MESES</b>\n\n"

    def pack(parts: list[str]) -> list[str]:
        messages = []
        current = prefix
        for part in parts:
            # Un bloque de evento/recinto puede partirse sin cambiar su orden.
            paragraphs = part.split("\n\n")
            for paragraph in paragraphs:
                candidate = current + ("\n\n" if current != prefix else "") + paragraph
                if len(candidate) <= hard_cap:
                    current = candidate
                    continue
                if current != prefix:
                    messages.append(current.rstrip())
                    current = prefix
                # Si un párrafo individual es demasiado largo, se divide por líneas.
                for line in paragraph.splitlines():
                    candidate = current + ("\n" if current != prefix else "") + line
                    if len(candidate) <= hard_cap:
                        current = candidate
                    else:
                        if current != prefix:
                            messages.append(current.rstrip())
                        current = prefix + line
        if current != prefix:
            messages.append(current.rstrip())
        return messages

    messages = pack(blocks)
    if len(messages) <= max_total:
        return messages

    # Modo compacto: conserva exactamente el mismo orden, títulos, sesiones y
    # enlaces, pero elimina descripciones para que el límite de cuatro mensajes
    # no obligue a eliminar eventos.
    compact_blocks = []
    for block in blocks:
        lines = block.splitlines()
        compact_lines = []
        skip_description = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("🎭 ", "🎼 ", "🎤 ", "🎵 ", "💃 ")) and not stripped.startswith("🎭 <b>CARTELERA"):
                skip_description = True
                continue
            if skip_description and stripped.startswith("<a href="):
                skip_description = False
            if skip_description:
                continue
            compact_lines.append(line)
        compact_blocks.append("\n".join(compact_lines))

    messages = pack(compact_blocks)
    if len(messages) <= max_total:
        return messages

    # Último recurso: conservar el contenido completo y repartirlo secuencialmente
    # entre cuatro mensajes por líneas. Nunca se reordenan recintos/eventos.
    all_lines = []
    for block in compact_blocks:
        all_lines.extend(block.splitlines())
        all_lines.append("")
    buckets = [prefix.rstrip() for _ in range(max_total)]
    idx = 0
    for line in all_lines:
        addition = ("\n" if buckets[idx] else "") + line
        if len(buckets[idx]) + len(addition) > hard_cap and idx < max_total - 1:
            idx += 1
        addition = ("\n" if buckets[idx] else "") + line
        buckets[idx] += addition
    return [b.rstrip() for b in buckets if b.strip()]


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

def telegram_send(token: str, chat_id: str, text: str, max_retries: int = 3):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(1, max_retries + 1):
        r = requests.post(url, json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            try:
                retry_after = r.json().get("parameters", {}).get("retry_after", 3)
            except Exception:
                retry_after = 3
            time.sleep(retry_after + 1)
            continue
        raise RuntimeError(f"Telegram HTTP {r.status_code}: {r.text}")
    raise RuntimeError("Telegram: no se pudo enviar tras varios reintentos")

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

    failures = []
    for chat_id in chat_ids:
        for i, msg in enumerate(messages, 1):
            try:
                telegram_send(token, chat_id, msg)
            except Exception as exc:
                failures.append(f"{chat_id} msg {i}: {exc}")
            if i < len(messages):
                time.sleep(1.2)
    if failures:
        raise RuntimeError(" | ".join(failures))
    print("OK: cartelera enviada correctamente.")

if __name__ == "__main__":
    main()
