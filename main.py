"""
Sofatutor-Lizenz automatisch abrufen (Stadtbibliothek Darmstadt / ebibliotheken-hessen).

Erkennt, ob die Lizenz verfügbar ist und holt sie ab. Bei einem request-gestützten
Sofatutor-Angebot geht der Flow so:

  1. POST  /library/login            → Session-Cookie setzen
  2. GET   /library/dashboard        → Status aller Angebote lesen
  3. GET   /library/offer/sofatutor  → Redirect zu sofatutor.com/signup/voucher/XXXX

Optional (wenn SOFATUTOR_EMAIL gesetzt):
  4. POST  /signup/check_voucher_code → Code validieren (Schritt 1)
  5. POST  /signup/voucher_create    → Account anlegen (Schritt 2 "Zugang freischalten")

Die Lizenz wird NICHT abgerufen, wenn der Status "VOLL" ist. Im loop-Modus
(RUN_ONCE=false): bei einer aktiven Lizenz wird bis 1 Tag vor Ablauf pausiert
und erst dann erneut geprüft; ansonsten alle CHECK_INTERVAL Minuten
(default 60, erlaubt z.B. "90m", "2h", "7d").

In der Ausgabe wird zudem immer der Status aller Angebote gedruckt, damit
man den Überblick behält (ONILO: aktiv, SOFATUTOR: voll, …).
"""

import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://login.ebibliotheken-hessen.de"
LOGIN_URL = f"{BASE_URL}/library/login"
DASHBOARD_URL = f"{BASE_URL}/library/dashboard"
OFFER_URL = f"{BASE_URL}/library/offer"   # /{slug} appended per-request
SOFA_SLUG = "sofatutor"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_env(path=".env"):
    """Minimal .env reader (no dependency)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def parse_interval(value):
    """Parse a duration like '60', '90m', '2h' or '7d' into minutes."""
    value = value.strip().lower()
    m = re.fullmatch(r"(\d+)\s*(m|min|h|d)?", value)
    if not m:
        sys.exit(f"Ungueltiger Interval-Wert: {value!r} (erlaubt: 60, 90m, 2h, 7d)")
    amount, unit = int(m.group(1)), m.group(2)
    if unit in ("h",):
        return amount * 60
    if unit in ("d",):
        return amount * 60 * 24
    return amount  # default: minutes


def read_config():
    cfg = {
        "library_id": os.environ.get("BIB_LIBRARY_ID", "9"),
        "card_number": os.environ.get("BIB_NR", "").strip(),
        "password": os.environ.get("BIB_PASSWORT", "").strip(),
        # CHECK_INTERVAL hat Vorrang, Fallback: CHECK_INTERVAL_MIN, sonst 60m
        "check_interval": parse_interval(
            os.environ.get("CHECK_INTERVAL", os.environ.get("CHECK_INTERVAL_MIN", "60"))
        ),
        "run_once": os.environ.get("RUN_ONCE", "false").lower() in ("1", "true", "yes"),
        "notify_email": os.environ.get("NOTIFY_EMAIL", "").strip(),
        "smtp_host": os.environ.get("SMTP_HOST", "").strip(),
        "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
        "smtp_user": os.environ.get("SMTP_USER", "").strip(),
        "smtp_pass": os.environ.get("SMTP_PASS", "").strip(),
        "smtp_from": os.environ.get("SMTP_FROM", "").strip(),
        # Optional: Sofatutor-Zugangsdaten fuer automatische Aktivierung
        "sofatutor_email": os.environ.get("SOFATUTOR_EMAIL", "").strip(),
        "sofatutor_pass": os.environ.get("SOFATUTOR_PASSWORT", "").strip(),
    }
    missing = [k for k in ("card_number", "password") if not cfg[k]]
    if missing:
        sys.exit(f"Fehlende Variablen: {', '.join(missing)}. .env pruefen.")
    return cfg


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

def _parse_offer_card(card):
    """Extract state, title, slug, badge text from an offer card <div>."""
    badge_div = card.find("div", style=re.compile(r"background:"))
    badge_text = badge_div.get_text(strip=True).upper() if badge_div else None

    # Determine state from badge color or text
    badge_style = badge_div.get("style", "") if badge_div else ""
    if badge_text is None or "VOLL" in badge_text:
        state = "voll"
    elif badge_style and "#22c55e" in badge_style:
        state = "aktiv"
    elif "VERFÜGBAR" in badge_text:
        state = "verfuegbar"
    elif "AKTIV" in badge_text:
        state = "aktiv"
    else:
        state = badge_text.lower() if badge_text else "unbekannt"

    title_el = card.find("h3")
    title = title_el.get_text(strip=True) if title_el else ""

    # Extract link href (may be absent when VOLL)
    link = card.find("a", href=True)
    slug = ""
    link_label = ""
    if link:
        href = link["href"]
        # Offer links look like: /library/offer/{slug}
        m = re.search(r"/library/offer/([a-z0-9_-]+)$", href, re.IGNORECASE)
        if m:
            slug = m.group(1)
        link_label = link.get_text(strip=True)

    # Expiry date ("Gültig bis …")
    expiry = ""
    for span in card.find_all("p", class_="text-xs"):
        t = span.get_text(strip=True)
        m2 = re.search(r"Gültig bis\s+(\d{2}\.\d{2}\.\d{4})", t)
        if m2:
            expiry = m2.group(1)

    # Reminder-active indicator
    reminder = bool(card.find(string=re.compile(r"Erinnerung aktiv")))

    return {
        "title": title,
        "state": state,
        "badge": badge_text,
        "slug": slug,
        "link_label": link_label,
        "expiry": expiry,
        "reminder": reminder,
    }


def parse_dashboard(html):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("div", attrs={"data-offer-id": True})
    offers = [_parse_offer_card(c) for c in cards]
    return offers


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def send_email(cfg, subject, body):
    if not cfg["smtp_host"] or not cfg["notify_email"]:
        return False
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg["smtp_from"] or cfg["smtp_user"]
    msg["To"] = cfg["notify_email"]
    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as srv:
            srv.starttls()
            if cfg["smtp_user"]:
                srv.login(cfg["smtp_user"], cfg["smtp_pass"])
            srv.send_message(msg)
        print(f"  -> E-Mail gesendet an {cfg['notify_email']}")
        return True
    except Exception as exc:
        print(f"  -> E-Mail fehlgeschlagen: {exc}")
        return False


# ---------------------------------------------------------------------------
# Core flow
# ---------------------------------------------------------------------------

def login(session, cfg):
    payload = {
        "library_id": cfg["library_id"],
        "card_number": cfg["card_number"],
        "password": cfg["password"],
    }
    resp = session.post(LOGIN_URL, data=payload, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()

    # Check for obvious login errors
    text = resp.text.lower()
    error_indicators = [
        "ungültiges passwort", "passwort falsch", "nicht gefunden",
        "anmeldung fehlgeschlagen", "falsche ausweisnummer",
    ]
    if any(msg in text for msg in error_indicators):
        raise RuntimeError("Login fehlgeschlagen – Zugangsdaten prüfen.")

    if "/library/dashboard" not in resp.url and "login" in resp.url:
        raise RuntimeError("Login fehlgeschlagen – keine Weiterleitung zum Dashboard.")

    print(f"Login OK -> {resp.url}")


def fetch_dashboard(session):
    resp = session.get(DASHBOARD_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def request_license(session, slug):
    url = f"{OFFER_URL}/{slug}"
    resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.url, resp.text


def extract_license_code(page_html):
    """Try to extract a license code / activation URL from the offer page."""
    soup = BeautifulSoup(page_html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Pattern 1: explicit code in a copyable field
    code_el = soup.find("input", attrs={"readonly": True})
    if code_el and code_el.get("value"):
        return code_el["value"]

    # Pattern 2: "Lizenzcode: <code>"
    m = re.search(r"Lizenz(?:code|key)[\s:]+([A-Za-z0-9\-_]{4,})", text)
    if m:
        return m.group(1)

    # Pattern 3: "Weiter zu Sofatutor" link (the activation URL)
    link = soup.find("a", string=re.compile(r"Weiter zu Sofatutor", re.IGNORECASE))
    if link and link.get("href"):
        return link["href"]

    # Pattern 4: any external link to sofatutor.com that looks like an
    # activation URL (nav links like /wirkung must NOT be returned)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "sofatutor.com" in href and re.search(r"signup|voucher|code=|activate", href, re.IGNORECASE):
            return href

    return None


# ---------------------------------------------------------------------------
# Sofatutor-Aktivierung (optional, nur wenn SOFATUTOR_EMAIL gesetzt)
# ---------------------------------------------------------------------------

def _extract_csrf(soup):
    """Extract the CSRF authenticity_token from meta tag or form hidden input."""
    meta = soup.find("meta", attrs={"name": "csrf-token"})
    if meta and meta.get("content"):
        return meta["content"]
    inp = soup.find("input", attrs={"name": "authenticity_token"})
    if inp:
        return inp.get("value")
    return None


def activate_sofatutor(cfg, code, extra_session):
    """
    2-Schritt-Flow auf sofatutor.com:
      Schritt 1: POST /signup/check_voucher_code  (Voucher freischalten)
      Schritt 2: POST /signup/voucher_create      (Account anlegen)
    Liefert (True/False/None, Beschreibung).
    """
    email = cfg["sofatutor_email"]
    password = cfg["sofatutor_pass"]
    if not email or not password:
        return None, "Sofatutor-Daten fehlen – nur Lizenzcode ausgegeben."

    # URL zusammenbauen
    if code.startswith("http"):
        voucher_url = code
    else:
        voucher_url = f"https://www.sofatutor.com/signup/voucher/{code}"

    # Voucher-Code aus URL extrahieren
    m = re.search(r"/signup/voucher/([A-Za-z0-9_-]+)", voucher_url)
    voucher_code = m.group(1) if m else code

    headers = {"User-Agent": USER_AGENT}

    # --- GET: Cookies + CSRF-Token holen ---
    try:
        resp = extra_session.get(voucher_url, headers=headers, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        return False, f"Voucher-Seite konnte nicht geöffnet werden: {exc}"

    soup = BeautifulSoup(resp.text, "html.parser")
    csrf = _extract_csrf(soup)
    if not csrf:
        return False, "Kein CSRF-Token auf der Voucher-Seite gefunden."

    # --- Schritt 1: Code prüfen (AJAX) ---
    step1_data = {
        "authenticity_token": csrf,
        "voucher_code": voucher_code,
        "user_email": "",
    }
    try:
        r1 = extra_session.post(
            "https://www.sofatutor.com/signup/check_voucher_code",
            data=step1_data,
            headers={**headers, "X-Requested-With": "XMLHttpRequest"},
            timeout=30,
        )
        r1.raise_for_status()
    except Exception as exc:
        return False, f"Schritt 1 (Code prüfen) fehlgeschlagen: {exc}"

    if "is-second-step" not in r1.text and "signup_form" not in r1.text:
        # Fehlercode oder bereits verwendeter Voucher
        return False, (
            f"Schritt 1: Voucher '{voucher_code}' wurde nicht akzeptiert. "
            f"Antwort: {r1.text[:300]}"
        )

    # --- Schritt 2: Account anlegen ---
    step2_data = {
        "authenticity_token": csrf,
        "user[voucher_code]": voucher_code,
        "user[email]": email,
        "user[password]": password,
        "commit": "Zugang freischalten",
    }
    try:
        r2 = extra_session.post(
            "https://www.sofatutor.com/signup/voucher_create",
            data=step2_data,
            headers=headers,
            timeout=30,
            allow_redirects=True,
        )
    except Exception as exc:
        return False, f"Schritt 2 (Account anlegen) fehlgeschlagen: {exc}"

    page_text = BeautifulSoup(r2.text, "html.parser").get_text(" ", strip=True)

    # Robuster Erfolg: Redirect in den angemeldeten /account-Bereich
    if "/account" in r2.url or "/mein" in r2.url.lower():
        return True, f"Sofatutor-Zugang für {email} freigeschaltet (URL {voucher_url})."

    success_pat = re.compile(r"freigeschaltet|herzlich willkommen|erfolgreich angemeldet", re.IGNORECASE)
    error_pat = re.compile(r"fehler|ungültig|ungueltig|bereits verwendet|schon verwendet|existiert bereits", re.IGNORECASE)

    if success_pat.search(page_text):
        return True, f"Sofatutor-Zugang für {email} freigeschaltet (URL {voucher_url})."

    if error_pat.search(page_text):
        return False, f"Freischaltung meldet Fehler ({r2.url}): {page_text[:300]}"

    return False, (
        f"Freischaltung unbestätigt (kein Erfolg-/Fehlertext erkannt). "
        f"Ergebnis siehe Antworttext oben (URL {r2.url})."
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _parse_expiry(expiry_str):
    """Parse '15.10.2026' to datetime, or return None."""
    if not expiry_str:
        return None
    try:
        return datetime.strptime(expiry_str, "%d.%m.%Y")
    except ValueError:
        return None


def print_status(offers):
    for o in offers:
        icon = {"verfuegbar": "+", "voll": "-", "aktiv": "*"}.get(o["state"], "?")
        label = o["link_label"] or o["badge"] or o["state"]
        exp = f" (bis {o['expiry']})" if o["expiry"] else ""
        print(f"  [{icon}] {o['title']:<15} {label}{exp}")


def run_once(session, cfg):
    dashboard_html = fetch_dashboard(session)
    offers = parse_dashboard(dashboard_html)
    print(f"\nAngebote ({len(offers)}):")
    print_status(offers)

    sf = next((o for o in offers if o["slug"] == SOFA_SLUG or o["title"].lower() == "sofatutor"), None)
    if sf is None:
        print("\nSofatutor-Karte nicht im Dashboard gefunden.")
        return "fehlt", cfg["check_interval"]

    state = sf["state"]
    print(f"\nSofatutor-Status: {state.upper()}")

    if state == "aktiv":
        expiry_dt = _parse_expiry(sf["expiry"])
        if expiry_dt:
            remaining = (expiry_dt - datetime.now()).total_seconds() / 60
            if remaining > 0:
                # 1 Tag vor Ablauf erneut prüfen (mindestens CHECK_INTERVAL)
                wait = max(remaining - 60 * 24, cfg["check_interval"])
                print(f"Lizenz bereits aktiv (bis {sf['expiry']}). "
                      f"Naechste Pruefung in {int(wait)} Minuten.")
                return "aktiv", int(wait)
        print(f"Lizenz bereits aktiv (bis {sf['expiry'] or 'unbekannt'}). "
              f"Naechste Pruefung in {cfg['check_interval']} Minuten.")
        return "aktiv", cfg["check_interval"]

    if state == "voll":
        print("Keine Lizenz verfügbar (VOLL).")
        return "voll", cfg["check_interval"]

    if state == "verfuegbar":
        print(f"Lizenz verfuegbar! Fordere an -> {OFFER_URL}/{sf['slug']}")
        try:
            target_url, offer_page = request_license(session, sf["slug"])
            print(f"  Redirect-Ziel: {target_url}")

            code = extract_license_code(offer_page)
            if "sofatutor.com" in target_url:
                code = target_url
                print(f"  Redirect auf sofatutor.com nutzbar als Zugang: {code}")
            if code:
                print(f"  Lizenzcode / Aktivierungs-URL: {code}")
            else:
                print("  Kein Code extrahiert – Seite enthält ggf. ein JS-Widget oder Redirect.")

            if cfg["sofatutor_email"] and cfg["sofatutor_pass"]:
                print("  Sofatutor-Daten vorhanden -> versuche automatische Freischaltung ...")
                activated, message = activate_sofatutor(cfg, code, session)
                print(f"  Aktivierung: {message}")
                send_email(
                    cfg,
                    "Sofatutor-Lizenz verarbeitet",
                    f"Die Sofatutor-Lizenz wurde abgerufen.\n\n"
                    f"Lizenzcode / Aktivierungs-URL:\n{code}\n\n"
                    f"Automatische Freischaltung: {'OK' if activated else 'NICHT OK / manuell prüfen'}\n"
                    f"Detail: {message}",
                )
            elif code:
                send_email(
                    cfg,
                    "Sofatutor-Lizenz erfolgreich abgerufen",
                    "Die Sofatutor-Lizenz wurde erfolgreich aktiviert.\n\n"
                    f"Lizenzcode / Aktivierungs-URL:\n{code}\n\n"
                    "Registrieren Sie sich mit diesem Zugang auf sofatutor.com\n"
                    "(falls noch nicht geschehen).",
                )
            else:
                print("  Keine E-Mail: kein Lizenzcode extrahiert.")
            return "erfolgreich", cfg["check_interval"]
        except Exception as exc:
            print(f"  Lizenzanforderung fehlgeschlagen: {exc}")
            return "fehlgeschlagen", cfg["check_interval"]

    print(f"Unbekannter Status: {state}")
    return "unbekannt", cfg["check_interval"]


def main():
    load_env()
    cfg = read_config()

    print(f"BIB_LIBRARY_ID={cfg['library_id']}")
    print(f"BIB_NR={cfg['card_number']}")
    print(f"RUN_ONCE={cfg['run_once']}")
    print(f"CHECK_INTERVAL={cfg['check_interval']} min")

    session = requests.Session()
    login(session, cfg)

    while True:
        try:
            result, next_check = run_once(session, cfg)
        except Exception as exc:
            print(f"\nFehler: {exc}")
            result, next_check = "fehler", cfg["check_interval"]

        if cfg["run_once"]:
            sys.exit(0 if result in ("aktiv", "erfolgreich") else 1)

        # Loop mode: wait and re-login (session may expire after 2 hours)
        print(f"\nNaechster Versuch in {next_check} Minuten ...")
        time.sleep(next_check * 60)
        try:
            login(session, cfg)
        except Exception as exc:
            print(f"Re-Login fehlgeschlagen: {exc}")


if __name__ == "__main__":
    main()
