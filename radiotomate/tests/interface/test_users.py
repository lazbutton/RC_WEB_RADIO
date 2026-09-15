import re

from playwright.async_api import Page, expect

from tests.interface import USER_PASSWORD, USER_USERNAME
from tests.interface.models import (
    RadiotomatePage,
    ensure_connected,
    ensure_disconnected,
)
from tests.interface.models.users_page import UsersPage


async def test_users_non_visible(luser_page: Page):
    page = RadiotomatePage(luser_page)
    await page.goto("/users")
    await expect(page.page).not_to_have_title(re.compile("Comptes"))
    await expect(
        page.get_by_role("navigation").get_by_role("link", name="Comptes")
    ).not_to_be_visible()


async def test_users_crud(admin_page: Page, page: Page):  # noqa: PLR0915
    username1 = "Robert'); drop table users; --"  # https://xkcd.com/327/
    username2 = 'Little%20"Bobby" tables'
    editeduser_page = RadiotomatePage(page)
    users_page = UsersPage(admin_page)
    await users_page.goto("/users")

    await expect(users_page.page).to_have_title(re.compile("Comptes"))
    await users_page.click_create_user()
    await expect(users_page.form).to_be_visible()
    await users_page.form_button("Annuler").click()
    await expect(users_page.form).to_be_hidden()

    await users_page.click_create_user()
    await users_page.form_input("Identifiant").fill(" ")
    await users_page.form_button("Ajouter").click()
    await expect(users_page.dialog).to_contain_text("Please provide an username")
    await users_page.dialog_click("OK")
    await users_page.form_input("Identifiant").fill(username1)
    await users_page.form_button("Ajouter").click()
    await expect(users_page.dialog).to_contain_text("Please provide a password")
    await users_page.dialog_click("OK")
    await users_page.form_input("Mot de passe").fill("testpassword")
    await users_page.form_input("notes").fill("Testing notes")
    await users_page.form_button("Ajouter").click()
    await expect(users_page.table).to_contain_text(username1)

    await users_page.click_create_user()
    await users_page.form_input("Identifiant").fill(username1)
    await users_page.form_input("Mot de passe").fill("testpassword")
    await users_page.form_input("notes").fill("Testing notes2")
    await users_page.form_button("Ajouter").click()
    await expect(users_page.dialog).to_contain_text("already exists")
    await users_page.dialog_click("OK")
    await users_page.form_input("Identifiant").fill(username2)
    await users_page.form_button("Ajouter").click()
    await expect(users_page.table).to_contain_text(username2)

    await users_page.user_line(username2).get_by_title("Modifier").first.click()
    await users_page.form_input("Identifiant").fill(username1)
    await users_page.form_input("notes").fill("Edited notes")
    await users_page.form_button("Enregistrer").click()
    await expect(users_page.dialog).to_contain_text("already exists")
    await users_page.dialog_click("OK")
    await users_page.form_button("Annuler").click()
    await expect(users_page.table).to_contain_text("Testing notes2")

    await users_page.user_line(username2).get_by_title("Modifier").first.click()
    await users_page.form_input("Identifiant").fill("editeduser")
    await users_page.form_input("notes").fill("Edited notes")
    await users_page.form_button("cart").click()
    await users_page.form_button("Enregistrer").click()
    await expect(users_page.form_button("Annuler")).not_to_be_visible()
    await expect(users_page.table).to_contain_text("Edited notes")

    # log that user in, so he'll be associated to basic data (like, a first session)
    await editeduser_page.goto("/")
    await editeduser_page.nav("Connexion")
    await editeduser_page.get_by_role("textbox", name="Identifiant").fill("editeduser")
    await editeduser_page.get_by_role("textbox", name="Mot de passe").fill(
        "testpassword"
    )
    await editeduser_page.get_by_role(
        "button",
    ).click()  # pressing Enter is tested by admin_context
    await ensure_connected(page)
    await editeduser_page.goto("/carts")
    await editeduser_page.nav("Déconnexion")
    await ensure_disconnected(page)

    await users_page.user_line("editeduser").get_by_title("Supprimer").click()
    await expect(users_page.dialog).to_contain_text("editeduser")
    await users_page.dialog_click("Annuler")
    await users_page.user_line("editeduser").get_by_title("Supprimer").click()
    await users_page.dialog_click("Oui")
    await expect(users_page.user_line("editeduser")).not_to_be_visible()
    await users_page.reload()
    await expect(users_page.user_line("editeduser")).not_to_be_visible()

    # test that the user can't log in with the old password
    await editeduser_page.nav("Connexion")
    await editeduser_page.get_by_role("textbox", name="Identifiant").fill("editeduser")
    await editeduser_page.get_by_role("textbox", name="Mot de passe").fill(
        "testpassword"
    )
    await editeduser_page.get_by_role("button").click()
    await expect(editeduser_page.dialog).to_contain_text(
        "Incorrect username or password",
    )


async def test_account_password(luser_page: Page):
    page = RadiotomatePage(luser_page)
    await page.goto("/")
    await expect(
        page.get_by_role("navigation").get_by_role("link", name="Mon compte")
    ).to_be_visible()
    await page.nav("Mon compte")
    await expect(page.page).to_have_title(re.compile("Mon compte"))
    await page.get_by_label("Nouveau mot de passe").fill("nouveau-mot-de-passe")
    await page.get_by_role("button", name="Enregistrer").click()
    await expect(page.page).to_have_title(re.compile("Mon compte"))
    await page.nav("Déconnexion")
    await ensure_disconnected(page)
    await page.get_by_role("textbox", name="Identifiant").fill(USER_USERNAME)
    await page.get_by_role("textbox", name="Mot de passe").fill("nouveau-mot-de-passe")
    await page.get_by_role("button").click()
    await ensure_connected(page)
    await page.nav("Mon compte")
    await page.get_by_label("Nouveau mot de passe").fill(USER_PASSWORD)
    await page.get_by_role("button", name="Enregistrer").click()
    await expect(page.page).to_have_title(re.compile("Mon compte"))
