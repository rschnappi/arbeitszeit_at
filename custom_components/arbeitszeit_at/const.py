"""Konstanten für Arbeitszeit & Überstunden (AT)."""

from __future__ import annotations

DOMAIN = "arbeitszeit_at"
PLATFORMS = ["sensor", "number", "text"]

# Struktur-Optionen (Änderung => Reload)
CONF_CALENDAR = "calendar_entity"
CONF_ICS_URL = "ics_url"
CONF_HOLIDAY_CALENDAR = "holiday_calendar_entity"
CONF_HOLIDAY_FILTER = "holiday_filter"
CONF_BUILTIN_HOLIDAYS = "builtin_holidays"
CONF_HOURS_PER_DAY = "hours_per_day"  # ersetzt seit 1.3.0 durch CONF_WEEKLY_HOURS, nur noch für Migration gelesen
CONF_WEEKLY_HOURS = "weekly_hours"
CONF_WORKDAYS = "workdays"
CONF_START_DATE = "start_date"
CONF_LEAVE_YEAR_START = "leave_year_start"
CONF_CARE_ENTITLEMENT = "care_leave_entitlement"
CONF_XMAS_EVE_FREE = "xmas_eve_nye_free"
CONF_KW_VACATION = "keywords_vacation"
CONF_KW_ZA = "keywords_za"
CONF_KW_CARE = "keywords_care"
CONF_KW_SICK = "keywords_sick"
CONF_KW_IGNORE = "keywords_ignore"
CONF_SCAN_INTERVAL = "scan_interval"

# Dynamische Werte (Änderung => nur Refresh; per Number/Text-Entity editierbar)
CONF_START_BALANCE = "start_balance"
CONF_LEAVE_BALANCE = "leave_balance"
CONF_LEAVE_BALANCE_DATE = "leave_balance_date"
CONF_PAYOUTS = "payouts"

DYNAMIC_KEYS = {CONF_START_BALANCE, CONF_LEAVE_BALANCE, CONF_LEAVE_BALANCE_DATE, CONF_PAYOUTS}

DEFAULTS = {
    CONF_WEEKLY_HOURS: 40.0,
    CONF_WORKDAYS: ["0", "1", "2", "3", "4"],
    CONF_LEAVE_YEAR_START: "16.08.",
    CONF_CARE_ENTITLEMENT: 5,
    CONF_XMAS_EVE_FREE: False,
    CONF_HOLIDAY_FILTER: "Gesetzlicher Feiertag",
    CONF_BUILTIN_HOLIDAYS: True,
    CONF_KW_VACATION: "Urlaub",
    CONF_KW_ZA: "Zeitausgleich, ZA",
    CONF_KW_CARE: "Pflegeurlaub",
    CONF_KW_SICK: "Krankenstand, Krank",
    CONF_KW_IGNORE: "",
    CONF_SCAN_INTERVAL: 15,
    CONF_START_BALANCE: 0.0,
    CONF_LEAVE_BALANCE: 0.0,
    CONF_PAYOUTS: "",
}

SERVICE_REFRESH = "refresh"
