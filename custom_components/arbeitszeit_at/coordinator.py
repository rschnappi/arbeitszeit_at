"""DataUpdateCoordinator: liest den Kalender und rechnet."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .calc import CalcConfig, calculate, fetch_window, holidays_from_events, parse_day_month
from .const import (
    CONF_BUILTIN_HOLIDAYS, CONF_CALENDAR, CONF_CARE_ENTITLEMENT, CONF_HOLIDAY_CALENDAR, CONF_HOLIDAY_FILTER, CONF_HOURS_PER_DAY, CONF_KW_CARE, CONF_KW_IGNORE,
    CONF_KW_SICK, CONF_KW_VACATION, CONF_KW_ZA, CONF_LEAVE_BALANCE, CONF_LEAVE_BALANCE_DATE,
    CONF_LEAVE_YEAR_START, CONF_PAYOUTS, CONF_SCAN_INTERVAL, CONF_START_BALANCE, CONF_START_DATE,
    CONF_WORKDAYS, CONF_XMAS_EVE_FREE, DEFAULTS, DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def merged_config(entry: ConfigEntry) -> dict[str, Any]:
    return {**DEFAULTS, **entry.data, **entry.options}


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def build_calc_config(conf: dict[str, Any]) -> CalcConfig:
    return CalcConfig(
        hours_per_day=float(conf[CONF_HOURS_PER_DAY]),
        workdays={int(d) for d in conf[CONF_WORKDAYS]},
        start_date=_to_date(conf.get(CONF_START_DATE)),
        leave_year_start=parse_day_month(conf[CONF_LEAVE_YEAR_START]),
        care_entitlement=float(conf[CONF_CARE_ENTITLEMENT]),
        xmas_eve_nye_free=bool(conf[CONF_XMAS_EVE_FREE]),
        kw_vacation=conf[CONF_KW_VACATION],
        kw_za=conf[CONF_KW_ZA],
        kw_care=conf[CONF_KW_CARE],
        kw_sick=conf[CONF_KW_SICK],
        kw_ignore=conf.get(CONF_KW_IGNORE) or "",
        start_balance=float(conf[CONF_START_BALANCE]),
        leave_balance=float(conf[CONF_LEAVE_BALANCE]),
        leave_balance_date=_to_date(conf.get(CONF_LEAVE_BALANCE_DATE)),
        payouts=conf.get(CONF_PAYOUTS) or "",
        builtin_holidays=bool(conf.get(CONF_BUILTIN_HOLIDAYS, True)),
    )


class ArbeitszeitCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Holt Events aus dem Kalender und berechnet alle Werte."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        conf = merged_config(entry)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=int(conf[CONF_SCAN_INTERVAL])),
        )
        self.last_options: dict[str, Any] = dict(entry.options)

    async def _async_update_data(self) -> dict[str, dict]:
        conf = merged_config(self.config_entry)
        cfg = build_calc_config(conf)
        calendar = conf[CONF_CALENDAR]
        holiday_cal = conf.get(CONF_HOLIDAY_CALENDAR) or None
        targets = [calendar] + ([holiday_cal] if holiday_cal and holiday_cal != calendar else [])
        now = dt_util.now()
        start, end = fetch_window(cfg, now.date())
        tz = dt_util.get_default_time_zone()
        try:
            resp = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "start_date_time": datetime.combine(start, time.min, tz),
                    "end_date_time": datetime.combine(end + timedelta(days=1), time.min, tz),
                },
                target={"entity_id": targets},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            raise UpdateFailed(f"Kalender {', '.join(targets)} nicht lesbar: {err}") from err

        resp = resp or {}
        events = resp.get(calendar, {}).get("events", [])
        if holiday_cal:
            cfg.extra_holidays = holidays_from_events(
                resp.get(holiday_cal, {}).get("events", []), conf.get(CONF_HOLIDAY_FILTER) or ""
            )
            _LOGGER.debug("%s: %d Feiertage", holiday_cal, len(cfg.extra_holidays))
        _LOGGER.debug("%s: %d Events von %s bis %s", calendar, len(events), start, end)
        try:
            return calculate(events, cfg, now)
        except (ValueError, KeyError) as err:
            raise UpdateFailed(f"Berechnung fehlgeschlagen: {err}") from err
