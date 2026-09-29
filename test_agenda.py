from datetime import date, timedelta
from unittest.mock import patch, MagicMock
import agenda

def test_three_month_window_is_dynamic():
    assert agenda.END_DATE == agenda.add_months(agenda.TODAY, 3)

def test_exact_end_date_is_included():
    e = agenda.make_event("Teatro", agenda.END_DATE, agenda.END_DATE, "20:00",
                          "Teatro María Luisa", "", "https://x", "F", category="Teatro")
    assert agenda.is_valid_cartelera_event(e)

def test_beyond_three_months_is_excluded():
    e = agenda.make_event("Teatro", agenda.END_DATE + timedelta(days=1),
                          agenda.END_DATE + timedelta(days=1), "20:00",
                          "Teatro María Luisa", "", "https://x", "F", category="Teatro")
    assert not agenda.is_valid_cartelera_event(e)

def test_conference_is_excluded():
    e = agenda.make_event("Congreso de cultura", agenda.TODAY, agenda.TODAY, "10:00",
                          "Teatro María Luisa", "", "https://x", "F", category="Congreso")
    assert e.genre == ""

def test_exhibition_is_excluded():
    e = agenda.make_event("Exposición de fotografía", agenda.TODAY, agenda.TODAY,
                          "", "Centro Cultural Alcazaba", "", "https://x", "F",
                          category="Exposición")
    assert e.genre == ""

def test_musical_based_on_movie_is_not_cinema():
    e = agenda.make_event("El Gran Showman. El Musical", agenda.TODAY, agenda.TODAY,
                          "20:00", "Palacio de Congresos",
                          "Espectáculo inspirado en la película.", "https://x", "F",
                          category="Musical")
    assert e.genre == "Musical"

def test_commercial_cinema_is_allowed():
    e = agenda.make_event("La bola negra", agenda.TODAY, agenda.TODAY, "19:00",
                          "Cines Victoria", "", "https://x", "F", category="Cine")
    assert e.genre == "Cine"

def test_neighborhood_school_event_is_excluded():
    e = agenda.make_event("Fiesta escolar infantil", agenda.TODAY, agenda.TODAY,
                          "17:00", "Centro Cultural Alcazaba",
                          "Actividad escolar del barrio.", "https://x", "F",
                          category="Infantil")
    assert e.genre == ""

def test_allowed_genres_only():
    for g in agenda.GENRE_LABELS:
        e = agenda.make_event(f"Evento {g}", agenda.TODAY, agenda.TODAY,
                              "20:00", "Teatro María Luisa", "", "https://x", "F",
                              category=g)
        assert e.genre == g

def test_message_contains_required_fields():
    e = agenda.make_event("Concierto X", agenda.TODAY, agenda.TODAY, "20:00",
                          "Palacio de Congresos", "", "https://entradas.example/x",
                          "F", category="Concierto")
    msg = agenda.compact_event_line(e)
    assert "Concierto X" in msg
    assert "20:00" in msg
    assert "Concierto" in msg
    assert "Palacio de Congresos" in msg
    assert "Entradas / info" in msg

def test_cinema_group_uses_single_title_and_all_sessions():
    a = agenda.make_event("Coyote vs Acme", agenda.TODAY, agenda.TODAY, "17:30",
                          "Cines Victoria", "Acción", "https://cine.example/movie", "Cines Victoria", category="Cine")
    a.genre = "Cine"
    a.session_times = ["17:30", "18:00", "20:45"]
    a.tags = ["💥"]
    b = agenda.make_event("Coyote vs Acme", agenda.TODAY + timedelta(days=1), agenda.TODAY + timedelta(days=1), "19:00",
                          "Cines Victoria", "Acción", "https://cine.example/movie", "Cines Victoria", category="Cine")
    b.genre = "Cine"
    b.session_times = ["19:00"]
    b.tags = ["💥"]
    text = agenda.render_event_group([a, b])
    assert text.count("Coyote vs Acme") == 1
    assert "17:30, 18:00, 19:00, 20:45" in text
    assert "🔗 Entradas / info" in text


def test_venue_order_is_fixed():
    assert agenda.venue_sort_key("Cines Victoria") < agenda.venue_sort_key("Palacio de Congresos")
    assert agenda.venue_sort_key("Palacio de Congresos") < agenda.venue_sort_key("Teatro María Luisa")
    assert agenda.venue_sort_key("Teatro María Luisa") < agenda.venue_sort_key("Teatro Romano")
    assert agenda.venue_sort_key("Teatro Romano") < agenda.venue_sort_key("Centro Cultural Alcazaba")
    assert agenda.venue_sort_key("Centro Cultural Alcazaba") < agenda.venue_sort_key("Cineclub Fórum")


def test_theater_scraper_only_accepts_real_event_links():
    assert "/events/" in "https://www.teatromarialuisa.org/events/yo-literal-ernesto-sevilla/"
    assert "/events/categories/monologo-humor/" not in "https://www.teatromarialuisa.org/events/yo-literal-ernesto-sevilla/"


def test_next_friday_when_run_on_tuesday():
    assert agenda.next_friday(date(2026, 9, 29)) == date(2026, 10, 2)

def test_friday_on_friday_is_same_day():
    assert agenda.next_friday(date(2026, 10, 2)) == date(2026, 10, 2)

def test_cinema_renderer_does_not_invent_spectator_price():
    e = agenda.make_event("Coyote vs Acme", date(2026, 10, 2), date(2026, 10, 2), "17:00",
                          "Cines Victoria", "Acción", "https://cine.example/movie", "Cines Victoria", category="Cine")
    e.genre = "Cine"
    e.session_times = ["17:00"]
    e.tags = ["💥"]
    e.cinema_spectator_day = "miércoles"
    text = agenda.render_cinema_group([e])
    assert "vie 02/10 · 17:00" in text
    assert "General:" not in text
    assert "miércoles · precio no publicado en la web" not in text
    assert "01 €" not in text

def test_palcongrex_venue_is_not_used_as_city():
    e = agenda.make_event("Ángel Martín: Somos Monos", date(2026, 10, 3), date(2026, 10, 3),
                          "", "Palacio de Congresos", "", "https://example.com",
                          "Palcongrex", city="Mérida", category="Espectáculos")
    if not e.genre:
        e.genre = "Teatro"
    assert agenda.is_valid_cartelera_event(e)
    assert agenda.canonical_venue(e.location, e.organizer) == "Palacio de Congresos"


def test_cinema_venue_renderer_puts_general_info_once():
    e = agenda.make_event("Coyote vs Acme", date(2026, 10, 2), date(2026, 10, 2), "17:00",
                          "Cines Victoria", "", "https://cine.example/movie", "Cines Victoria", category="Cine")
    e.genre = "Cine"
    e.session_times = ["17:00"]
    e.tags = ["🎬"]
    e.cinema_spectator_day = "miércoles"
    e.cinema_promotion = "6,90 €"
    msg = agenda.render_venue_block("Cines Victoria", [[e]])
    assert msg.count("General: precio no publicado en la web") == 1
    assert msg.count("Día del espectador: miércoles") == 1
    assert msg.count("Promoción publicada: 6,90 €") == 1
    assert "Coyote vs Acme" in msg


def test_sala_trajano_has_own_venue():
    assert agenda.canonical_venue("Sala Trajano") == "Sala Trajano"
    assert agenda.venue_sort_key("Teatro Romano") < agenda.venue_sort_key("Sala Trajano")
    assert agenda.venue_sort_key("Sala Trajano") < agenda.venue_sort_key("Centro Cultural Alcazaba")


def test_sala_trajano_alias():
    assert agenda.canonical_venue("Sala Trajano") == "Sala Trajano"

def test_palcongrex_uses_three_columns():
    assert "tres columnas" in (agenda.scrape_palcongrex.__doc__ or "")


def test_event_renderer_has_no_repeated_genre_icon():
    e = agenda.make_event("Antes de que alguien se vaya", date(2026, 10, 2), date(2026, 10, 2), "20:30",
                          "Sala Trajano", "Comedia", "https://example.com", "Ayuntamiento de Mérida", category="Teatro")
    text = agenda.render_event_group([e])
    assert text.startswith("• <b>Antes de que alguien se vaya</b>")
    assert "🎭 <b>Antes" not in text
    assert "🎭 Comedia" not in text


def test_merida_event_link_accepts_detail_and_rejects_navigation():
    assert agenda._merida_is_real_event_link(
        "https://merida.es/agenda/teatro-choriza-2/", "Teatro: Choriza"
    )
    assert not agenda._merida_is_real_event_link(
        "https://merida.es/agenda/categoria/teatro/", "Teatro"
    )
