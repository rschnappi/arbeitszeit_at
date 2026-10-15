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

- **Soll** = frei eintragbare Wochenstunden (z. B. 40, 38,5, 20, 16 …) ÷ Anzahl gewählter Arbeitstage,
  angerechnet an Arbeitstagen ohne Feiertage (optional 24.12./31.12. frei)
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

## Google Calendar: fehlende Vergangenheit (ICS-URL)

Home Assistants Google-Calendar-Integration hält für `calendar.get_events` nur ein rollierendes
~90-Tage-Fenster lokal vor (`SYNC_EVENT_MIN_TIME` in HA-Core) – anschließend werden nur noch
Termine nachgeliefert, die seit dem letzten Abgleich neu angelegt oder geändert wurden. Ein
Arbeits-Termin, der z. B. im Jänner eingetragen und seither nie mehr angefasst wurde, kommt dort
nie an, egal wie lange der Google-Account schon existiert – Google selbst hat die Daten, HAs
lokaler Cache aber nicht.

Abhilfe: **Feld „ICS-URL“** in den Integrations-Einstellungen. Trägt man dort Googles direkten
iCal-Export-Link ein (Google Calendar → Kalendereinstellungen des Arbeitszeit-Kalenders →
„Geheime Adresse im iCal-Format“), liest die Integration die Termine direkt per HTTP – ohne
HAs Zwischenspeicher und dessen 90-Tage-Grenze. Der Arbeitszeit-Kalender selbst bleibt trotzdem
gewählt (bestimmt u. a. den Entry-Titel); ist die ICS-URL gesetzt, wird er für die eigentliche
Kalenderabfrage aber nicht mehr verwendet. Der Link enthält ein Geheimnis – nicht weitergeben.

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
| `sensor.abwesenheiten_jahr` | alle Abwesenheiten des Jahres mit Datum (Urlaub, ZA, Pflegeurlaub, Krankenstand) |
| `sensor.za_ausbezahlt_jahr` | ausbezahlte Stunden |
| `number.uberstunden_startsaldo` | Übertrag aus dem Vorjahr (editierbar) |
| `number.resturlaub_stand` | Resturlaub laut Lohnzettel – beim Ändern wird das Stand-Datum auf heute gesetzt |
| `text.ausbezahlter_za` | z. B. `Mai: 20h, August 16 Stunden` |

Attribute wie `monats_uebersicht`, `saldo_monatsende`, `soll_stunden_bis_heute`, `heute_offen`
sind kompatibel zu bestehenden Markdown-Karten.

### Abwesenheiten mit Datum

`sensor.urlaub_jahr`, `sensor.za_jahr`, `sensor.pflegeurlaub_jahr` und `sensor.krankenstand_jahr`
haben zusätzlich das Attribut `zeitraeume`, `sensor.abwesenheiten_jahr` fasst alle Arten in
`eintraege` zusammen (sortiert nach Datum). Jeder Eintrag ist ein zusammenhängender Zeitraum:

```yaml
- art: Urlaub
  von: "2026-08-03"
  bis: "2026-08-18"
  zeitraum: "03.08. – 18.08."
  tage: 12          # Werktage ohne Wochenenden/Feiertage, stundenweise anteilig
  stunden: "96.00"
  status: genommen  # genommen | laufend | geplant
```

Wochenenden und Feiertage unterbrechen einen Zeitraum nicht (Urlaub 05.01. + 07.01. → „05.01. – 07.01.“, 2 Tage),
ein normaler Arbeitstag dazwischen schon. Weitere Attribute von `sensor.abwesenheiten_jahr`: `summen`
(Tage je Art), `genommen_tage`, `geplant_tage` und `naechste` (nächster laufender oder geplanter Eintrag).

Beispiel für eine Markdown-Karte:

```jinja
{% for e in state_attr('sensor.abwesenheiten_jahr', 'eintraege') %}
| {{ e.art }} | {{ e.zeitraum }} | {{ e.tage }} | {{ '✅' if e.status == 'genommen' else '🗓️' }} |
{% endfor %}
```

Dienst `arbeitszeit_at.refresh` liest den Kalender sofort neu ein.

## Migration von per REST gesetzten Sensoren

Wurden `sensor.uberstunden_*` bisher per REST-API gesetzt, belegen sie die Entity-IDs bis zum
nächsten Neustart. Reihenfolge: externen Sync abschalten → HA neu starten → Integration einrichten.
Sonst entstehen IDs mit Suffix `_2`.
