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
- Entgangene Zuschläge (Schätzung): Überstunden je Kalenderwoche (angerechnet − Soll, nur
  positive Wochen). Davon zuerst Sonn-/Feiertags- und Nachtstunden mit 100 %, der Rest mit 50 %.
  Verlust = 0,5 × h50 + 1,0 × h100 (Stunden) × Stundensatz (Bruttomonatsgehalt / (Wochenstunden × 4,33)).
- AZG-/ARG-Verstöße: > 12 h/Tag, > 60 h/Woche, Ø > 48 h über 17 Wochen, < 11 h tägliche Ruhe,
  < 36 h Wochenruhe, > 6 h ohne 30 min Pause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo

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
ABSENCE_LABELS = {
    CAT_VACATION: "Urlaub",
    CAT_ZA: "Zeitausgleich",
    CAT_CARE: "Pflegeurlaub",
    CAT_SICK: "Krankenstand",
}


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
    monthly_salary: float = 0.0
    night_window: str = "22:00-06:00"
    sunday_holiday_100: bool = True
    pause_check: bool = True
    payout_entries: list[tuple[date, float]] = field(default_factory=list)  # per Service erfasst
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


_PAYOUT_DATE_RE = re.compile(
    r"(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{2,4})?\s*:?\s*(?P<hours>\d+(?:[.,]\d+)?)\s*(?:h|std\.?|stunden)?",
    re.IGNORECASE,
)


@dataclass
class Payout:
    hours: float
    month: int | None = None
    day: date | None = None  # exaktes Auszahlungsdatum (Jahr fehlt -> aktuelles Jahr)

    @property
    def label(self) -> str:
        if self.day:
            return f"{self.day:%d.%m.%Y}: {self.hours:g}h"
        return f"{MONTHS_DE[self.month - 1] if self.month else 'Ohne Monat'}: {self.hours:g}h"


def parse_payouts_detailed(raw: str, year: int | None = None) -> list[Payout]:
    """'15.05.2026: 20h, Mai: 20h, August 16 Stunden, 12' -> Payout-Liste (Textreihenfolge)."""
    year = year or date.today().year
    text = (raw or "").strip()
    found: list[tuple[int, Payout]] = []
    if not text:
        return []
    for m in _PAYOUT_DATE_RE.finditer(text):
        y = int(m.group("y")) if m.group("y") else year
        if y < 100:
            y += 2000
        try:
            d = date(y, int(m.group("m")), int(m.group("d")))
        except ValueError:
            continue
        found.append((m.start(), Payout(float(m.group("hours").replace(",", ".")), d.month, d)))
    rest = _PAYOUT_DATE_RE.sub(" ", text)
    for m in _PAYOUT_RE.finditer(rest):
        word = m.group("month").lower()
        month = next((n for n, al in _MONTH_ALIASES.items() if word.startswith(al)), None)
        if month is None:
            continue
        found.append((10**6 + m.start(), Payout(float(m.group("hours").replace(",", ".")), month)))
    if not found:
        num = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:h|std\.?|stunden)?\s*", text, re.IGNORECASE)
        if num:
            found.append((0, Payout(float(num.group(1).replace(",", ".")))))
    return [p for _, p in sorted(found, key=lambda t: t[0])]


def parse_payouts(raw: str) -> list[tuple[int | None, float]]:
    """'Mai: 20h, August 16 Stunden' -> [(5, 20.0), (8, 16.0)]. Reine Zahl -> (None, x)."""
    return [(p.month, p.hours) for p in parse_payouts_detailed(raw)]


def format_payout_entry(day: date, hours: float) -> str:
    return f"{day:%d.%m.%Y}: {hours:g}h"


# --------------------------------------------------------------------------- Kernberechnung
@dataclass
class DayData:
    work_intervals: list[tuple[datetime, datetime]] = field(default_factory=list)
    allday: str | None = None  # Abwesenheits-Kategorie (ganztägig)
    timed_abs: dict[str, float] = field(default_factory=dict)

    def merged(self) -> list[tuple[datetime, datetime]]:
        return merge_intervals(self.work_intervals)

    def work_hours(self) -> float:
        return sum((e - s).total_seconds() for s, e in self.merged()) / 3600


def merge_intervals(ivs: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for s, e in sorted(ivs):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def parse_time_window(raw: str) -> tuple[time, time] | None:
    """'22:00-06:00' -> (22:00, 06:00). Leer -> None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?", raw)
    if not m:
        raise ValueError(f"Ungültiges Zeitfenster (HH:MM-HH:MM): {raw!r}")
    h1, m1, h2, m2 = (int(x or 0) for x in m.groups())
    return time(h1 % 24, m1), time(h2 % 24, m2)


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

    def absence_blocks(self, start: date, end: date, cat: str) -> list[tuple[date, date, float]]:
        """Zusammenhängende Zeiträume (von, bis, Tage) einer Abwesenheitsart.

        Tage ohne Soll (Wochenende, Feiertag) dazwischen unterbrechen einen Zeitraum nicht,
        ein Arbeitstag ohne diese Abwesenheit schon.
        """
        blocks: list[tuple[date, date, float]] = []
        for d in self.absence_dates(start, end, cat):
            if blocks:
                b_start, b_end, n = blocks[-1]
                gap = b_end + timedelta(days=1)
                while gap < d and self.soll(gap) <= 0:
                    gap += timedelta(days=1)
                if gap == d:
                    blocks[-1] = (b_start, d, n + self.absence_day(d, cat))
                    continue
            blocks.append((d, d, self.absence_day(d, cat)))
        return blocks


# --------------------------------------------------------------------------- Zuschläge & Verstöße
WEEKS_PER_MONTH = 4.33  # Überstundenteiler: Wochenstunden × 4,33 (z. B. 40 h -> 1/173)

V_DAY = "Tageshöchstarbeitszeit"
V_WEEK = "Wochenhöchstarbeitszeit"
V_AVG = "Durchschnitt 48 h (17 Wochen)"
V_REST = "Tägliche Ruhezeit"
V_WEEKREST = "Wochenruhe"
V_PAUSE = "Ruhepause"
VIOLATION_RULES = {
    V_DAY: "§ 9 AZG: max. 12 h pro Tag",
    V_WEEK: "§ 9 AZG: max. 60 h pro Woche",
    V_AVG: "§ 9 Abs. 4 AZG: Ø max. 48 h in 17 Wochen",
    V_REST: "§ 12 AZG: mind. 11 h Ruhe zwischen zwei Arbeitstagen",
    V_WEEKREST: "§ 3/§ 4 ARG: mind. 36 h ununterbrochene Ruhe pro Woche",
    V_PAUSE: "§ 11 AZG: ab mehr als 6 h mind. 30 min Pause",
}


def _overlap(ivs: list[tuple[datetime, datetime]], ws: datetime, we: datetime) -> float:
    return sum(max(0.0, (min(e, we) - max(s, ws)).total_seconds()) for s, e in ivs) / 3600


def _night_hours(ivs: list[tuple[datetime, datetime]], window: tuple[time, time] | None) -> float:
    if not window or not ivs:
        return 0.0
    ts, te = window
    tz = ivs[0][0].tzinfo
    first, last = ivs[0][0].date() - timedelta(days=1), ivs[-1][1].date()
    total, d = 0.0, first
    while d <= last:
        ws = datetime.combine(d, ts, tz)
        we = datetime.combine(d + timedelta(days=1) if te <= ts else d, te, tz)
        total += _overlap(ivs, ws, we)
        d += timedelta(days=1)
    return total


def surcharge_analysis(c: "Calculator", start: date, end: date) -> dict:
    """Überstunden je Kalenderwoche in [start, end], aufgeteilt auf 50 % / 100 %."""
    cfg = c.cfg
    window = parse_time_window(cfg.night_window)
    weeks: list[dict] = []
    ws = start - timedelta(days=start.weekday())
    while ws <= end:
        s, e = max(ws, start), min(ws + timedelta(days=6), end)
        p = c.period(s, e)
        ot = max(0.0, p["saldo"])
        cand = 0.0
        d = s
        while d <= e:
            dd = c.days.get(d)
            if dd and c.work(d) > 0:
                if cfg.sunday_holiday_100 and (d.weekday() == 6 or c.is_holiday(d)):
                    cand += c.work(d)
                else:
                    cand += _night_hours(dd.merged(), window)
            d += timedelta(days=1)
        h100 = min(ot, cand)
        h50 = ot - h100
        weeks.append({"von": s, "bis": e, "ueberstunden": ot, "h50": h50, "h100": h100,
                      "verlust_h": 0.5 * h50 + 1.0 * h100})
        ws += timedelta(days=7)
    return {"wochen": weeks,
            "h50": sum(w["h50"] for w in weeks), "h100": sum(w["h100"] for w in weeks),
            "ueberstunden": sum(w["ueberstunden"] for w in weeks),
            "verlust_h": sum(w["verlust_h"] for w in weeks)}


def violations(c: "Calculator", start: date, end: date) -> list[dict]:
    """AZG-/ARG-Grenzen aus den Kalenderdaten in [start, end] prüfen (nur Vergangenes)."""
    cfg = c.cfg
    tz = c.tz
    out: list[dict] = []

    def add(d: date, art: str, wert: float, grenze: float, text: str) -> None:
        out.append({"datum": d.isoformat(), "art": art, "wert": round(wert, 2),
                    "grenze": grenze, "text": text, "regel": VIOLATION_RULES[art]})

    all_ivs = merge_intervals([iv for d, dd in c.days.items() if start <= d <= end
                               and not (cfg.start_date and d < cfg.start_date)
                               for iv in dd.work_intervals])

    # Tag > 12 h, Pause
    d = start
    while d <= end:
        dd = c.days.get(d)
        if dd and c.work(d) > 0:
            h = c.work(d)
            if h > 12:
                add(d, V_DAY, h, 12, f"{d:%d.%m.}: {h:.2f} h gearbeitet")
            if cfg.pause_check:
                blk_s = blk_work = None
                for s, e in dd.merged():
                    if blk_s is not None and (s - blk_end).total_seconds() < 1800:
                        blk_work += (e - s).total_seconds() / 3600
                    else:
                        blk_s, blk_work = s, (e - s).total_seconds() / 3600
                    blk_end = e
                    if blk_work > 6:
                        add(d, V_PAUSE, blk_work, 6,
                            f"{d:%d.%m.}: {blk_work:.2f} h ab {blk_s:%H:%M} ohne 30 min Pause")
                        blk_s = None
                        break
        d += timedelta(days=1)

    # Tägliche Ruhezeit < 11 h (Lücken zwischen Arbeitsblöcken an verschiedenen Tagen)
    for (s1, e1), (s2, e2) in zip(all_ivs, all_ivs[1:]):
        gap = (s2 - e1).total_seconds() / 3600
        if s2.date() != e1.date() and gap < 11:
            add(s2.date(), V_REST, gap, 11,
                f"{e1:%d.%m. %H:%M} → {s2:%d.%m. %H:%M}: nur {gap:.2f} h Ruhe")

    # Woche > 60 h, Wochenruhe < 36 h, Ø 48 h / 17 Wochen
    week_hours: list[tuple[date, float]] = []
    ws = start - timedelta(days=start.weekday())
    while ws <= end:
        we = ws + timedelta(days=6)
        h = sum(c.work(x) for x in (ws + timedelta(days=i) for i in range(7)) if start <= x <= end)
        kw = f"KW {ws.isocalendar().week}"
        if h > 60:
            add(ws, V_WEEK, h, 60, f"{kw}: {h:.2f} h gearbeitet")
        complete = ws >= start and we < c.today
        if complete:
            week_hours.append((ws, h))
            w0 = datetime.combine(ws, time.min, tz)
            w1 = datetime.combine(we + timedelta(days=1), time.min, tz)
            inside = [(max(s, w0), min(e, w1)) for s, e in all_ivs if e > w0 and s < w1]
            if inside:
                points = [w0] + [t for iv in inside for t in iv] + [w1]
                best = max((points[i + 1] - points[i]).total_seconds() / 3600
                           for i in range(0, len(points), 2))
                if best < 36:
                    add(ws, V_WEEKREST, best, 36, f"{kw}: längste Ruhe nur {best:.2f} h")
        ws += timedelta(days=7)
    for i in range(16, len(week_hours)):
        avg = sum(h for _, h in week_hours[i - 16:i + 1]) / 17
        if avg > 48:
            add(week_hours[i][0], V_AVG, avg, 48,
                f"17 Wochen bis KW {week_hours[i][0].isocalendar().week}: Ø {avg:.2f} h")
    out.sort(key=lambda v: (v["datum"], v["art"]))
    return out


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


def _zeitraum_text(von: date, bis: date) -> str:
    return f"{von:%d.%m.}" if von == bis else f"{von:%d.%m.} – {bis:%d.%m.}"


def _zeitraeume(calc: Calculator, start: date, end: date, cat: str) -> list[dict]:
    """Abwesenheits-Zeiträume mit Datum, Werktagen und Status (genommen/laufend/geplant)."""
    out = []
    for von, bis, n in calc.absence_blocks(start, end, cat):
        if bis < calc.today:
            status = "genommen"
        elif von > calc.today:
            status = "geplant"
        else:
            status = "laufend"
        out.append({
            "art": ABSENCE_LABELS[cat],
            "von": von.isoformat(),
            "bis": bis.isoformat(),
            "zeitraum": _zeitraum_text(von, bis),
            "tage": _t(n),
            "stunden": _h(n * calc.cfg.hours_per_day),
            "status": status,
        })
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

    payouts_all = parse_payouts_detailed(cfg.payouts, today.year) + [
        Payout(h, d.month, d) for d, h in sorted(cfg.payout_entries)]
    # Datierte Auszahlungen zählen nur im eigenen Kalenderjahr und erst ab dem Auszahlungstag
    payouts = [p for p in payouts_all if p.day is None or (p.day.year == today.year and p.day <= today)]
    payouts_planned = [p for p in payouts_all if p.day and p.day.year == today.year and p.day > today]
    payout_h = sum(p.hours for p in payouts)
    payout_list = [p.label for p in payouts]
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
            "zeitraeume": _zeitraeume(c, date(today.year, 1, 1), y_end, CAT_VACATION),
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
            "tage_liste": _tage_liste(c, za_dates, CAT_ZA, True),
            "zeitraeume": _zeitraeume(c, y_start, y_end, CAT_ZA), "last_calculated": stamp,
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
            "zeitraeume": _zeitraeume(c, y_start, y_end, CAT_CARE),
            "last_calculated": stamp,
        },
    }
    sick_dates = c.absence_dates(y_start, y_end, CAT_SICK)
    out["krankenstand_jahr"] = {
        "state": _t(yr_full["krank_t"]),
        "attributes": {
            "krankenstand_stunden": _h(yr_full["krank_h"]),
            "tage_liste": _tage_liste(c, sick_dates, CAT_SICK, True),
            "zeitraeume": _zeitraeume(c, y_start, y_end, CAT_SICK),
            "last_calculated": stamp,
        },
    }
    eintraege = sorted(
        (z for cat in ABSENCES for z in _zeitraeume(c, y_start, y_end, cat)),
        key=lambda z: (z["von"], z["art"]),
    )
    summen = {ABSENCE_LABELS[cat]: _t(yr_full[f"{cat}_t"]) for cat in ABSENCES}
    out["abwesenheiten_jahr"] = {
        "state": _t(sum(yr_full[f"{cat}_t"] for cat in ABSENCES)),
        "attributes": {
            "kalenderjahr": today.year,
            "eintraege": eintraege,
            "summen": summen,
            "genommen_tage": _t(sum(z["tage"] for z in eintraege if z["status"] != "geplant")),
            "geplant_tage": _t(sum(z["tage"] for z in eintraege if z["status"] == "geplant")),
            "naechste": next((z for z in eintraege if z["status"] != "genommen"), None),
            "last_calculated": stamp,
        },
    }
    out["za_ausbezahlt_jahr"] = {
        "state": round(payout_h, 2),
        "attributes": {
            "stunden": _h(payout_h), "tage": _h(payout_h / cfg.hours_per_day),
            "auszahlungen_liste": payout_list,
            "auszahlungen": [{"datum": p.day.isoformat() if p.day else None,
                              "monat": MONTHS_DE[p.month - 1] if p.month else None,
                              "stunden": _h(p.hours)} for p in payouts],
            "geplant_liste": [p.label for p in payouts_planned],
            "letzte_auszahlung": next((p.day.isoformat() for p in sorted(
                (p for p in payouts if p.day), key=lambda p: p.day, reverse=True)), None),
            "eingabe_text": cfg.payouts,
            "last_calculated": stamp,
        },
    }
    # ---- Entgangene Zuschläge & Verstöße (Kalenderjahr bis heute)
    sa = surcharge_analysis(c, y_start, today)
    rate = cfg.monthly_salary / (cfg.hours_per_day * len(cfg.workdays) * WEEKS_PER_MONTH) \
        if cfg.monthly_salary > 0 and cfg.workdays else 0.0
    brutto_pos = max(0.0, yr["saldo"])
    faktor = min(1.0, brutto_pos / sa["ueberstunden"]) if sa["ueberstunden"] > 0 else 0.0
    monate: dict[int, dict[str, float]] = {}
    for wk_ in sa["wochen"]:
        m = monate.setdefault(wk_["bis"].month, {"ue": 0.0, "h50": 0.0, "h100": 0.0, "v": 0.0})
        m["ue"] += wk_["ueberstunden"]; m["h50"] += wk_["h50"]; m["h100"] += wk_["h100"]
        m["v"] += wk_["verlust_h"]
    zuschlag_attrs = {
        "zeitraum": f"{y_start:%d.%m.%Y} - {today:%d.%m.%Y}",
        "ueberstunden_wochenbasis": _h(sa["ueberstunden"]),
        "ueberstunden_50_prozent": _h(sa["h50"]),
        "ueberstunden_100_prozent": _h(sa["h100"]),
        "entgangen_stunden": _h(sa["verlust_h"]),
        "entgangen_stunden_konservativ": _h(sa["verlust_h"] * faktor),
        "stundensatz": f"{rate:.2f}",
        "ueberstundenteiler": _h(cfg.hours_per_day * len(cfg.workdays) * WEEKS_PER_MONTH),
        "bruttomonatsgehalt": f"{cfg.monthly_salary:.2f}",
        "entgangen_eur": f"{sa['verlust_h'] * rate:.2f}",
        "entgangen_eur_konservativ": f"{sa['verlust_h'] * faktor * rate:.2f}",
        "wert_ueberstunden_mit_zuschlag_eur": f"{(sa['ueberstunden'] + sa['verlust_h']) * rate:.2f}",
        "nachtfenster": cfg.night_window or "aus",
        "sonn_feiertag_100": cfg.sunday_holiday_100,
        "monats_uebersicht": [
            f"{MONTHS_DE[mo_ - 1]}: Überstunden {v['ue']:.1f}h | 50% {v['h50']:.1f}h | 100% {v['h100']:.1f}h"
            f" | entgangen {v['v']:.1f}h" + (f" ≈ {v['v'] * rate:.0f} €" if rate else "")
            for mo_, v in sorted(monate.items())],
        "wochen": [
            {"kw": f"KW {wk_['von'].isocalendar().week}", "von": wk_["von"].isoformat(),
             "bis": wk_["bis"].isoformat(), "ueberstunden": _h(wk_["ueberstunden"]),
             "h50": _h(wk_["h50"]), "h100": _h(wk_["h100"]), "entgangen_h": _h(wk_["verlust_h"]),
             "entgangen_eur": f"{wk_['verlust_h'] * rate:.2f}"}
            for wk_ in sa["wochen"] if wk_["ueberstunden"] > 0],
        "hinweis": "Schätzung: Überstunden je Kalenderwoche, Minus-Wochen nicht gegengerechnet; "
                   "konservativ = auf positives Jahres-Brutto gedeckelt. KV-Regeln können abweichen.",
        "last_calculated": stamp,
    }
    out["zuschlaege_entgangen_stunden"] = {"state": round(sa["verlust_h"], 2), "attributes": zuschlag_attrs}
    out["zuschlaege_entgangen_eur"] = {"state": round(sa["verlust_h"] * rate, 2), "attributes": zuschlag_attrs}

    viol = violations(c, y_start, today)
    summen_v = {art: sum(1 for v in viol if v["art"] == art) for art in VIOLATION_RULES}
    out["azg_verstoesse_jahr"] = {
        "state": len(viol),
        "attributes": {
            "kalenderjahr": today.year,
            "summen": summen_v,
            "regeln": VIOLATION_RULES,
            "eintraege": viol,
            "letzter": viol[-1] if viol else None,
            "monat_aktuell": sum(1 for v in viol if v["datum"][:7] == today.strftime("%Y-%m")),
            "last_calculated": stamp,
        },
    }
    return out
