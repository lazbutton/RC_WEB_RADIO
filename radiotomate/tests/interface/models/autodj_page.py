from tests.interface.models import RadiotomatePage


class AutoDJPage(RadiotomatePage):
    async def click_label(self, label):
        await self.page.get_by_label(label).click()

    @property
    def form(self):
        return self.page.get_by_role("form")

    def form_input(self, label):
        return self.form.get_by_label(label)

    def day(self, name):
        return self.get_by_role("region", name=name)

    def slot(self, title):
        return self.get_by_role("article", name=title)
