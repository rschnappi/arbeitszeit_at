"""Tests der Berechnungslogik mit synthetischen Beispieldaten."""
import importlib.util
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

_p = pathlib.Path(__file__).parents[1] / "custom_components/arbeitszeit_at/calc.py"
_spec = importlib.util.spec_from_file_location("calc", _p)
calc = importlib.util.module_from_spec(_spec)
sys.modules["calc"] = calc
_spec.loader.exec_module(calc)

TZ = timezone(timedelta(hours=2))


def w(day, s, e, summary="Arbeit"):
    return {"start": f"2026-09-{day:02d}T{s}:00+02:00", "end": f"2026-09-{day:02d}T{e}:00+02:00", "summary": summary}


# Beispielmonat September 2026: 9 h pro Tag, Urlaub 21.–23. und 28.–29.
EVENTS = []
for d in (1, 2, 3, 4, 7, 8, 9, 10, 11, 14, 15, 16, 17, 18):
    EVENTS += [w(d, "07:00", "12:00"), w(d, "12:30", "16:30")]
EVENTS += [
    w(1, "09:00", "10:00", "Meeting"),  # überlappt -> darf nicht doppelt zählen
    {"start": "2026-09-21", "end": "2026-09-24", "summary": "Urlaub"},
    w(24, "07:00", "12:00"), w(24, "12:30", "17:00"),
    w(25, "07:00", "12:00"), w(25, "12:30", "15:30"),
    {"start": "2026-09-28", "end": "2026-09-30", "summary": "Urlaub"},
    {"start": "2026-09-30", "end": "2026-10-01", "summary": "Zahnarzt"},  # kein Keyword -> ignoriert
    {"start": "2026-04-27", "end": "2026-04-29", "summary": "Pflegeurlaub"},
    {"start": "2026-02-20", "end": "2026-02-21", "summary": "Zeitausgleich"},
]
NOW = datetime(2026, 9, 25, 20, 53, tzinfo=TZ)
CFG = calc.CalcConfig(start_balance=10, leave_balance=25, leave_balance_date=date(2026, 9, 25),
                      payouts="Mai: 20h, August 16 Stunden")
R = calc.calculate(EVENTS, CFG, NOW)


def test_week():
    a = R["uberstunden_woche"]
    assert a["attributes"]["ist_gearbeitet"] == "17.50"
    assert a["attributes"]["urlaub_tage"] == 3
    assert a["attributes"]["kalenderwoche"] == "KW 39"
    assert a["state"] == 1.5


def test_month():
    at = R["uberstunden_monat"]["attributes"]
    assert at["ist_gearbeitet"] == "143.50"
    assert at["soll_stunden_gesamt"] == "176.00"
    assert at["soll_stunden_bis_heute"] == "152.00"
    assert at["urlaub_tage_gesamt"] == 5
    assert at["saldo_monatsende"] == "7.50"
    assert R["uberstunden_monat"]["state"] == 15.5


def test_running_event_counts_until_now():
    now = datetime(2026, 9, 25, 10, 0, tzinfo=TZ)
    r = calc.calculate(EVENTS, CFG, now)
    assert r["uberstunden_woche"]["attributes"]["heute_gearbeitet"] == "3.00"


def test_holidays_2026():
    soll = {1: 160, 2: 160, 3: 176, 4: 168, 5: 144, 6: 168, 7: 184, 8: 168, 9: 176}
    c = calc.Calculator([], calc.CalcConfig(), NOW)
    for m, exp in soll.items():
        s = date(2026, m, 1)
        e = (s + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        assert c.period(s, e)["soll"] == exp, m


def test_holiday_calendar_filter():
    evs = [
        {"start": "2026-10-26", "end": "2026-10-27", "summary": "Nationalfeiertag", "description": "Gesetzlicher Feiertag"},
        {"start": "2026-10-25", "end": "2026-10-26", "summary": "Ende der Sommerzeit", "description": "Gedenktag"},
        {"start": "2026-11-02", "end": "2026-11-03", "summary": "Allerseelen", "description": "Gedenktag"},
        {"start": "2026-11-03T10:00:00+01:00", "end": "2026-11-03T11:00:00+01:00", "summary": "Termin"},
    ]
    hol = calc.holidays_from_events(evs, "Gesetzlicher Feiertag")
    assert hol == {date(2026, 10, 26): "Nationalfeiertag"}
    assert len(calc.holidays_from_events(evs, "")) == 3


def test_holiday_calendar_only():
    cfg = calc.CalcConfig(builtin_holidays=False, extra_holidays={date(2026, 11, 2): "Betriebsfrei"})
    c = calc.Calculator([], cfg, NOW)
    assert c.soll(date(2026, 11, 2)) == 0      # aus Kalender
    assert c.soll(date(2026, 10, 26)) == 8     # eingebaut deaktiviert
    c2 = calc.Calculator([], calc.CalcConfig(extra_holidays={date(2026, 11, 2): "Betriebsfrei"}), NOW)
    assert c2.soll(date(2026, 10, 26)) == 0 and c2.soll(date(2026, 11, 2)) == 0  # kombiniert


def test_keywords():
    c = calc.CalcConfig()
    assert c.classify("Pflegeurlaub") == "pflege"
    assert c.classify("Urlaub Kroatien") == "urlaub"
    assert c.classify("ZA") == "za"
    assert c.classify("Zahnarzt") is None
    assert c.classify("Arbeit") is None


def test_payouts():
    assert calc.parse_payouts("Mai: 20h, August 16 Stunden") == [(5, 20.0), (8, 16.0)]
    assert calc.parse_payouts("März 7,5 h") == [(3, 7.5)]
    assert calc.parse_payouts("12") == [(None, 12.0)]
    assert R["za_ausbezahlt_jahr"]["state"] == 36.0


def test_leave_and_netto():
    assert R["urlaub_rest"]["state"] == 23  # 25 laut Stand 25.09., minus 28.+29.09.
    assert R["pflegeurlaub_jahr"]["attributes"]["verbleibend"] == 3
    j = R["uberstunden_jahr"]["attributes"]
    netto = float(j["ueberstunden_guthaben"]) + 10 - float(j["za_stunden_jahr"]) - 36
    assert R["uberstunden_saldo_netto"]["state"] == round(netto, 2)


# Synthetisches ICS im Google-Export-Stil (basic.ics): reines Datum für ganztägig,
# UTC-Zeit ("Z") für zeitgebundene Termine, gefaltete Zeile bei SUMMARY.
ICS_SAMPLE = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART;VALUE=DATE:20260105\r\n"
    "DTEND;VALUE=DATE:20260106\r\n"
    "SUMMARY:Urlaub\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260112T063000Z\r\n"
    "DTEND:20260112T110000Z\r\n"
    "SUMMARY:Arbeit\r\n"
    "DESCRIPTION:Lange Beschreibung die über\r\n"
    " mehrere Zeilen gefaltet ist\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260113T063000Z\r\n"
    "DTEND:20260113T110000Z\r\n"
    "SUMMARY:Termin\\, mit Komma\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_events_from_ics():
    events = calc.events_from_ics(ICS_SAMPLE)
    assert len(events) == 3
    assert events[0] == {"start": "2026-01-05", "end": "2026-01-06", "summary": "Urlaub"}
    assert events[1]["start"] == "2026-01-12T06:30:00+00:00"
    assert events[1]["end"] == "2026-01-12T11:00:00+00:00"
    assert events[1]["description"] == "Lange Beschreibung die übermehrere Zeilen gefaltet ist"
    assert events[2]["summary"] == "Termin, mit Komma"


def test_events_from_ics_feeds_into_calculate():
    events = calc.events_from_ics(ICS_SAMPLE)
    r = calc.calculate(events, calc.CalcConfig(), datetime(2026, 1, 13, 12, 0, tzinfo=TZ))
    assert r["urlaub_jahr"]["state"] == 1
