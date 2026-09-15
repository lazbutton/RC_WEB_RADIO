from datetime import date
from re import compile as re_compile

from playwright.async_api import Page, expect

from tests.interface.models import RadiotomatePage


async def test_live(admin_page: Page):
    page = RadiotomatePage(admin_page)
    await page.goto("/")
    await expect(
        page.get_by_role("navigation").get_by_role("link", name="Antenne")
    ).to_have_attribute("aria-current", "page")
    today = date.today().strftime("%d/%m/%Y")
    await expect(page.get_by_role("timer").filter(has_text=today)).to_be_visible()
    currently_playing = await page.get_by_role("main").text_content()
    await page.get_by_role("button", name="Passer le morceau").click()
    await page.dialog_click("Oui")
    await expect(page.get_by_role("main")).not_to_contain_text(currently_playing)
    await expect(page.locator("#ntr-onair")).to_have_class(
        re_compile(r"is-(live|auto|cart)")
    )
    await expect(page.get_by_text("Prochain cart")).to_be_visible()
    await expect(page.get_by_text("Prochain auto-DJ")).to_be_visible()
    await expect(page.get_by_text("Prochain jingle")).to_be_visible()
    currently_playing = await page.get_by_role("main").text_content()
    await page.keyboard.press("s")
    await page.dialog_click("Oui")
    await expect(page.get_by_role("main")).not_to_contain_text(currently_playing)


async def test_live_luser(luser_page: Page):
    page = RadiotomatePage(luser_page)
    await page.goto("/")
    await expect(
        page.get_by_role("navigation").get_by_role("link", name="Antenne")
    ).to_have_attribute("aria-current", "page")
    today = date.today().strftime("%d/%m/%Y")
    await expect(page.get_by_role("timer").filter(has_text=today)).to_be_visible()
    await expect(page.get_by_text("Prochain cart")).to_be_visible()
    await expect(
        page.get_by_role("button", name="Passer le morceau")
    ).not_to_be_visible()
