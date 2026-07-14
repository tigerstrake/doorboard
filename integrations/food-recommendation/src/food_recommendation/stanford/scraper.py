"""Scraper for the Stanford R&DE dining-hall menu site.

The menu site is a classic ASP.NET WebForms app: an initial GET yields the form
state (``__VIEWSTATE`` and friends) plus the location/day/meal dropdowns, and a
POST per hall returns that hall's menu. We use ``httpx`` (the house HTTP client)
with bounded, streamed reads and no redirects, and BeautifulSoup with the stdlib
``html.parser`` backend so no ``lxml`` dependency is needed.

All network calls run in the wallboard-worker (NUC) daily job — never on the
door critical path. On failure the scraper raises ``RuntimeError`` so the job's
yesterday-cache fallback engages.
"""

from __future__ import annotations

import logging
import time

import httpx
from bs4 import BeautifulSoup, Tag

from .models import DiningHallMenu, MenuItem

logger = logging.getLogger("doorboard.food_recommendation.stanford.scraper")

MENU_URL = "https://rdeapps.stanford.edu/dininghallmenu/Menu.aspx"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_HIDDEN_FIELDS = (
    "__VIEWSTATE",
    "__VIEWSTATEGENERATOR",
    "__EVENTVALIDATION",
    "__EVENTTARGET",
    "__EVENTARGUMENT",
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _extract_hidden_fields(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in _HIDDEN_FIELDS:
        tag = soup.find("input", {"name": name})
        if isinstance(tag, Tag):
            value = tag.get("value", "")
            fields[name] = value if isinstance(value, str) else ""
    return fields


def _options(soup: BeautifulSoup, select_id: str) -> list[Tag]:
    select = soup.find("select", {"id": select_id})
    if not isinstance(select, Tag):
        return []
    return [opt for opt in select.find_all("option") if isinstance(opt, Tag)]


def _option_value(opt: Tag) -> str:
    value = opt.get("value", "")
    return value.strip() if isinstance(value, str) else ""


def _extract_locations(soup: BeautifulSoup) -> list[tuple[str, str]]:
    return [
        (_option_value(opt), opt.get_text(strip=True))
        for opt in _options(soup, "MainContent_lstLocations")
        if _option_value(opt)
    ]


def _extract_day_options(soup: BeautifulSoup) -> list[str]:
    return [v for opt in _options(soup, "MainContent_lstDay") if (v := _option_value(opt))]


def _extract_meal_options(soup: BeautifulSoup) -> list[str]:
    return [v for opt in _options(soup, "MainContent_lstMealType") if (v := _option_value(opt))]


def _section_text(tag: Tag, section_class: str) -> str:
    """Return a label's text with its bold section-name prefix removed."""
    copy = _soup(str(tag)).find(tag.name)
    if not isinstance(copy, Tag):
        return ""
    for section_span in copy.find_all(class_=section_class):
        section_span.extract()
    return copy.get_text(strip=True)


def _parse_item(li: Tag) -> MenuItem:
    li_classes = li.get("class", [])
    if not isinstance(li_classes, list):
        li_classes = [li_classes] if li_classes else []

    name_tag = li.find(class_="clsLabel_Name")
    name = name_tag.get_text(strip=True) if isinstance(name_tag, Tag) else "Unknown"

    ingredients = ""
    ing_tag = li.find(class_="clsLabel_Ingredients")
    if isinstance(ing_tag, Tag):
        ingredients = _section_text(ing_tag, "clsSectionName")

    allergens = ""
    al_tag = li.find(class_="clsLabel_Allergens")
    if isinstance(al_tag, Tag):
        allergens = _section_text(al_tag, "clsSectionNameAllegens")

    trace_allergens = ""
    tr_tag = li.find(class_="clsLabel_TraceAllergens")
    if isinstance(tr_tag, Tag):
        trace_allergens = _section_text(tr_tag, "clsSectionNameAllegens")

    return MenuItem(
        name=name,
        ingredients=ingredients,
        allergens=allergens,
        trace_allergens=trace_allergens,
        is_gluten_free="clsGF_Row" in li_classes,
        is_vegetarian="clsV_Row" in li_classes,
        is_vegan="clsVGN_Row" in li_classes,
        is_halal="clsHALAL_Row" in li_classes,
        is_kosher="clsKOSHER_Row" in li_classes,
    )


def parse_menu_html(
    html: str, hall_id: str, hall_name: str, date: str, meal: str
) -> DiningHallMenu:
    soup = _soup(html)
    raw_items = [li for li in soup.find_all("li", class_="clsMenuItem") if isinstance(li, Tag)]
    if not raw_items and not soup.find("select", {"id": "MainContent_lstLocations"}):
        # No items AND no form: the page structure likely changed. If the form is
        # present the hall simply has no items for this meal/date (not an error).
        logger.warning(
            "%s: neither menu items nor the location dropdown found — the page "
            "structure may have changed (check 'clsMenuItem' / 'MainContent_lstLocations').",
            hall_name,
        )
    items: list[MenuItem] = []
    for li in raw_items:
        try:
            items.append(_parse_item(li))
        except Exception as exc:  # noqa: BLE001 - one bad row must not drop the menu
            logger.warning("Failed to parse a menu item in %s: %s", hall_name, type(exc).__name__)
    return DiningHallMenu(hall_name=hall_name, hall_id=hall_id, date=date, meal=meal, items=items)


class StanfordMenuScraper:
    def __init__(
        self,
        *,
        timeout_s: float = 15.0,
        request_delay_s: float = 0.5,
        max_response_bytes: int = 4_000_000,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.request_delay_s = request_delay_s
        self.max_response_bytes = max_response_bytes
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=self.timeout_s,
            follow_redirects=False,
            transport=self._transport,
        )

    def _fetch(self, client: httpx.Client, method: str, **kwargs: object) -> str:
        """Stream a response, enforcing the byte cap, and return decoded text."""
        with client.stream(method, MENU_URL, **kwargs) as resp:  # type: ignore[arg-type]
            resp.raise_for_status()
            total = 0
            chunks: list[bytes] = []
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > self.max_response_bytes:
                    raise RuntimeError("menu response exceeded the configured size limit")
                chunks.append(chunk)
            return b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")

    def _post_menu(
        self, client: httpx.Client, hidden: dict[str, str], hall_id: str, date_str: str, meal: str
    ) -> str:
        data = {
            "__VIEWSTATE": hidden.get("__VIEWSTATE", ""),
            "__VIEWSTATEGENERATOR": hidden.get("__VIEWSTATEGENERATOR", ""),
            "__EVENTVALIDATION": hidden.get("__EVENTVALIDATION", ""),
            "__EVENTTARGET": "GetMenulstMealType",
            "__EVENTARGUMENT": "",
            "ctl00$MainContent$lstLocations": hall_id,
            "ctl00$MainContent$lstDay": date_str,
            "ctl00$MainContent$lstMealType": meal,
        }
        html = self._fetch(client, "POST", data=data, headers={"Referer": MENU_URL})
        # Carry forward refreshed form state for the next POST on this session.
        for key, value in _extract_hidden_fields(_soup(html)).items():
            if value:
                hidden[key] = value
        return html

    def scrape_all(
        self, date_str: str, meal: str, hall_ids: list[str] | None = None
    ) -> list[DiningHallMenu]:
        try:
            with self._client() as client:
                initial = _soup(self._fetch(client, "GET"))
                hidden = _extract_hidden_fields(initial)

                locations = _extract_locations(initial)
                if not locations:
                    raise RuntimeError("no dining halls found — the menu page may have changed")

                days = _extract_day_options(initial)
                if days and date_str not in days:
                    raise RuntimeError(f"date {date_str!r} not offered by the menu page")

                meals = _extract_meal_options(initial)
                if meals and meal not in meals:
                    raise RuntimeError(f"meal {meal!r} not offered by the menu page")

                if hall_ids:
                    locations = [(hid, name) for hid, name in locations if hid in hall_ids]
                    if not locations:
                        raise RuntimeError(f"none of the requested halls {hall_ids} are available")

                results: list[DiningHallMenu] = []
                for hall_id, hall_name in locations:
                    try:
                        html = self._post_menu(client, hidden, hall_id, date_str, meal)
                        results.append(parse_menu_html(html, hall_id, hall_name, date_str, meal))
                    except (httpx.HTTPError, RuntimeError) as exc:
                        logger.warning("Failed to scrape %s: %s", hall_name, type(exc).__name__)
                        results.append(
                            DiningHallMenu(
                                hall_name=hall_name,
                                hall_id=hall_id,
                                date=date_str,
                                meal=meal,
                                items=[],
                            )
                        )
                    if self.request_delay_s:
                        time.sleep(self.request_delay_s)
                return results
        except httpx.HTTPError as exc:
            raise RuntimeError(f"menu site request failed: {type(exc).__name__}") from exc
