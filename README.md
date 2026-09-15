Sofatutor-Lizenz automatisch eintragen
=====================================

Dieses Skript prüft automatisch, ob eine kostenlose Sofatutor-Lizenz über die eigene Bibliothek, die im Verbund von ebibliotheken-hessen.de ist, verfügbar ist, holt sie ab
und aktiviert sie optional direkt auf sofatutor.com.


Was es macht
------------

1. Einloggen auf login.ebibliotheken-hessen.de
2. Dashboard aller Angebote auslesen (Sofatutor, Kompreno, Onilo, uTalk, etc.)
3. Wenn Sofatutor-Status = VERFUEGBAR:
   - Lizenz anfordern -> Redirect auf sofatutor.com/signup/voucher/XXXX
   - Optional: 2-Schritt-Aktivierung bei sofatutor.com (Code prüfen, Account anlegen)
4. Wenn Sofatutor-Status = AKTIV:
   - Kein Aktion; prüft erst wieder 1 Tag vor Ablauf
5. Wenn Sofatutor-Status = VOLL:
   - Kein Aktion; prüft intervalle neu


Voraussetzungen
---------------

- Python 3.10+
- pip (requirements.txt)
- Bibliotheks-Ausweis einer hessischen Bibliothek
- Optional: sofatutor.com Account (für automatische Freischaltung)
- Optional: SMTP-Zugang (für E-Mail-Benachrichtigung)


Installation
------------

    git clone https://github.com/Buchhems/st_check.git
    cd st_check
    cp .env.example .env

Dann .env ausfüllen (siehe unten).


Konfiguration (.env)
---------------------

Pflicht:

    BIB_NR=700018                     # Ausweisnummer
    BIB_PASSWORT=dein-passwort         # PIN / Passwort

Optional - Sofatutor-Freischaltung (wenn gesetzt, wird automatisch aktiviert):

    SOFATUTOR_EMAIL=deine@email.de
    SOFATUTOR_PASSWORT=dein-passwort

Optional - Intervall / Modus:

    CHECK_INTERVAL=60                  # Prüfintervall (Minuten, oder z.B. 2h, 7d)
    RUN_ONCE=false                     # true = einmal prüfen, dann beenden

Optional - E-Mail-Benachrichtigung:

    NOTIFY_EMAIL=deine@email.de
    SMTP_HOST=smtp.example.com
    SMTP_PORT=587
    SMTP_USER=deine@email.de
    SMTP_PASS=dein-smtp-passwort
    SMTP_FROM=deine@email.de


Nutzung
-------

Direkt:

    pip install -r requirements.txt
    python main.py

Einmaliger Check (z.B. als Cron-Job):

    RUN_ONCE=true python main.py

Docker
------

Voraussetzung: Docker mit Compose-Plugin (Docker Desktop, Docker Engine 24+).

### 1. .env vorbereiten

Die Zugangsdaten werden nur über die `.env`-Datei übergeben
(nie im `docker-compose.yml` fixieren – die Datei wird im Repo versioniert):

    cp .env.example .env
    # .env öffnen und BIB_NR / BIB_PASSWORT eintragen

### 2. Image bauen und starten

    docker compose up -d --build

Das startet den Container `st-check` mit `restart: unless-stopped`,
d.h. er läuft dauerhaft im Hintergrund und startet bei Neustart / Reboot
automatisch wieder.

### 3. Logs anzeigen

    docker compose logs -f

### 4. Status prüfen

    docker compose ps

### 5. Stoppen / aktualisieren

    docker compose down            # stoppen
    docker compose up -d --build   # nach einem `git pull` neu bauen + starten

### Wichtige Hinweise

- `RUN_ONCE` steht in `docker-compose.yml` auf `false` (Endlosschleife).
  Für einen einmaligen Testlauf: `RUN_ONCE=true docker compose up`.
- `CHECK_INTERVAL` kann alternativ in `.env` gesetzt werden –
  `environment`-Einträge in der Compose-Datei haben Vorrang.
- Die `.env` wird vom Container eingelesen, aber **nicht** im Image
  abgelegt – so bleiben die Credentials aus dem Build/Export.
- Der Container hat einen `HEALTHCHECK`, der alle 5 Minuten die
  Laufzeit bestätigt (Status `healthy` in `docker compose ps`).


Endlosschleife (Loop-Modus)
----------------------------

Standard (RUN_ONCE=false): Das Skript läuft dauerhaft und prüft alle
60 Minuten (oder dem angegebenen CHECK_INTERVAL).

Intelligente Planung bei aktiver Lizenz:
  - Wenn die Lizenz aktiv ist, wird bis 1 Tag vor Ablauf gewartet
  - Dadurch wird unnötiger Traffic vermieden und neue Lizenzen werden
    direkt nach Verfügbarkeit abgerufen
  - Bei VOLL-Status wird normal weitergeprüft


Ausgabe
-------

    [+] Lizenz verfügbar / Charter  (- Sofatutor: voll)  [Status: VERFUEGBAR]
    [-] Sofatutor-Status: VOLL      (kein Aktion)
    [*] Sofatutor-Status: AKTIV     (lizenz läuft bis 15.10.2026)

Beispiel-Ausgabe:

    Login OK -> https://login.ebibliotheken-hessen.de/library/dashboard

    Angebote (9):
      [+] Enote           Zur App
      [*] Kompreno        Jetzt einloggen (bis 16.09.2026)
      [-] Onilo           Jetzt einloggen (bis 15.09.2026)
      [+] Polylino        Lizenz anfordern
      [+] Riffreporter    Lizenz anfordern
      [*] Sofatutor       Jetzt einloggen (bis 15.10.2026)
      [+] Studyflix       Lizenz anfordern
      [-] Tigerbooks      Zur App
      [*] uTalk           Jetzt einloggen (bis 15.10.2026)

    Sofatutor-Status: AKTIV
    Lizenz bereits aktiv (bis 15.10.2026). Naechste Pruefung in 40655 Minuten.


Sofatutor-Aktivierung (optional)
---------------------------------

Wenn SOFATUTOR_EMAIL und SOFATUTOR_PASSWORT gesetzt sind, wird der
aktivierte Code automatisch auf sofatutor.com übertragen.

Der 2-Schritt-Flow:

  1. POST /signup/check_voucher_code (AJAX) - Code validieren
  2. POST /signup/voucher_create - Account anlegen / Zugang freischalten

Danach Redirect auf /account - Konto ist fertig eingerichtet.

Ohne diese Angaben wird nur der Lizenzcode ausgegeben; diesen kann man
dann manuell auf der sofatutor.com-Seite eingeben.


Lizenz
-------

Dieses Projekt ist unter der MIT-Lizenz verfügbar.
