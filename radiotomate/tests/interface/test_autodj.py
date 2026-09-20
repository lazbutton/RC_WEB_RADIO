from playwright.async_api import Page, expect

from radiotomate.beets.demo import FAKE_ARTIST
from tests.interface.models.autodj_page import AutoDJPage


async def test_autodj_calendar(admin_page: Page, luser_page: Page):  # noqa: PLR0915
    page = AutoDJPage(admin_page)
    luser_page = AutoDJPage(luser_page)
    await page.goto("/")
    await page.nav("Auto-DJ")
    await expect(page.day("Lundi")).to_be_visible()
    await expect(page.get_by_text("path:/media/10-rotation")).to_be_visible()

    slot = page.slot("24/24 Rotation habillée").first
    await slot.get_by_role("link").click()
    await expect(page.form_input("Annuler")).to_be_visible()
    await expect(page.form_input("Supprimer")).not_to_be_visible()
    await page.click_label("Annuler")

    await page.click_label("Nouveau créneau")
    await expect(page.form).to_be_visible()
    await page.form_input("Titre").fill("TopHits")
    await page.form.get_by_placeholder("hour").fill("8")
    await expect(page.form_input("Couleur")).to_be_editable()  # to be tested manually
    await page.click_label("Enregistrer")
    await expect(page.dialog).to_contain_text("choose a day")
    await page.dialog_click("OK")
    await page.form_input("Jour").select_option(label="Lundi")
    await page.click_label("Enregistrer")

    slot = page.slot("TopHits")
    await expect(slot).to_be_visible()
    await page.click_label("Nouveau créneau")
    await page.form_input("Titre").fill("Duplicate !!!!!!!!")
    await page.form_input("Jour").select_option(label="Lundi")
    await page.form.get_by_placeholder("hour").fill("8")
    await page.click_label("Enregistrer")
    await expect(page.dialog).to_contain_text("already scheduled at that time")
    await page.dialog_click("OK")
    await page.click_label("Annuler")
    await expect(page.form).not_to_be_visible()

    slot = page.slot("TopHits")
    await slot.get_by_role("link").click()
    await page.form_input("Titre").fill("TopEdits")
    await page.form.get_by_placeholder("minute").fill("15")
    await page.click_label("Enregistrer")
    slot = page.slot("TopEdits")
    await expect(slot).to_contain_text("08:15")
    await slot.get_by_role("link").click()
    await expect(page.form_input("Jour")).to_be_visible()
    assert (
        await page.form_input("Jour").get_by_role("option", name="Tous les jours").count()
        == 0
    )
    await page.form_input("Jour").select_option(label="Mercredi")
    await page.click_label("Enregistrer")
    await expect(
        page.get_by_role("region", name="Mercredi").get_by_role(
            "article", name="TopEdits"
        )
    ).to_be_visible()

    slot = page.slot("TopEdits")
    await slot.get_by_role("link").click()
    await page.click_label("Supprimer")
    await page.dialog_click("Oui")
    await expect(page.slot("24/24 Rotation habillée").first).to_be_visible()
    await expect(slot).not_to_be_visible()

    await page.click_label("Nouveau créneau")
    await page.form_input("Titre").fill("Every mid-day")
    await page.form_input("Jour").select_option(label="Tous les jours")
    await page.form.get_by_placeholder("hour").fill("12")
    await page.click_label("Enregistrer")
    slot = page.slot("Every mid-day")
    await expect(slot.first).to_be_visible()
    assert await slot.count() == 7

    ##################################### Testing filters ##############################
    await slot.first.get_by_role("link").click()
    await page.click_label("Ajouter un filtre")
    await page.form.get_by_placeholder("weight").fill("2")
    await page.form.get_by_placeholder("filter").fill(FAKE_ARTIST)
    await page.click_label("Tester")
    results = page.get_by_role("complementary", name="Test du filtre")
    await expect(results).to_be_visible()
    assert (
        await results.get_by_text(FAKE_ARTIST).count() == 6
    )  # filter copy + 5 results
    await page.click_label("Enregistrer")

    await slot.first.get_by_role("link").click()
    await expect(page.get_by_placeholder("weight")).to_have_value("2")
    await expect(page.get_by_placeholder("filter")).to_have_value(FAKE_ARTIST)
    await page.click_label("Retirer")
    await page.click_label("Enregistrer")
    await slot.first.get_by_role("link").click()
    await expect(page.get_by_text(FAKE_ARTIST)).not_to_be_visible()

    ##################"" logged-in users can only see the calendar #####################
    await luser_page.nav("Auto-DJ")
    await expect(luser_page.day("Lundi")).to_be_visible()
    slot = luser_page.slot("Every mid-day")
    await expect(slot.first).to_be_visible()
    await expect(slot.get_by_role("link")).not_to_be_visible()
