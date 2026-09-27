"""Config- und Options-Flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector as sel

from .calc import parse_day_month
from .const import (
    CONF_BUILTIN_HOLIDAYS, CONF_CALENDAR, CONF_CARE_ENTITLEMENT, CONF_HOLIDAY_CALENDAR, CONF_HOLIDAY_FILTER, CONF_HOURS_PER_DAY, CONF_ICS_URL, CONF_KW_CARE, CONF_KW_IGNORE,
    CONF_KW_SICK, CONF_KW_VACATION, CONF_KW_ZA, CONF_LEAVE_BALANCE, CONF_LEAVE_BALANCE_DATE,
    CONF_LEAVE_YEAR_START, CONF_PAYOUTS, CONF_SCAN_INTERVAL, CONF_START_BALANCE, CONF_START_DATE,
    CONF_WORKDAYS, CONF_XMAS_EVE_FREE, DEFAULTS, DOMAIN,
)

WEEKDAYS = [
    sel.SelectOptionDict(value=str(i), label=n)
    for i, n in enumerate(["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"])
]


def _num(vmin: float, vmax: float, step: float, unit: str | None = None) -> sel.NumberSelector:
    return sel.NumberSelector(sel.NumberSelectorConfig(
        min=vmin, max=vmax, step=step, mode=sel.NumberSelectorMode.BOX, unit_of_measurement=unit))


def _schema(d: dict[str, Any]) -> vol.Schema:
    def opt(key: str) -> vol.Optional:
        val = d.get(key, DEFAULTS.get(key))
        return vol.Optional(key, description={"suggested_value": val}) if val not in (None, "") \
            else vol.Optional(key)

    return vol.Schema({
        vol.Required(CONF_CALENDAR, default=d.get(CONF_CALENDAR, vol.UNDEFINED)):
            sel.EntitySelector(sel.EntitySelectorConfig(domain="calendar")),
        opt(CONF_ICS_URL): sel.TextSelector(sel.TextSelectorConfig(type=sel.TextSelectorType.PASSWORD)),
        opt(CONF_HOLIDAY_CALENDAR): sel.EntitySelector(sel.EntitySelectorConfig(domain="calendar")),
        opt(CONF_HOLIDAY_FILTER): sel.TextSelector(),
        vol.Required(CONF_BUILTIN_HOLIDAYS, default=d.get(CONF_BUILTIN_HOLIDAYS, True)): sel.BooleanSelector(),
        vol.Required(CONF_HOURS_PER_DAY, default=d.get(CONF_HOURS_PER_DAY, 8.0)): _num(0.5, 24, 0.25, "h"),
        vol.Required(CONF_WORKDAYS, default=d.get(CONF_WORKDAYS, DEFAULTS[CONF_WORKDAYS])):
            sel.SelectSelector(sel.SelectSelectorConfig(options=WEEKDAYS, multiple=True)),
        opt(CONF_START_DATE): sel.DateSelector(),
        vol.Required(CONF_LEAVE_YEAR_START, default=d.get(CONF_LEAVE_YEAR_START, "16.08.")): sel.TextSelector(),
        vol.Required(CONF_CARE_ENTITLEMENT, default=d.get(CONF_CARE_ENTITLEMENT, 5)): _num(0, 30, 0.5, "Tage"),
        vol.Required(CONF_XMAS_EVE_FREE, default=d.get(CONF_XMAS_EVE_FREE, False)): sel.BooleanSelector(),
        vol.Required(CONF_START_BALANCE, default=d.get(CONF_START_BALANCE, 0.0)): _num(-1000, 1000, 0.25, "h"),
        vol.Required(CONF_LEAVE_BALANCE, default=d.get(CONF_LEAVE_BALANCE, 0.0)): _num(0, 365, 0.5, "Tage"),
        opt(CONF_LEAVE_BALANCE_DATE): sel.DateSelector(),
        opt(CONF_PAYOUTS): sel.TextSelector(),
        vol.Required(CONF_KW_VACATION, default=d.get(CONF_KW_VACATION, DEFAULTS[CONF_KW_VACATION])): sel.TextSelector(),
        vol.Required(CONF_KW_ZA, default=d.get(CONF_KW_ZA, DEFAULTS[CONF_KW_ZA])): sel.TextSelector(),
        vol.Required(CONF_KW_CARE, default=d.get(CONF_KW_CARE, DEFAULTS[CONF_KW_CARE])): sel.TextSelector(),
        vol.Required(CONF_KW_SICK, default=d.get(CONF_KW_SICK, DEFAULTS[CONF_KW_SICK])): sel.TextSelector(),
        opt(CONF_KW_IGNORE): sel.TextSelector(),
        vol.Required(CONF_SCAN_INTERVAL, default=d.get(CONF_SCAN_INTERVAL, 15)): _num(1, 240, 1, "min"),
    })


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    try:
        parse_day_month(user_input[CONF_LEAVE_YEAR_START])
    except ValueError:
        errors[CONF_LEAVE_YEAR_START] = "invalid_day_month"
    if not user_input.get(CONF_HOLIDAY_CALENDAR) and not user_input.get(CONF_BUILTIN_HOLIDAYS, True):
        errors[CONF_BUILTIN_HOLIDAYS] = "no_holiday_source"
    if user_input.get(CONF_HOLIDAY_CALENDAR) and user_input.get(CONF_HOLIDAY_CALENDAR) == user_input.get(CONF_CALENDAR):
        errors[CONF_HOLIDAY_CALENDAR] = "same_calendar"
    if not user_input.get(CONF_WORKDAYS):
        errors[CONF_WORKDAYS] = "no_workdays"
    return errors


class ArbeitszeitConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_CALENDAR])
                self._abort_if_unique_id_configured()
                state = self.hass.states.get(user_input[CONF_CALENDAR])
                title = f"Arbeitszeit ({state.name if state else user_input[CONF_CALENDAR]})"
                return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema(user_input or {}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ArbeitszeitOptionsFlow()


class ArbeitszeitOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = {**DEFAULTS, **self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                # leere optionale Felder explizit leeren
                for key in (CONF_START_DATE, CONF_LEAVE_BALANCE_DATE, CONF_PAYOUTS, CONF_KW_IGNORE,
                            CONF_HOLIDAY_CALENDAR, CONF_HOLIDAY_FILTER, CONF_ICS_URL):
                    user_input.setdefault(key, "")
                return self.async_create_entry(data=user_input)
            current.update(user_input)
        return self.async_show_form(step_id="init", data_schema=_schema(current), errors=errors)
