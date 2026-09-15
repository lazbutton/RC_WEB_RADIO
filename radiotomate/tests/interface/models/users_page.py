from tests.interface.models import RadiotomatePage


class UsersPage(RadiotomatePage):
    @property
    def table(self):
        return self.page.get_by_role("table", name="Liste des comptes")

    @property
    def form(self):
        return self.page.get_by_role("form")

    def form_input(self, name):
        return self.form.get_by_role("textbox", name=name)

    def form_button(self, name):
        return self.form.get_by_label(name)

    def user_line(self, username):
        return self.table.get_by_role("row").filter(has_text=username)

    async def click_create_user(self):
        await self.page.get_by_label("Nouveau compte").click()
