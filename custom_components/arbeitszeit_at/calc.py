"""Arbeitszeit-/Überstundenberechnung – bewusst ohne Home-Assistant-Imports.

Eingabe sind die Events aus `calendar.get_events`:
    {"start": "2026-01-05T07:00:00+01:00" | "2026-01-05", "end": ..., "summary": "..."}

Regeln:
- Zeitgebundene Events = Arbeitszeit (außer Summary passt zu Abwesenheits-/Ignore-Keyword).
  Überlappungen werden zusammengeführt, laufende Events bis "jetzt" gezählt.
- Ganztägige Events mit Keyword (Urlaub, ZA, Pflegeurlaub, Krankenstand) werden an
  Soll-Tagen mit den Tages-Sollstunden angerechnet. Wochenenden/Feiertage zählen nicht.
- Zeitgebundene Abwesenheits-Events (z. B. "Zeitausgleich" 12:00–16:00) zählen mit ihrer Dauer.
- Soll = Sollstunden/Tag an Arbeitstagen ohne Feiertage (eingebaut AT und/oder Feiertags-Kalender).
- Brutto-Überstunden = Ist + Urlaub + ZA + Pflege + Krank − Soll
- Netto-Saldo = Brutto + Startsaldo − ZA − ausbezahlte Stunden
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo

MONTHS_DE = [
    "Jänner", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]

CAT_VACATION = "urlaub"
CAT_ZA = "za"
CAT_CARE = "pflege"
CAT_SICK = "krank"
CAT_IGNORE = "ignore"
ABSENCES = (CAT_VACATION, CAT_ZA, CAT_CARE, CAT_SICK)


# --------------------------------------------------------------------------- Feiertage
def easter_sunday(year: int) -> date:
    """Ostersonntag (anonymer gregorianischer Algorithmus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def austrian_holidays(year: int, xmas_eve_nye_free: bool = False) -> dict[date, str]:
    """Gesetzliche Feiertage Österreich (bundesweit)."""
    easter = easter_sunday(year)
    hol = {
        date(year, 1, 1): "Neujahr",
        date(year, 1, 6): "Heilige Drei Könige",
        easter + timedelta(days=1): "Ostermontag",
        date(year, 5, 1): "Staatsfeiertag",
        easter + timedelta(days=39): "Christi Himmelfahrt",
        easter + timedelta(days=50): "Pfingstmontag",
        easter + timedelta(days=60): "Fronleichnam",
        date(year, 8, 15): "Mariä Himmelfahrt",
        date(year, 10, 26): "Nationalfeiertag",
        date(year, 11, 1): "Allerheiligen",
        date(year, 12, 8): "Mariä Empfängnis",
        date(year, 12, 25): "Christtag",
        date(year, 12, 26): "Stefanitag",
    }
    if xmas_eve_nye_free:
        hol[date(year, 12, 24)] = "Heiliger Abend"
        hol[date(year, 12, 31)] = "Silvester"
    return hol


def _ics_unescape(value: str) -> str:
    return (value.replace("\\N", "\n").replace("\\n", "\n")
                 .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _ics_to_iso(value: str) -> str:
    """DTSTART/DTEND-Rohwert -> calc.py-Format ("YYYY-MM-DD" bzw. ISO-Datetime).

    Deckt nur die zwei Formen ab, die Googles ICS-Export tatsächlich liefert: reines
    Datum (`;VALUE=DATE:20260105`, ganztägig) und UTC-Zeit (`:20260112T113000Z`).
    """
    if len(value) == 8:
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    offset = "+00:00" if value.endswith("Z") else ""
    value = value.rstrip("Z")
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}T{value[9:11]}:{value[11:13]}:{value[13:15]}{offset}"


def events_from_ics(text: str) -> list[dict]:
    """Rohes ICS (z. B. Googles "geheime Adresse im iCal-Format"-Export) -> Events im
    calc.py-Format ({"start", "end", "summary", "description"}).

    Umgeht Home Assistants `calendar.get_events`: dessen lokaler Sync für Google-Kalender
    liefert nur Termine, die innerhalb eines rollierenden ~90-Tage-Fensters liegen ODER seit
    dem letzten Sync neu angelegt/geändert wurden (Googles Sync-Token-Mechanismus reicht nur
    Änderungen durch, nicht das Datum des Termins selbst) - siehe SYNC_EVENT_MIN_TIME in
    homeassistant/components/google/calendar.py. Ein seit Jahren unverändertes Kalender-Event
    kommt dort nie an, selbst wenn der Google-Account viel länger Zugriff hat. Der direkte
    ICS-Export unterliegt dieser Beschränkung nicht.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    unfolded: list[str] = []
    for line in lines:
        if line[:1] in (" ", "\t") and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)

    events: list[dict] = []
    cur: dict[str, str] | None = None
    for line in unfolded:
        if line == "BEGIN:VEVENT":
            cur = {}
            continue
        if line == "END:VEVENT":
            if cur and "start" in cur and "end" in cur:
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        key, _, value = line.partition(":")
        name = key.split(";")[0]
        if name == "DTSTART":
            cur["start"] = _ics_to_iso(value)
        elif name == "DTEND":
            cur["end"] = _ics_to_iso(value)
        elif name == "SUMMARY":
            cur["summary"] = _ics_unescape(value)
        elif name == "DESCRIPTION":
            cur["description"] = _ics_unescape(value)
    return events


def holidays_from_events(events: list[dict], filter_keywords: str = "") -> dict[date, str]:
    """Ganztägige Events eines Feiertagskalenders -> {Datum: Name}.

    filter_keywords: kommagetrennt; mindestens eines muss in Summary oder Beschreibung
    vorkommen (z. B. "Gesetzlicher Feiertag", um Gedenktage auszuschließen). Leer = alle.
    """
    kws = [k.strip().lower() for k in (filter_keywords or "").split(",") if k.strip()]
    out: dict[date, str] = {}
    for ev in events:
        start, end = ev.get("start", ""), ev.get("end", "")
        if len(start) != 10:
            continue  # nur ganztägige Termine
        text = f"{ev.get('summary') or ''} {ev.get('description') or ''}".lower()
        if kws and not any(k in text for k in kws):
            continue
        d = date.fromisoformat(start)
        e = date.fromisoformat(end) if len(end) == 10 else d + timedelta(days=1)
        while d < e:
            out.setdefault(d, ev.get("summary") or "Feiertag")
            d += timedelta(days=1)
    return out


# --------------------------------------------------------------------------- Konfiguration
def _split_keywords(raw: str) -> list[str]:
    return [k.strip().lower() for k in (raw or "").split(",") if k.strip()]


def _kw_pattern(keywords: list[str]) -> re.Pattern | None:
    if not keywords:
        return None
    parts = []
    for kw in keywords:
        p = r"(?<!\w)" + re.escape(kw)
        if len(kw) <= 3:  # kurze Kürzel ("ZA") nur als ganzes Wort
            p += r"(?!\w)"
        parts.append(p)
    return re.compile("|".join(parts), re.IGNORECASE)


def parse_day_month(raw: str) -> tuple[int, int]:
    """'16.08.' / '16.8' -> (16, 8)."""
    m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\.?\s*$", raw or "")
    if not m:
        raise ValueError(f"Ungültiges Datum (TT.MM.): {raw!r}")
    d, mo = int(m.group(1)), int(m.group(2))
    date(2000, mo, d)  # validiert
    return d, mo


@dataclass
class CalcConfig:
    hours_per_day: float = 8.0
    workdays: set[int] = field(default_factory=lambda: {0, 1, 2, 3, 4})
    start_date: date | None = None
    leave_year_start: tuple[int, int] = (16, 8)  # (Tag, Monat)
    care_entitlement: float = 5
    xmas_eve_nye_free: bool = False
    kw_vacation: str = "Urlaub"
    kw_za: str = "Zeitausgleich, ZA"
    kw_care: str = "Pflegeurlaub"
    kw_sick: str = "Krankenstand, Krank"
    kw_ignore: str = ""
    start_balance: float = 0.0
    leave_balance: float = 0.0
    leave_balance_date: date | None = None
    payouts: str = ""
    builtin_holidays: bool = True
    extra_holidays: dict[date, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Reihenfolge = Priorität (Pflegeurlaub vor Urlaub)
        self._patterns = [
            (CAT_IGNORE, _kw_pattern(_split_keywords(self.kw_ignore))),
            (CAT_CARE, _kw_pattern(_split_keywords(self.kw_care))),
            (CAT_SICK, _kw_pattern(_split_keywords(self.kw_sick))),
            (CAT_ZA, _kw_pattern(_split_keywords(self.kw_za))),
            (CAT_VACATION, _kw_pattern(_split_keywords(self.kw_vacation))),
        ]

    def classify(self, summary: str) -> str | None:
        for cat, pat in self._patterns:
            if pat and pat.search(summary or ""):
                return cat
        return None


# --------------------------------------------------------------------------- Auszahlungen
_MONTH_ALIASES = {
    1: ("jän", "jan"), 2: ("feb",), 3: ("mär", "mar", "mrz"), 4: ("apr",), 5: ("mai", "may"),
    6: ("jun",), 7: ("jul",), 8: ("aug",), 9: ("sep",), 10: ("okt", "oct"),
    11: ("nov",), 12: ("dez", "dec"),
}
_PAYOUT_RE = re.compile(
    r"(?P<month>[a-zäöü]+)\.?\s*:?\s*(?P<hours>\d+(?:[.,]\d+)?)\s*(?:h|std\.?|stunden)?",
    re.IGNORECASE,
)


def parse_payouts(raw: str) -> list[tuple[int | None, float]]:
    """'Mai: 20h, August 16 Stunden' -> [(5, 20.0), (8, 16.0)]. Reine Zahl -> (None, x)."""
    result: list[tuple[int | None, float]] = []
    text = (raw or "").strip()
    if not text:
        return result
    for m in _PAYOUT_RE.finditer(text):
        word = m.group("month").lower()
        month = next((n for n, al in _MONTH_ALIASES.items() if word.startswith(al)), None)
        if month is None:
            continue
        result.append((month, float(m.group("hours").replace(",", "."))))
    if not result:
        num = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:h|std\.?|stunden)?\s*", text, re.IGNORECASE)
        if num:
            result.append((None, float(num.group(1).replace(",", "."))))
    return result


# --------------------------------------------------------------------------- Kernberechnung
@dataclass
class DayData:
    work_intervals: list[tuple[datetime, datetime]] = field(default_factory=list)
    allday: str | None = None  # Abwesenheits-Kategorie (ganztägig)
    timed_abs: dict[str, float] = field(default_factory=dict)

    def work_hours(self) -> float:
        if not self.work_intervals:
            return 0.0
        ivs = sorted(self.work_intervals)
        total = 0.0
        cur_s, cur_e = ivs[0]
        for s, e in ivs[1:]:
            if s <= cur_e:
                cur_e = max(cur_e, e)
            else:
                total += (cur_e - cur_s).total_seconds()
                cur_s, cur_e = s, e
        total += (cur_e - cur_s).total_seconds()
        return total / 3600


def _parse(value: str, tz: tzinfo) -> tuple[datetime | date, bool]:
    if len(value) == 10:
        return date.fromisoformat(value), True
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz), False


class Calculator:
    def __init__(self, events: list[dict], cfg: CalcConfig, now: datetime) -> None:
        self.cfg = cfg
        self.now = now
        self.today = now.date()
        self.tz = now.tzinfo
        self.days: dict[date, DayData] = {}
        self._hol_cache: dict[int, dict[date, str]] = {}
        self._ingest(events)

    # ---- Aufbereitung
    def _day(self, d: date) -> DayData:
        return self.days.setdefault(d, DayData())

    def _ingest(self, events: list[dict]) -> None:
        for ev in events:
            summary = ev.get("summary") or ""
            cat = self.cfg.classify(summary)
            if cat == CAT_IGNORE:
                continue
            start, allday = _parse(ev["start"], self.tz)
            end, _ = _parse(ev["end"], self.tz)
            if allday:
                if cat is None:
                    continue  # unbekannte Ganztages-Events ignorieren
                d = start
                while d < end:
                    dd = self._day(d)
                    if dd.allday is None:
                        dd.allday = cat
                    d += timedelta(days=1)
                continue
            if cat is None:
                if start >= self.now:
                    continue  # geplante Arbeit zählt erst, wenn sie begonnen hat
                self._day(start.date()).work_intervals.append((start, min(end, self.now)))
            else:
                hours = (end - start).total_seconds() / 3600
                ta = self._day(start.date()).timed_abs
                ta[cat] = ta.get(cat, 0.0) + hours

    # ---- Tageswerte
    def holidays(self, year: int) -> dict[date, str]:
        if year not in self._hol_cache:
            hol: dict[date, str] = {}
            if self.cfg.builtin_holidays:
                hol.update(austrian_holidays(year, self.cfg.xmas_eve_nye_free))
            elif self.cfg.xmas_eve_nye_free:
                hol[date(year, 12, 24)] = "Heiliger Abend"
                hol[date(year, 12, 31)] = "Silvester"
            hol.update({d: n for d, n in self.cfg.extra_holidays.items() if d.year == year})
            self._hol_cache[year] = hol
        return self._hol_cache[year]

    def is_holiday(self, d: date) -> bool:
        return d in self.holidays(d.year)

    def soll(self, d: date) -> float:
        if self.cfg.start_date and d < self.cfg.start_date:
            return 0.0
        if d.weekday() not in self.cfg.workdays or self.is_holiday(d):
            return 0.0
        return self.cfg.hours_per_day

    def absence_hours(self, d: date) -> dict[str, float]:
        out = {c: 0.0 for c in ABSENCES}
        dd = self.days.get(d)
        if not dd:
            return out
        s = self.soll(d)
        if dd.allday and s > 0:
            out[dd.allday] += s
        if s > 0:
            for c, h in dd.timed_abs.items():
                out[c] += h
        return out

    def absence_day(self, d: date, cat: str) -> float:
        """Abwesenheitstage (ganztägig = 1, stundenweise = anteilig)."""
        dd = self.days.get(d)
        if not dd or self.soll(d) <= 0:
            return 0.0
        if dd.allday == cat:
            return 1.0
        return dd.timed_abs.get(cat, 0.0) / self.cfg.hours_per_day

    def work(self, d: date) -> float:
        if self.cfg.start_date and d < self.cfg.start_date:
            return 0.0
        dd = self.days.get(d)
        return dd.work_hours() if dd else 0.0

    # ---- Zeiträume
    def period(self, start: date, end: date) -> dict:
        """Summen über [start, end] (inklusive)."""
        res = {"ist": 0.0, "soll": 0.0, "arbeitstage": 0, "feiertage": 0,
               **{f"{c}_h": 0.0 for c in ABSENCES}, **{f"{c}_t": 0.0 for c in ABSENCES}}
        d = start
        while d <= end:
            s = self.soll(d)
            res["soll"] += s
            if s > 0:
                res["arbeitstage"] += 1
            elif d.weekday() in self.cfg.workdays and self.is_holiday(d):
                res["feiertage"] += 1
            res["ist"] += self.work(d)
            for c, h in self.absence_hours(d).items():
                res[f"{c}_h"] += h
                res[f"{c}_t"] += self.absence_day(d, c)
            d += timedelta(days=1)
        res["angerechnet"] = res["ist"] + sum(res[f"{c}_h"] for c in ABSENCES)
        res["saldo"] = res["angerechnet"] - res["soll"]
        return res

    def absence_dates(self, start: date, end: date, cat: str) -> list[date]:
        return [d for d in sorted(self.days) if start <= d <= end and self.absence_day(d, cat) > 0]


# --------------------------------------------------------------------------- Ausgabe
def _h(v: float) -> str:
    return f"{v:.2f}"


def _t(v: float) -> int | float:
    return int(v) if float(v).is_integer() else round(v, 2)


def _tage_liste(calc: Calculator, dates: list[date], cat: str, with_dates: bool) -> list[str]:
    by_month: dict[int, list[date]] = {}
    for d in dates:
        by_month.setdefault(d.month, []).append(d)
    out = []
    for mo, ds in by_month.items():
        n = _t(sum(calc.absence_day(d, cat) for d in ds))
        label = f"{MONTHS_DE[mo - 1]}: {n} {'Tag' if n == 1 else 'Tage'}"
        if with_dates:
            label += " (" + ", ".join(f"{d.day:02d}." for d in ds[:-1])
            label += (", " if len(ds) > 1 else "") + f"{ds[-1].day:02d}.{mo:02d}.)"
        out.append(label)
    return out


def leave_year_bounds(cfg: CalcConfig, today: date) -> tuple[date, date]:
    d, m = cfg.leave_year_start
    start = date(today.year, m, d)
    if start > today:
        start = date(today.year - 1, m, d)
    end = date(start.year + 1, m, d) - timedelta(days=1)
    return start, end


def fetch_window(cfg: CalcConfig, today: date) -> tuple[date, date]:
    """Welcher Zeitraum muss aus dem Kalender geladen werden."""
    ly_start, ly_end = leave_year_bounds(cfg, today)
    week_start = today - timedelta(days=today.weekday())
    candidates = [date(today.year, 1, 1), ly_start, week_start]
    if cfg.leave_balance_date:
        candidates.append(cfg.leave_balance_date)
    start = min(candidates)
    end = max(date(today.year, 12, 31), ly_end, week_start + timedelta(days=6))
    return start, end


def calculate(events: list[dict], cfg: CalcConfig, now: datetime) -> dict[str, dict]:
    """Liefert {sensor_key: {"state": ..., "attributes": {...}}}."""
    c = Calculator(events, cfg, now)
    today = c.today
    stamp = now.strftime("%d.%m.%Y %H:%M")
    out: dict[str, dict] = {}

    # ---- Woche
    w_start = today - timedelta(days=today.weekday())
    w_end = w_start + timedelta(days=6)
    wk = c.period(w_start, today)
    wk_full = c.period(w_start, w_end)
    kw = f"KW {today.isocalendar().week}"
    zeitraum = f"{w_start:%d.%m.} - {w_end:%d.%m.%Y}"
    heute = c.period(today, today)
    week_attrs = {
        "zeitraum": zeitraum,
        "kalenderwoche": kw,
        "ist_gearbeitet": _h(wk["ist"]),
        "urlaub_stunden": _h(wk["urlaub_h"]),
        "urlaub_tage": _t(wk["urlaub_t"]),
        "za_stunden": _h(wk["za_h"]),
        "za_tage": _t(wk["za_t"]),
        "pflegeurlaub_stunden": _h(wk["pflege_h"]),
        "krankenstand_stunden": _h(wk["krank_h"]),
        "soll_stunden_woche": _h(wk_full["soll"]),
        "soll_stunden_bis_heute": _h(wk["soll"]),
        "gesamt_angerechnet": _h(wk["angerechnet"]),
        "saldo_gesamte_woche": _h(wk_full["angerechnet"] - wk_full["soll"]),
        "heute_gearbeitet": _h(heute["ist"]),
        "heute_offen": _h(max(0.0, heute["soll"] - heute["angerechnet"])),
        "last_calculated": stamp,
    }
    out["uberstunden_woche"] = {"state": round(wk["saldo"], 2), "attributes": week_attrs}
    out["arbeitszeit_woche_ist"] = {
        "state": round(wk["ist"], 2),
        "attributes": {k: week_attrs[k] for k in (
            "zeitraum", "kalenderwoche", "urlaub_stunden", "urlaub_tage", "za_stunden",
            "za_tage", "gesamt_angerechnet", "last_calculated")},
    }

    # ---- Monat
    m_start = today.replace(day=1)
    m_end = (m_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    mo = c.period(m_start, today)
    mo_full = c.period(m_start, m_end)
    # Monatsende-Prognose: Ist bis heute + geplante Abwesenheiten des ganzen Monats
    mo_end_saldo = (mo["ist"] + sum(mo_full[f"{k}_h"] for k in ABSENCES)) - mo_full["soll"]
    monat_label = f"{MONTHS_DE[today.month - 1]} {today.year}"
    month_attrs = {
        "monat": monat_label,
        "arbeitstage_gesamt": mo_full["arbeitstage"],
        "arbeitstage_bisher": mo["arbeitstage"],
        "ist_gearbeitet": _h(mo["ist"]),
        "urlaub_tage_bisher": _t(mo["urlaub_t"]),
        "urlaub_stunden_bisher": _h(mo["urlaub_h"]),
        "urlaub_tage_gesamt": _t(mo_full["urlaub_t"]),
        "urlaub_stunden_gesamt": _h(mo_full["urlaub_h"]),
        "za_tage_bisher": _t(mo["za_t"]),
        "za_stunden_bisher": _h(mo["za_h"]),
        "pflegeurlaub_stunden_bisher": _h(mo["pflege_h"]),
        "krankenstand_stunden_bisher": _h(mo["krank_h"]),
        "soll_stunden_bis_heute": _h(mo["soll"]),
        "soll_stunden_gesamt": _h(mo_full["soll"]),
        "gesamt_angerechnet": _h(mo["angerechnet"]),
        "saldo_monatsende": _h(mo_end_saldo),
        "last_calculated": stamp,
    }
    out["uberstunden_monat"] = {"state": round(mo["saldo"], 2), "attributes": month_attrs}
    out["arbeitszeit_monat_ist"] = {
        "state": round(mo["ist"], 2),
        "attributes": {
            "monat": monat_label,
            "urlaub_tage_bisher": month_attrs["urlaub_tage_bisher"],
            "urlaub_stunden_bisher": month_attrs["urlaub_stunden_bisher"],
            "gesamt_angerechnet": month_attrs["gesamt_angerechnet"],
            "last_calculated": stamp,
        },
    }
    out["urlaub_monat"] = {
        "state": _t(mo_full["urlaub_t"]),
        "attributes": {"monat": monat_label, "urlaub_stunden_gesamt": _h(mo_full["urlaub_h"]),
                       "urlaub_tage_bisher": _t(mo["urlaub_t"]), "last_calculated": stamp},
    }

    # ---- Jahr
    y_start = date(today.year, 1, 1)
    if cfg.start_date and cfg.start_date > y_start:
        y_start = cfg.start_date
    y_end = date(today.year, 12, 31)
    yr = c.period(y_start, today)
    yr_full = c.period(y_start, y_end)

    monats_uebersicht = []
    for month in range(y_start.month, today.month + 1):
        ms = max(date(today.year, month, 1), y_start)
        me = (date(today.year, month, 1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        p = c.period(ms, min(me, today))
        parts = [f"Ist {p['ist']:.1f}h", f"Soll {p['soll']:.1f}h"]
        for key, label in ((CAT_VACATION, "Urlaub"), (CAT_ZA, "ZA"), (CAT_CARE, "Pflege"), (CAT_SICK, "Krank")):
            if p[f"{key}_t"] > 0:
                txt = f"{label} {_t(p[f'{key}_t'])}T"
                if key in (CAT_VACATION, CAT_ZA):
                    txt += f" ({p[f'{key}_h']:.0f}h)"
                parts.append(txt)
        if p["feiertage"]:
            parts.append(f"Feiertage {p['feiertage']}T")
        parts.append(f"Saldo {p['saldo']:+.1f}h")
        monats_uebersicht.append(f"{MONTHS_DE[month - 1]} {today.year}: " + " | ".join(parts))

    payouts = [(m, h) for m, h in parse_payouts(cfg.payouts)]
    payout_h = sum(h for _, h in payouts)
    payout_list = [f"{MONTHS_DE[m - 1] if m else 'Ohne Monat'}: {h:g}h" for m, h in payouts]
    netto = yr["saldo"] + cfg.start_balance - yr["za_h"] - payout_h

    out["uberstunden_jahr"] = {
        "state": round(yr["saldo"], 2),
        "attributes": {
            "erfassungsstart": y_start.isoformat(),
            "datenquelle": "Kalender (arbeitszeit_at)",
            "ist_gearbeitet": _h(yr["ist"]),
            "soll_stunden_jahr": _h(yr["soll"]),
            "soll_stunden_jahr_gesamt": _h(yr_full["soll"]),
            "urlaub_tage_jahr": _t(yr["urlaub_t"]),
            "urlaub_stunden_jahr": _h(yr["urlaub_h"]),
            "za_tage_jahr": _t(yr["za_t"]),
            "za_stunden_jahr": _h(yr["za_h"]),
            "pflegeurlaub_tage_jahr": _t(yr["pflege_t"]),
            "pflegeurlaub_stunden_jahr": _h(yr["pflege_h"]),
            "krankenstand_tage_jahr": _t(yr["krank_t"]),
            "krankenstand_stunden_jahr": _h(yr["krank_h"]),
            "gesamt_erbracht": _h(yr["angerechnet"]),
            "ueberstunden_guthaben": _h(yr["saldo"]),
            "startsaldo_uebertrag": _h(cfg.start_balance),
            "za_ausbezahlt_stunden": _h(payout_h),
            "za_ausbezahlt_tage": _h(payout_h / cfg.hours_per_day),
            "saldo_netto_mit_za_abzug": _h(netto),
            "monats_uebersicht": monats_uebersicht,
            "last_calculated": stamp,
        },
    }
    out["uberstunden_saldo_netto"] = {
        "state": round(netto, 2),
        "attributes": {
            "brutto": _h(yr["saldo"]), "startsaldo": _h(cfg.start_balance),
            "za_verbraucht": _h(yr["za_h"]), "ausbezahlt": _h(payout_h),
            "last_calculated": stamp,
        },
    }
    out["arbeitszeit_jahr_ist"] = {
        "state": round(yr["ist"], 2),
        "attributes": {
            "erfassungsstart": y_start.isoformat(),
            "urlaub_tage_jahr": _t(yr["urlaub_t"]),
            "urlaub_stunden_jahr": _h(yr["urlaub_h"]),
            "za_tage_jahr": _t(yr["za_t"]),
            "za_stunden_jahr": _h(yr["za_h"]),
            "last_calculated": stamp,
        },
    }

    # ---- Urlaub
    ly_start, ly_end = leave_year_bounds(cfg, today)
    ly = c.period(ly_start, ly_end)
    ly_so_far = c.period(ly_start, today)
    stand_date = cfg.leave_balance_date or today
    since_stand = c.period(stand_date + timedelta(days=1), ly_end) if stand_date < ly_end else None
    used_since = since_stand["urlaub_t"] if since_stand else 0.0
    rest = cfg.leave_balance - used_since
    ly_zeitraum = f"{ly_start:%d.%m.%Y} - {ly_end:%d.%m.%Y}"
    vac_year_dates = c.absence_dates(date(today.year, 1, 1), y_end, CAT_VACATION)
    out["urlaub_rest"] = {
        "state": _t(rest),
        "attributes": {
            "resturlaub_tage": f"{rest:.1f}",
            "resturlaub_stunden": f"{rest * cfg.hours_per_day:.1f}",
            "stand_laut_lohnzettel": f"{cfg.leave_balance:.1f}",
            "stand_datum": stand_date.strftime("%d.%m.%Y"),
            "verbrauch_seit_stand_tage": f"{used_since:.1f}",
            "urlaubsjahr_zeitraum": ly_zeitraum,
            "urlaubsjahr_ende": ly_end.strftime("%d.%m.%Y"),
            "verbrauch_urlaubsjahr_tage": f"{ly['urlaub_t']:.1f}",
            "verbrauch_urlaubsjahr_bisher_tage": f"{ly_so_far['urlaub_t']:.1f}",
            "verbrauch_urlaubsjahr_stunden": f"{ly['urlaub_h']:.1f}",
            "gesamtanspruch_urlaubsjahr_tage": f"{rest + ly['urlaub_t']:.1f}",
            "last_calculated": stamp,
        },
    }
    out["urlaub_jahr"] = {
        "state": _t(yr_full["urlaub_t"]),
        "attributes": {
            "kalenderjahr": today.year,
            "urlaub_tage": _t(yr_full["urlaub_t"]),
            "urlaub_tage_bisher": _t(yr["urlaub_t"]),
            "urlaub_stunden": _h(yr_full["urlaub_h"]),
            "tage_liste": _tage_liste(c, vac_year_dates, CAT_VACATION, False),
            "urlaubsjahr_zeitraum": ly_zeitraum,
            "resturlaub_urlaubsjahr": f"{rest:.1f}",
            "last_calculated": stamp,
        },
    }

    # ---- ZA, Pflege, Krank, Auszahlung
    za_dates = c.absence_dates(y_start, y_end, CAT_ZA)
    out["za_jahr"] = {
        "state": _t(yr_full["za_t"]),
        "attributes": {
            "za_tage": _t(yr_full["za_t"]), "za_stunden": _h(yr_full["za_h"]),
            "za_tage_bisher": _t(yr["za_t"]),
            "tage_liste": _tage_liste(c, za_dates, CAT_ZA, True), "last_calculated": stamp,
        },
    }
    care_dates = c.absence_dates(y_start, y_end, CAT_CARE)
    out["pflegeurlaub_jahr"] = {
        "state": _t(yr_full["pflege_t"]),
        "attributes": {
            "pflegeurlaub_tage": _t(yr_full["pflege_t"]),
            "pflegeurlaub_stunden": _h(yr_full["pflege_h"]),
            "anspruch_gesamt": _t(cfg.care_entitlement),
            "verbleibend": _t(cfg.care_entitlement - yr_full["pflege_t"]),
            "tage_liste": _tage_liste(c, care_dates, CAT_CARE, True),
            "last_calculated": stamp,
        },
    }
    sick_dates = c.absence_dates(y_start, y_end, CAT_SICK)
    out["krankenstand_jahr"] = {
        "state": _t(yr_full["krank_t"]),
        "attributes": {
            "krankenstand_stunden": _h(yr_full["krank_h"]),
            "tage_liste": _tage_liste(c, sick_dates, CAT_SICK, True),
            "last_calculated": stamp,
        },
    }
    out["za_ausbezahlt_jahr"] = {
        "state": round(payout_h, 2),
        "attributes": {
            "stunden": _h(payout_h), "tage": _h(payout_h / cfg.hours_per_day),
            "auszahlungen_liste": payout_list, "eingabe_text": cfg.payouts,
            "last_calculated": stamp,
        },
    }
    return out
