"""Validate source readiness after each navigation, including resumed crawls."""
from bs4 import BeautifulSoup
from rel_crawler.api import RelClient

SOURCE_SELECTOR = '[role="main"][aria-label="Collection of Marketplace items"]'


class MarketplaceClient(RelClient):
    def navigate(self, **kwargs):
        page = super().navigate(**kwargs)
        action_args = {k: kwargs[k] for k in ('session_id', 'output', 'timeout', 'wait')}
        page = self.perform(actions=[{'action': 'wait-for', 'selector': SOURCE_SELECTOR, 'timeout': 25}], **action_args)
        soup = BeautifulSoup(page.output_path.read_text(), 'html.parser')
        if soup.select_one('[role="dialog"] input[type="password"]'):
            page = self.perform(actions=[{'action': 'click', 'selector': '[role="dialog"] [aria-label="Close"]'}], **action_args)
            soup = BeautifulSoup(page.output_path.read_text(), 'html.parser')
        sidebar = soup.select_one('[role="navigation"][aria-label="Marketplace sidebar"]')
        if not sidebar or 'Santa Cruz, California' not in sidebar.get_text(' ', strip=True):
            raise ValueError('Facebook did not apply Santa Cruz location; inspect the REL session')
        if soup.select_one('[role="dialog"] input[type="password"]'):
            raise ValueError('Facebook login blocks results; sign in through the REL session')
        return page
