#!/usr/bin/env python3
"""Boka det första lediga grupprummet i TimeEdit två dagar framåt.

Selektorer kan behöva anpassas när TimeEdit ändrar sin installation. Alla viktiga
selektorer kan därför ersättas i .env utan att koden behöver ändras.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

LOG = logging.getLogger("timeedit-booker")
DEFAULT_URL = "https://cloud.timeedit.net/liu/web/wr_stud/ri1Q8.html"


class BookingError(RuntimeError):
    """Ett förväntat fel som användaren kan åtgärda."""


class NoAvailabilityError(BookingError):
    """Inget ledigt bokningsalternativ hittades."""


@dataclass(frozen=True)
class Config:
    username: str
    password: str
    purpose: str
    url: str
    state_file: Path
    timeout_ms: int
    headless: bool
    date_input_selector: str | None
    available_selector: str | None
    purpose_selector: str | None
    book_button_selector: str | None
    success_selector: str | None

    @classmethod
    def from_env(cls, *, force_headed: bool = False) -> "Config":
        load_dotenv()
        username = os.getenv("TIMEEDIT_USERNAME", "").strip()
        password = os.getenv("TIMEEDIT_PASSWORD", "")
        if not username or not password:
            raise BookingError("TIMEEDIT_USERNAME och TIMEEDIT_PASSWORD måste finnas i .env")

        try:
            timeout_ms = int(os.getenv("TIMEEDIT_TIMEOUT_MS", "20000"))
        except ValueError as exc:
            raise BookingError("TIMEEDIT_TIMEOUT_MS måste vara ett heltal") from exc

        return cls(
            username=username,
            password=password,
            purpose=os.getenv("TIMEEDIT_PURPOSE", "Grupparbete").strip(),
            url=os.getenv("TIMEEDIT_URL", DEFAULT_URL).strip(),
            state_file=Path(os.getenv("TIMEEDIT_STATE_FILE", ".timeedit-state.json")).expanduser(),
            timeout_ms=timeout_ms,
            headless=False if force_headed else os.getenv("TIMEEDIT_HEADLESS", "true").lower() in {"1", "true", "yes"},
            date_input_selector=_optional_env("TIMEEDIT_DATE_INPUT_SELECTOR"),
            available_selector=_optional_env("TIMEEDIT_AVAILABLE_SELECTOR"),
            purpose_selector=_optional_env("TIMEEDIT_PURPOSE_SELECTOR"),
            book_button_selector=_optional_env("TIMEEDIT_BOOK_BUTTON_SELECTOR"),
            success_selector=_optional_env("TIMEEDIT_SUCCESS_SELECTOR"),
        )


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def target_date(today: date | None = None) -> date:
    """Returnera övermorgon; separat funktion gör datumlogiken testbar."""
    return (today or date.today()) + timedelta(days=2)


def first_visible(page: Page, selectors: list[str], timeout_ms: int) -> Locator:
    """Hitta första synliga elementet, med explicit väntan per kandidat."""
    per_selector = max(750, timeout_ms // max(1, len(selectors)))
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=per_selector)
            return locator
        except PlaywrightTimeoutError:
            continue
    raise BookingError(f"Inget synligt element matchade: {', '.join(selectors)}")


def looks_like_login(page: Page) -> bool:
    url = page.url.lower()
    if any(part in url for part in ("login", "signin", "saml", "oauth", "microsoftonline")):
        return True
    return page.locator('input[type="password"]').count() > 0


def assist_login(page: Page, config: Config) -> None:
    """Fyll vanliga LiU/Microsoft-fält; MFA lämnas alltid till användaren."""
    user_fields = page.locator('input[type="email"], input[name="loginfmt"], input[name="username"]')
    if user_fields.count():
        user_fields.first.fill(config.username)
        next_buttons = page.get_by_role("button", name=re.compile(r"Nästa|Next", re.I))
        if next_buttons.count():
            next_buttons.first.click()
    try:
        password = page.locator('input[type="password"]').first
        password.wait_for(state="visible", timeout=8_000)
        password.fill(config.password)
        sign_in = page.get_by_role("button", name=re.compile(r"Logga in|Sign in", re.I))
        if sign_in.count():
            sign_in.first.click()
    except PlaywrightTimeoutError:
        pass


def save_session(context: BrowserContext, state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(state_file))
    try:
        state_file.chmod(0o600)
    except OSError:
        LOG.warning("Kunde inte begränsa filrättigheterna för %s", state_file)
    LOG.info("Sessionen sparades i %s", state_file)


def establish_session(playwright: Playwright, config: Config) -> None:
    """Öppna synlig webbläsare och vänta på manuell MFA."""
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(storage_state=str(config.state_file) if config.state_file.exists() else None)
    page = context.new_page()
    page.goto(config.url, wait_until="domcontentloaded", timeout=config.timeout_ms)
    assist_login(page, config)
    print("\nSlutför inloggning och MFA i webbläsaren. Gå tillbaka till TimeEdit.")
    try:
        input("Tryck Enter här när TimeEdit-bokningssidan visas ... ")
    except EOFError as exc:
        browser.close()
        raise BookingError("Manuell MFA kräver en interaktiv terminal; kör med --setup-session") from exc
    page.goto(config.url, wait_until="domcontentloaded", timeout=config.timeout_ms)
    if looks_like_login(page):
        browser.close()
        raise BookingError("Inloggningen verkar inte vara klar; sessionen sparades inte")
    save_session(context, config.state_file)
    browser.close()


def select_date(page: Page, day: date, config: Config) -> None:
    iso = day.isoformat()
    selectors = ([config.date_input_selector] if config.date_input_selector else []) + [
        'input[type="date"]',
        'input[name*="date" i]',
        'input[aria-label*="datum" i]',
        'input[aria-label*="date" i]',
    ]
    try:
        field = first_visible(page, selectors, config.timeout_ms)
        input_type = field.get_attribute("type")
        field.fill(iso if input_type == "date" else day.strftime("%Y-%m-%d"))
        field.press("Enter")
        page.wait_for_load_state("networkidle", timeout=config.timeout_ms)
        LOG.info("Valde datum %s", iso)
        return
    except (BookingError, PlaywrightTimeoutError):
        LOG.info("Inget direkt datumfält hittades; provar kalenderknapp")

    # Reservväg för datumväljare: öppna kalendern och välj ett element vars
    # tillgängliga namn innehåller hela ISO-datumet. TIMEEDIT_DATE_INPUT_SELECTOR
    # är den säkraste lösningen om den lokala installationen skiljer sig.
    opener = first_visible(page, [
        'button[aria-label*="datum" i]', 'button[aria-label*="date" i]',
        '[role="button"][title*="datum" i]', '[role="button"][title*="date" i]',
    ], config.timeout_ms)
    opener.click()
    date_cell = first_visible(page, [
        f'[data-date="{iso}"]', f'[aria-label*="{iso}" i]', f'[title*="{iso}" i]'
    ], config.timeout_ms)
    date_cell.click()
    page.wait_for_load_state("networkidle", timeout=config.timeout_ms)


def book_first_available(page: Page, config: Config) -> None:
    candidates = ([config.available_selector] if config.available_selector else []) + [
        '[data-status="available"]',
        '[class*="available" i]:not([aria-disabled="true"])',
        '[class*="free" i]:not([aria-disabled="true"])',
        'button:has-text("Ledig")',
        'button:has-text("Available")',
    ]
    try:
        available = first_visible(page, candidates, config.timeout_ms)
    except BookingError as exc:
        raise NoAvailabilityError(
            "Inga lediga rum/tider hittades. Ange vid behov TIMEEDIT_AVAILABLE_SELECTOR i .env."
        ) from exc
    available.click()

    purpose_selectors = ([config.purpose_selector] if config.purpose_selector else []) + [
        'textarea[name*="purpose" i]', 'input[name*="purpose" i]',
        'textarea[aria-label*="syfte" i]', 'input[aria-label*="beskriv" i]',
        'textarea',
    ]
    try:
        purpose = first_visible(page, purpose_selectors, 5_000)
        purpose.fill(config.purpose)
    except BookingError:
        LOG.info("Formuläret efterfrågade inget syfte/beskrivning")

    book_selectors = ([config.book_button_selector] if config.book_button_selector else []) + [
        'button:has-text("Boka")', 'button:has-text("Book")',
        'input[type="submit"][value*="Boka" i]', 'input[type="submit"][value*="Book" i]',
    ]
    button = first_visible(page, book_selectors, config.timeout_ms)
    if button.is_disabled():
        raise BookingError("Boka-knappen är inaktiverad; kontrollera obligatoriska fält")
    button.click()

    success_selectors = ([config.success_selector] if config.success_selector else []) + [
        'text=/bokningen är (klar|bekräftad)/i', 'text=/booking (complete|confirmed)/i',
        '[class*="success" i]', '[role="status"]',
    ]
    try:
        first_visible(page, success_selectors, config.timeout_ms)
    except BookingError as exc:
        raise BookingError(
            "Klickade på Boka men kunde inte verifiera bekräftelsen. Kontrollera TimeEdit manuellt."
        ) from exc


def run_booking(playwright: Playwright, config: Config) -> None:
    if not config.state_file.exists():
        LOG.info("Ingen sparad session hittades; startar interaktiv inloggning")
        establish_session(playwright, config)

    browser: Browser = playwright.chromium.launch(headless=config.headless)
    context = browser.new_context(storage_state=str(config.state_file))
    page = context.new_page()
    try:
        page.goto(config.url, wait_until="domcontentloaded", timeout=config.timeout_ms)
        if looks_like_login(page):
            # Kravet är att en saknad/utgången session skall ge användaren en
            # synlig möjlighet att göra MFA. I en cron-körning utan terminal
            # avslutas detta tydligt av EOF-hanteringen i establish_session.
            LOG.warning("Den sparade sessionen har gått ut; öppnar interaktiv inloggning")
            browser.close()
            establish_session(playwright, config)
            run_booking(playwright, config)
            return
        day = target_date()
        select_date(page, day, config)
        book_first_available(page, config)
        save_session(context, config.state_file)
        LOG.info("Bokningen för %s är bekräftad", day.isoformat())
    finally:
        browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup-session", action="store_true", help="logga in/MFA och spara session utan att boka")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    try:
        config = Config.from_env(force_headed=args.setup_session)
        with sync_playwright() as playwright:
            if args.setup_session:
                establish_session(playwright, config)
            else:
                run_booking(playwright, config)
        return 0
    except NoAvailabilityError as exc:
        LOG.warning("Ingen bokning gjordes: %s", exc)
        return 2
    except (BookingError, PlaywrightTimeoutError) as exc:
        LOG.error("Bokningen misslyckades: %s", exc)
        return 1
    except Exception:
        LOG.exception("Oväntat fel")
        return 1


if __name__ == "__main__":
    sys.exit(main())
