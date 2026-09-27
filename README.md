# Arbeitszeit & Überstunden (AT)

Home-Assistant-Integration, die Überstunden, Urlaub, Zeitausgleich, Pflegeurlaub und
Krankenstand **direkt aus einem Kalender** berechnet (z. B. Google Calendar, CalDAV, Local Calendar).
Feiertage kommen aus einem wählbaren Feiertags-Kalender und/oder den eingebauten
österreichischen Feiertagen – keine externen Abhängigkeiten.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=rschnappi&repository=arbeitszeit_at&category=integration)

## Wie gerechnet wird

| Kalendereintrag | Wirkung |
|---|---|
| Zeitgebundener Termin (z. B. „Arbeit“ 07:00–12:00) | Arbeitszeit (Ist). Überlappungen werden zusammengeführt, laufende Termine bis „jetzt“ gezählt. |
| Ganztägig „Urlaub“ | Urlaubstag, Tages-Soll wird angerechnet |
| Ganztägig „Zeitausgleich“ / „ZA“ | ZA-Tag, Tages-Soll wird angerechnet (Brutto), im Netto-Saldo abgezogen |
| Ganztägig „Pflegeurlaub“ | Pflegeurlaubstag, angerechnet |
| Ganztägig „Krankenstand“ | Krankenstandstag, angerechnet |
| Zeitgebundene Abwesenheit (z. B. „Zeitausgleich“ 12–16 Uhr) | zählt mit ihrer Dauer |
| Andere Ganztages-Termine, Wochenenden, Feiertage | werden ignoriert |

- **Soll** = Sollstunden/Tag an Arbeitstagen ohne Feiertage (optional 24.12./31.12. frei)
- **Überstunden brutto** = Ist + Urlaub + ZA + Pflege + Krank − Soll
- **Saldo netto** = Brutto + Startsaldo − verbrauchter ZA − ausbezahlte Stunden
- **Resturlaub** = Stand laut Lohnzettel − Urlaubstage nach dem Stand-Datum (inkl. bereits eingetragener künftiger)

Schlüsselwörter sind konfigurierbar (kommagetrennt, Wortanfang; Kürzel ≤ 3 Zeichen nur als ganzes Wort – „ZA“ matcht nicht „Zahnarzt“).

## Feiertage

In den Einstellungen wählbar, beide Quellen lassen sich kombinieren:

- **Feiertags-Kalender** – jeder ganztägige Termin gilt als arbeitsfrei. Mit dem **Feiertags-Filter**
  (Standard `Gesetzlicher Feiertag`) muss ein Begriff in Titel oder Beschreibung vorkommen. Damit
  blendet man beim Google-Kalender „Feiertage in Österreich“ Gedenktage wie Allerseelen oder
  „Ende der Sommerzeit“ aus. Eigene Kalender (z. B. Betriebsurlaub, Fenstertage) mit leerem Filter nutzen.
- **Eingebaute Feiertage (AT, bundesweit)** – Neujahr, Hl. Drei Könige, Ostermontag, Staatsfeiertag,
  Christi Himmelfahrt, Pfingstmontag, Fronleichnam, Mariä Himmelfahrt, Nationalfeiertag, Allerheiligen,
  Mariä Empfängnis, Christtag, Stefanitag. Empfohlen als Ergänzung: Google-Feiertagskalender liefern
  oft nur einen begrenzten Zeitraum, vergangene Monate fehlen dann.

## Installation

**HACS:** HACS → ⋮ → *Benutzerdefinierte Repositories* → `https://github.com/rschnappi/arbeitszeit_at`, Kategorie *Integration* → installieren → HA neu starten.
**Manuell:** `custom_components/arbeitszeit_at` nach `/config/custom_components/` kopieren, neu starten.

Dann *Einstellungen → Geräte & Dienste → Integration hinzufügen → Arbeitszeit & Überstunden (AT)*,
Arbeitszeit-Kalender und optional Feiertags-Kalender wählen. Alles ist später unter *Konfigurieren* änderbar.

## Entities

| Entity | Inhalt |
|---|---|
| `sensor.uberstunden_woche` / `_monat` / `_jahr` | Saldo bis heute (h), Details als Attribute |
| `sensor.uberstunden_saldo_netto` | Netto-Saldo inkl. Startsaldo, ZA, Auszahlung |
| `sensor.arbeitszeit_woche_ist` / `_monat_ist` / `_jahr_ist` | gearbeitete Stunden |
| `sensor.urlaub_rest`, `sensor.urlaub_jahr`, `sensor.urlaub_monat` | Urlaub |
| `sensor.za_jahr`, `sensor.pflegeurlaub_jahr`, `sensor.krankenstand_jahr` | Abwesenheiten mit Tagesliste |
| `sensor.za_ausbezahlt_jahr` | ausbezahlte Stunden |
| `number.uberstunden_startsaldo` | Übertrag aus dem Vorjahr (editierbar) |
| `number.resturlaub_stand` | Resturlaub laut Lohnzettel – beim Ändern wird das Stand-Datum auf heute gesetzt |
| `text.ausbezahlter_za` | z. B. `Mai: 20h, August 16 Stunden` |

Attribute wie `monats_uebersicht`, `saldo_monatsende`, `soll_stunden_bis_heute`, `heute_offen`
sind kompatibel zu bestehenden Markdown-Karten.

Dienst `arbeitszeit_at.refresh` liest den Kalender sofort neu ein.

## Migration von per REST gesetzten Sensoren

Wurden `sensor.uberstunden_*` bisher per REST-API gesetzt, belegen sie die Entity-IDs bis zum
nächsten Neustart. Reihenfolge: externen Sync abschalten → HA neu starten → Integration einrichten.
Sonst entstehen IDs mit Suffix `_2`.
