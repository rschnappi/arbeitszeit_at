"""Setup-Test: Config-Flow, Sensoren, Number/Text, Options-Refresh."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arbeitszeit_at.const import DOMAIN
from custom_components.arbeitszeit_at.coordinator import build_calc_config, merged_config

from test_calc import EVENTS, NOW  # noqa: E402

CAL = "calendar.arbeit"
HOL = "calendar.feiertage"
HOL_EVENTS = [
    {"start": "2026-09-24", "end": "2026-09-25", "summary": "Betriebsfeiertag", "description": "Gesetzlicher Feiertag"},
    {"start": "2026-09-25", "end": "2026-09-26", "summary": "Gedenktag X", "description": "Gedenktag"},
]


from homeassistant.core import ServiceRegistry

_orig_call = ServiceRegistry.async_call


async def _fake_call(self, domain, service, data=None, blocking=False, context=None, target=None, return_response=False):
    if (domain, service) == ("calendar", "get_events"):
        ids = target["entity_id"]
        out = {CAL: {"events": EVENTS}}
        if HOL in ids:
            out[HOL] = {"events": HOL_EVENTS}
        return out
    return await _orig_call(self, domain, service, data, blocking, context, target, return_response)


@pytest.fixture
def frozen():
    with patch("custom_components.arbeitszeit_at.coordinator.dt_util.now", return_value=NOW):
        yield


async def test_flow_and_sensors(hass: HomeAssistant, frozen) -> None:
    await hass.config.async_set_time_zone("Europe/Vienna")
    hass.states.async_set(CAL, "off", {"friendly_name": "Arbeit"})
    hass.states.async_set(HOL, "off", {"friendly_name": "Feiertage"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    with patch("homeassistant.core.ServiceRegistry.async_call", _fake_call):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {
            "calendar_entity": CAL, "holiday_calendar_entity": HOL,
            "holiday_filter": "Gesetzlicher Feiertag", "builtin_holidays": True, "weekly_hours": 40, "workdays": ["0", "1", "2", "3", "4"],
            "leave_year_start": "16.08.", "care_leave_entitlement": 5, "xmas_eve_nye_free": False,
            "start_balance": 10, "leave_balance": 25, "leave_balance_date": "2026-09-25",
            "payouts": "", "keywords_vacation": "Urlaub", "keywords_za": "Zeitausgleich, ZA",
            "keywords_care": "Pflegeurlaub", "keywords_sick": "Krankenstand", "scan_interval": 15,
        })
        assert result["type"] == "create_entry", result
        await hass.async_block_till_done()

        # 24.09. ist laut Feiertagskalender frei -> Soll −8 h, die 9,5 h zählen voll als Mehrarbeit
        assert hass.states.get("sensor.uberstunden_woche").state == "9.5"
        assert hass.states.get("sensor.arbeitszeit_monat_ist").state == "143.5"
        assert hass.states.get("sensor.uberstunden_monat").state == "23.5"
        assert hass.states.get("sensor.urlaub_rest").state == "23"
        assert hass.states.get("number.uberstunden_startsaldo").state == "10.0"

        # Text-Entity setzt Auszahlung -> nur Refresh, kein Reload
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        coord = entry.runtime_data
        await hass.services.async_call("text", "set_value",
            {"entity_id": "text.ausbezahlter_za", "value": "Mai: 20h"}, blocking=True)
        await hass.async_block_till_done()
        assert entry.runtime_data is coord  # kein Reload
        await coord.async_refresh()  # Debouncer überspringen
        await hass.async_block_till_done()
        assert hass.states.get("sensor.za_ausbezahlt_jahr").state == "20.0"
        assert hass.states.get("text.ausbezahlter_za").state == "Mai: 20h"


async def test_options_flow(hass: HomeAssistant, frozen) -> None:
    # Legacy-Entry mit dem alten hours_per_day-Feld -> prüft die Migration auf weekly_hours beim Setup.
    entry = MockConfigEntry(domain=DOMAIN, unique_id=CAL, data={
        "calendar_entity": CAL, "hours_per_day": 8, "workdays": ["0", "1", "2", "3", "4"],
        "leave_year_start": "16.08.", "care_leave_entitlement": 5, "xmas_eve_nye_free": False,
        "start_balance": 0, "leave_balance": 0, "keywords_vacation": "Urlaub",
        "keywords_za": "ZA", "keywords_care": "Pflegeurlaub", "keywords_sick": "Krank", "scan_interval": 15})
    entry.add_to_hass(hass)
    # Was das Options-Formular nach der Migration tatsächlich zeigt/submitted (weekly_hours statt hours_per_day).
    submit_base = {k: v for k, v in entry.data.items() if k != "hours_per_day"} | {"weekly_hours": 40}
    with patch("homeassistant.core.ServiceRegistry.async_call", _fake_call):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        res = await hass.config_entries.options.async_init(entry.entry_id)
        assert res["type"] == "form"
        res = await hass.config_entries.options.async_configure(res["flow_id"], {
            **submit_base, "leave_year_start": "xx"})
        assert res["errors"] == {"leave_year_start": "invalid_day_month"}
        res = await hass.config_entries.options.async_configure(res["flow_id"], {
            **submit_base, "builtin_holidays": False})
        assert res["errors"] == {"builtin_holidays": "no_holiday_source"}
        res = await hass.config_entries.options.async_configure(res["flow_id"], {
            **submit_base, "holiday_calendar_entity": HOL,
            "holiday_filter": "", "builtin_holidays": False})
        assert res["type"] == "create_entry"
        await hass.async_block_till_done()
        assert entry.options["holiday_calendar_entity"] == HOL
        assert entry.options["weekly_hours"] == 40


def test_weekly_hours_migrates_from_legacy_hours_per_day():
    entry = SimpleNamespace(
        data={"calendar_entity": CAL, "hours_per_day": 8, "workdays": ["0", "1", "2", "3", "4"],
              "leave_year_start": "16.08.", "care_leave_entitlement": 5, "xmas_eve_nye_free": False,
              "start_balance": 0, "leave_balance": 0, "keywords_vacation": "Urlaub",
              "keywords_za": "ZA", "keywords_care": "Pflegeurlaub", "keywords_sick": "Krank",
              "scan_interval": 15},
        options={},
    )
    conf = merged_config(entry)
    assert conf["weekly_hours"] == 40.0
    assert build_calc_config(conf).hours_per_day == 8.0


def test_weekly_hours_explicit_part_time():
    entry = SimpleNamespace(
        data={"calendar_entity": CAL, "weekly_hours": 20, "workdays": ["0", "1", "2", "3", "4"],
              "leave_year_start": "16.08.", "care_leave_entitlement": 5, "xmas_eve_nye_free": False,
              "start_balance": 0, "leave_balance": 0, "keywords_vacation": "Urlaub",
              "keywords_za": "ZA", "keywords_care": "Pflegeurlaub", "keywords_sick": "Krank",
              "scan_interval": 15},
        options={},
    )
    conf = merged_config(entry)
    assert conf["weekly_hours"] == 20
    assert build_calc_config(conf).hours_per_day == 4.0
