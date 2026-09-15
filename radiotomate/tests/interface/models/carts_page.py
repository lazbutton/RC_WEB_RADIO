from tests.interface.models import RadiotomatePage


class CartsPage(RadiotomatePage):
    """
    Basic methods are made to work with the carts list or with the cart's sounds list.
    """

    @property
    def table(self):
        return self.page.get_by_role("table", name="list")

    @property
    def form(self):
        return self.get_by_role("form")

    def form_input(self, name):
        return self.form.get_by_role("textbox", name=name)

    def form_button(self, name):
        return self.form.get_by_label(name)

    def line_of(self, title):
        return self.table.get_by_title(title)

    async def click_create_cart(self):
        await self.page.get_by_role("link", name="Nouveau cart").click()
