"""
Page models for Playwright-based tests
======================================

https://playwright.dev/python/docs/pom

All models should inherit from `RadiotomatePage`.
"""

from playwright.async_api import Page, expect


async def ensure_connected(page: Page):
    await expect(
        page.get_by_role("navigation").get_by_role("link", name="Déconnexion"),
    ).to_be_visible()


async def ensure_disconnected(page: Page):
    await expect(
        page.get_by_role("navigation").get_by_role("link", name="Connexion"),
    ).to_be_visible()


class RadiotomatePage(Page):
    """
    Base object model.

    Method calls are forwarded to the underlying page object, so you can also
    use this as a Page object. This class inherits from ``Page`` only to help
    auto-completion.
    """

    def __init__(self, page: Page) -> None:
        self.page = page

    def __getattr__(self, name):
        return getattr(self.page, name)

    @property
    def dialog(self):
        return self.page.get_by_role("dialog")

    async def dialog_click(self, button_name):
        await self.dialog.get_by_role("button", name=button_name).click()

    async def nav(self, title):
        await (
            self.page.get_by_role("navigation").get_by_role("link", name=title).click()
        )
