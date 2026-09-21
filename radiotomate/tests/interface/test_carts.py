import re
import shutil
import sqlite3
from os import sync
from pathlib import Path

import pytest
from playwright.async_api import Page, expect

from tests.interface import ADMIN_USERNAME
from tests.interface.models.carts_page import CartsPage

# avoid weird timeouts: Playwright needs an absolute path to files to upload
assets_folder = Path(__file__).resolve().parent.parent / "assets"


class TestCarts:
    """
    Those tests are independant, but each one assumes that features from the
    previous ones are correct so, if they fail, fix them in order.
    """

    carts_forlders_wildcard = "radio_data/carts/*"

    @pytest.fixture(autouse=True)
    def cleanup_carts_after_test(self, server_root: Path, db_cursor: sqlite3.Cursor):
        yield
        db_cursor.execute("DELETE FROM carts")
        for d in server_root.glob(self.carts_forlders_wildcard):
            shutil.rmtree(d)

    @pytest.fixture(autouse=True)
    def _set_server_root(self, server_root: Path):
        self.server_root = server_root

    def get_check_nb_cart_folders(self, nb_carts: int) -> list[str]:
        glob = self.server_root.glob(self.carts_forlders_wildcard)
        carts_folders = [d.name for d in glob]
        assert len(carts_folders) == nb_carts
        return carts_folders

    def assert_cart_contains(self, cart_filter: str, nb_mp3: int):
        sync()  # otherwise that test might be flaky
        for d in self.server_root.glob(self.carts_forlders_wildcard):
            if cart_filter in d.name:
                cart_path = d
                break
        else:
            raise AssertionError(f"Could not find a cart folder like {cart_filter}")
        assert len(list(cart_path.glob("*.mp3"))) == nb_mp3

    async def test_carts_crud(self, admin_page: Page):  # noqa: PLR0915
        """
        We need to create carts with weird names because it checks that their folders'
        names are robust.
        """
        testing_cart_title = '//#"Testing" cart#//'
        testing_cart_title2 = "Test%%cart... édited ?"
        testing_cart2_title = "\\$ Test cart *returns* $\\"

        carts_page = CartsPage(admin_page)
        await admin_page.goto("/carts")

        await expect(admin_page).to_have_title(re.compile("Carts"))
        await carts_page.click_create_cart()
        await expect(carts_page.form).to_be_visible()
        await carts_page.form_button("Annuler").click()
        await expect(carts_page.form).to_be_hidden()

        await carts_page.click_create_cart()
        await carts_page.form_button("Ajouter").click()
        await expect(carts_page.dialog).to_contain_text("Please provide a title")
        await carts_page.dialog_click("OK")
        await carts_page.form_input("Titre").fill(testing_cart_title)
        # set a maximum duration:
        await carts_page.form.get_by_placeholder("minutes").fill("1")
        await carts_page.form.get_by_placeholder("seconds").fill("300")
        await carts_page.form_button("Ajouter").click()
        await expect(carts_page.table).to_contain_text(testing_cart_title)
        await expect(carts_page.table).to_contain_text("limit")
        await expect(carts_page.table).to_contain_text("6:00")
        await expect(
            carts_page.line_of(testing_cart_title).get_by_title("Diffuser maintenant")
        ).to_be_visible()

        await carts_page.click_create_cart()
        await carts_page.form_input("Titre").fill(testing_cart_title)
        await carts_page.form_button("Ajouter").click()
        await expect(carts_page.dialog).to_contain_text("already exists")
        await carts_page.dialog_click("OK")
        await carts_page.form_input("Titre").fill(testing_cart2_title)
        await carts_page.form.get_by_label("Programmation").select_option(
            label="Jingles"
        )
        await carts_page.form_button("Ajouter").click()
        await expect(carts_page.table).to_contain_text(testing_cart2_title)
        await expect(carts_page.line_of(testing_cart2_title)).to_contain_text(
            "Jingles",
        )

        cartfolders = self.get_check_nb_cart_folders(2)
        cartfolders.sort()  # we exploit their prefix by ID
        assert "Testingcart" in cartfolders[0]
        assert "Testcartreturns" in cartfolders[1]

        await carts_page.line_of(testing_cart2_title).get_by_title("Modifier").click()
        await carts_page.form_input("Titre").fill(testing_cart_title)
        await carts_page.form_input("notes").fill("Edited notes")
        await carts_page.form_button("Enregistrer").click()
        await expect(carts_page.dialog).to_contain_text("already exists")
        await carts_page.dialog_click("OK")
        await carts_page.form_button("Annuler").click()
        await expect(carts_page.table).to_contain_text(testing_cart2_title)

        await carts_page.line_of(testing_cart2_title).get_by_title("Modifier").click()
        await carts_page.form_input("Titre").fill(testing_cart_title2)
        await carts_page.form_input("notes").fill("Edited notes")
        await carts_page.form.get_by_placeholder("minutes").fill("0")
        await carts_page.form.get_by_placeholder("seconds").fill("0")
        await carts_page.form.get_by_role("combobox", name="mode").select_option(
            label="Random",
        )
        await carts_page.form.get_by_label("Programmation").select_option(
            label="Horloge"
        )
        await carts_page.form_button("Enregistrer").click()
        await expect(carts_page.form_button("Annuler")).not_to_be_visible()
        await expect(carts_page.table).to_contain_text("Edited notes")
        await expect(carts_page.line_of(testing_cart_title2)).not_to_contain_text(
            "limit"
        )
        await expect(carts_page.line_of(testing_cart_title2)).to_contain_text("Horloge")
        await expect(carts_page.line_of(testing_cart_title2)).not_to_contain_text(
            "error",
        )

        await carts_page.line_of(testing_cart_title2).get_by_title("Modifier").click()
        await carts_page.form.get_by_placeholder("minutes").fill("3")
        await carts_page.form.get_by_placeholder("seconds").clear()
        await carts_page.form.get_by_label("Avancé").check()
        await carts_page.form.get_by_placeholder("hour").fill("25")
        await carts_page.form_button("Enregistrer").click()
        await expect(carts_page.form_button("Annuler")).not_to_be_visible()
        await expect(carts_page.line_of(testing_cart_title2)).to_contain_text("erreur")
        await expect(carts_page.line_of(testing_cart_title2)).to_contain_text("3:00")

        cartfolders = self.get_check_nb_cart_folders(2)
        cartfolders.sort()  # we exploit their prefix by ID
        assert "Testingcart" in cartfolders[0]
        assert "édited" in cartfolders[1]

        await carts_page.line_of(testing_cart_title2).get_by_title("Supprimer").click()
        await expect(carts_page.dialog).to_contain_text(testing_cart_title2)
        await carts_page.dialog_click("Annuler")
        await carts_page.line_of(testing_cart_title2).get_by_title("Supprimer").click()
        await carts_page.dialog_click("Oui")
        await carts_page.reload()
        await expect(carts_page.line_of(testing_cart_title2)).not_to_be_visible()

        cartfolders = self.get_check_nb_cart_folders(1)
        assert "Testingcart" in cartfolders[0]

    async def test_carts_simple_timing(self, admin_page: Page):
        """
        Switch between simple and advanced timing

        When using the advanced mode, we also push to the Auto-DJ queue: when switching
        back to simple timing it should fallback to the Carts queue automatically.
        """
        carts_page = CartsPage(admin_page)
        await admin_page.goto("/carts")
        await carts_page.click_create_cart()
        await expect(carts_page.form).to_be_visible()

        await carts_page.form_input("Titre").fill("Testing cart modes")

        await carts_page.form.get_by_label("Programmation").select_option(
            label="Jingles"
        )
        await expect(
            carts_page.form.get_by_text("Quand", exact=True)
        ).not_to_be_visible()
        # The cron "Timed" mode is retired: only Horloge / Jingles are offered.
        await expect(
            carts_page.form.get_by_label("Programmation").get_by_role(
                "option", name="Timed"
            )
        ).to_have_count(0)
        await carts_page.form.get_by_label("Auto-DJ").click()
        await carts_page.form_button("Ajouter").click()

        await carts_page.line_of("Testing cart modes").get_by_title("Modifier").click()
        await expect(carts_page.form.get_by_label("Auto-DJ")).to_be_checked()
        await expect(carts_page.line_of("Testing cart modes")).to_contain_text(
            "Jingles"
        )

    async def test_carts_sounds(self, admin_page: Page, luser_page: Page):  # noqa: PLR0915
        """
        Test carts' sounds management

        GTA3Theme lasts 95s
        ohradiotomateoh lasts 1s
        winamp-it-really-whips-the-llamas-ass lasts 6s
        """
        page = CartsPage(admin_page)
        other_user_page = CartsPage(luser_page)
        await page.goto("/carts")
        await page.click_create_cart()
        await page.form_input("Titre").fill("Sounds test")
        await page.form_button("Ajouter").click()
        line = page.line_of("Sounds test")
        await expect(line).to_contain_text("Ajouter")
        await line.get_by_label("Ajouter").set_input_files(
            [
                assets_folder / "GTA3Theme.mp3",
                assets_folder / "winamp-it-really-whips-the-llamas-ass.mp3",
            ],
        )
        # browsing to "cart's sounds" page - check files once we're settled, none
        # should be marked as Next yet: we're still analyzing
        line = page.line_of("GTA3Theme.mp3")
        await expect(line.get_by_text("Suivant")).not_to_be_visible()
        await expect(line).to_contain_text("Analyse")
        line = page.line_of("winamp-it-really-whips-the-llamas-ass.mp3")
        await expect(line).to_contain_text(":05")
        await expect(line).to_contain_text("Analyse")
        await expect(line).to_contain_text(ADMIN_USERNAME)
        await expect(line.get_by_text("Suivant")).not_to_be_visible()
        self.assert_cart_contains("Soundstest", 2)
        # wait a second, while the analyzer stub marks sounds as available.
        await page.wait_for_timeout(1000)

        title_input = line.get_by_role("textbox", name="titre")
        await title_input.fill("")
        await line.get_by_role("button", name="Enregistrer").click()
        await expect(page.dialog).to_contain_text("Please provide a title")
        await page.dialog_click("OK")
        await title_input.fill("Winamp's legendary jingle")
        await title_input.press("Enter")

        # meanwhile: another user can browse in those sounds
        await other_user_page.nav("Carts")
        await expect(
            other_user_page.get_by_role("link", name="Nouveau cart")
        ).not_to_be_visible()
        await (
            other_user_page.line_of("Sounds test")
            .get_by_title("Gérer les sons")
            .click()
        )
        await expect(other_user_page.line_of("GTA3Theme")).to_be_visible()
        line = other_user_page.line_of("Winamp")
        await expect(line).to_be_visible()
        await expect(line.get_by_role("textbox", name="titre")).not_to_be_visible()
        await expect(line).to_contain_text(ADMIN_USERNAME)
        await expect(line.get_by_role("link", name="Télécharger")).to_be_visible()
        # /other_user_page

        await page.line_of("GTA3Theme").get_by_title("Supprimer").click()
        await expect(page.dialog).to_contain_text("GTA3Theme")
        await page.dialog_click("Annuler")
        await page.line_of("GTA3Theme").get_by_title("Supprimer").click()
        await page.dialog_click("Oui")
        await expect(page.line_of("GTA3Theme")).not_to_be_visible()
        self.assert_cart_contains("Soundstest", 1)
        # at this point the analyzer should be done, so the only sound should be next:
        await expect(page.get_by_text("Suivant")).to_be_visible()
        await expect(page.get_by_text("Analyse")).not_to_be_visible()

        # one sound left: if we disable it, the cart should not give any sound
        line = page.line_of("Winamp's legendary jingle")
        await line.get_by_role("checkbox", name="actif").uncheck()
        await expect(page.get_by_text("Suivant")).not_to_be_visible()
        await expect(page.get_by_role("note")).to_contain_text("1 son (1 inactif)")
        await line.get_by_role("checkbox", name="actif").check()
        await expect(line.get_by_text("Suivant")).to_be_visible()

        await page.get_by_label("Ajouter").set_input_files(
            [assets_folder / "ohradiotomateoh.mp3"],
        )
        newline = page.line_of("ohradiotomateoh")
        await expect(newline).to_contain_text("Jamais diffusé")
        await expect(newline.get_by_role("cell").first).to_contain_text("2")

        # previous one is still the next one and n°1
        await expect(line.get_by_text("Suivant")).to_be_visible()
        await expect(line.get_by_role("cell").first).to_contain_text("1")
        # disable it
        await line.get_by_role("checkbox", name="actif").uncheck()
        await expect(line.get_by_text("Suivant")).not_to_be_visible()
        await expect(newline.get_by_text("Suivant")).to_be_visible()
        await expect(page.get_by_role("note")).to_contain_text("2 sons (1 inactif)")

        await newline.get_by_label("Monter").click()
        await expect(newline.get_by_role("cell").first).to_contain_text("1")
        await expect(newline.get_by_text("Suivant")).to_be_visible()
        await expect(line.get_by_text("Suivant")).not_to_be_visible()

        # re-enable the first one and put it first
        await line.get_by_role("checkbox", name="actif").check()
        await newline.get_by_label("Descendre").click()
        await expect(newline.get_by_role("cell").first).to_contain_text("2")
        await expect(newline.get_by_text("Suivant")).not_to_be_visible()
        await expect(line.get_by_text("Suivant")).to_be_visible()

        # Clicking on "edit cart" should bring you back to the sound page
        await page.get_by_role("link", name="Modifier le cart").click()
        await page.form_button("Annuler").click()

        # now really editing: cart mode
        await page.get_by_role("link", name="Modifier le cart").click()
        # we should not propose it becomes a relay any more:
        await expect(
            page.form.get_by_role("combobox", name="mode").get_by_label("Relay")
        ).not_to_be_visible()
        await page.form.get_by_role("combobox", name="mode").select_option(
            label="Random",
        )
        await page.form_button("Enregistrer").click()

        await expect(
            page.get_by_role("cell", name="Rang", exact=True),
        ).not_to_be_visible()
        await expect(newline.get_by_role("cell").first).not_to_contain_text("1")
        await expect(newline.get_by_role("cell").first).not_to_contain_text("2")
        await expect(newline.get_by_text("Suivant")).not_to_be_visible()
        await expect(line.get_by_text("Suivant")).not_to_be_visible()

        # back to playlist
        await page.get_by_role("link", name="Modifier le cart").click()
        await page.form.get_by_role("combobox", name="mode").select_option(
            label="Playlist",
        )
        await page.form_button("Enregistrer").click()

        await expect(
            page.get_by_role("columnheader", name="Rang", exact=True),
        ).to_be_visible()
        await expect(line.get_by_role("cell").first).to_contain_text("1")
        await expect(newline.get_by_role("cell").first).to_contain_text("2")
        await expect(newline.get_by_text("Suivant")).not_to_be_visible()
        await expect(line.get_by_text("Suivant")).to_be_visible()

        # edit title: that should update sounds' path
        await page.get_by_role("link", name="Modifier le cart").click()
        await page.form_input("Titre").fill("Sounds test, edited")
        await page.form_button("Enregistrer").click()

        await page.line_of("ohradiotomateoh").get_by_title("Supprimer").click()
        await page.dialog_click("Oui")
        await expect(page.line_of("ohradiotomateoh")).not_to_be_visible()
        self.assert_cart_contains("edited", 1)
        await page.nav("Carts")
        await expect(page.line_of("Sounds test")).to_contain_text("1 son")
        await expect(page.line_of("Sounds test")).to_contain_text(":03")
        await expect(
            page.line_of("Sounds test").get_by_title("Playlist"),
        ).to_be_visible()

    async def test_relays(self, admin_page: Page):
        """
        Test stream relay carts
        """
        page = CartsPage(admin_page)
        await page.goto("/carts")
        await page.click_create_cart()
        await page.form_input("Titre").fill("Relay RCG")
        await page.form.get_by_role("combobox", name="mode").select_option(
            label="Relay"
        )
        await page.form_input("url").fill("https://live.campusgrenoble.org/rcg112")
        await page.form_button("Ajouter").click()
        await expect(page.dialog).to_contain_text(
            "Stream relays should have a maximum duration"
        )
        await page.dialog_click("OK")
        await page.form.get_by_placeholder("minutes").fill("15")
        await page.form_button("Ajouter").click()

        line = page.line_of("Relay RCG")
        await expect(line).to_contain_text("campusgrenoble")
        await expect(line).not_to_contain_text("Ajouter")
        await line.get_by_title("Modifier").click()
        await expect(page.form.get_by_role("combobox", name="mode")).to_be_disabled()
        await page.form_input("url").fill("live.radiocampus.fr/radiocampusfrance.mp3")
        await page.form_button("Enregistrer").click()
        await expect(page.dialog).to_contain_text(
            "This does not look like a complete URL"
        )
        await page.dialog_click("OK")
        await page.form_input("url").fill(
            "http://live.radiocampus.fr:8000/radiocampusfrance.mp3"
        )

        await page.form.get_by_placeholder("minutes").fill("0")
        await page.form_button("Enregistrer").click()
        await expect(page.dialog).to_contain_text(
            "Stream relays should have a maximum duration"
        )
        await page.dialog_click("OK")
        await page.form.get_by_placeholder("minutes").fill("15")
        await page.form_button("Enregistrer").click()
        await expect(line).to_contain_text("radiocampus")
