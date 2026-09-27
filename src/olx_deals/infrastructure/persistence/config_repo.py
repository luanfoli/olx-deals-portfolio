from typing import Protocol, runtime_checkable
import os
@runtime_checkable
class ConfigRepository(Protocol):
    def load(self)->dict: ...
    def save(self, patch:dict)->dict: ...
class FileConfigRepository:
    def __init__(self, sd=None, cf=None, state_dir=None, config_file=None):
        self._sd=sd or state_dir; self._cf=cf or config_file
    def _paths(self):
        if self._sd or self._cf: sd=self._sd or os.path.expanduser("~/.olx"); cf=self._cf or os.path.join(sd,"config.json"); return sd,cf
        import olx_scraper; return olx_scraper.STATE_DIR, olx_scraper.CONFIG_FILE
    def load(self):
        import olx_scraper
        sd,cf=self._paths()
        if self._sd or self._cf:
            old_sd,old_cf=olx_scraper.STATE_DIR, olx_scraper.CONFIG_FILE
            olx_scraper.STATE_DIR,olx_scraper.CONFIG_FILE=sd,cf
            try: return olx_scraper._orig_load_config()
            finally: olx_scraper.STATE_DIR,olx_scraper.CONFIG_FILE=old_sd,old_cf
        return olx_scraper._orig_load_config()
    def save(self,patch):
        import olx_scraper
        sd,cf=self._paths()
        if self._sd or self._cf:
            old_sd,old_cf=olx_scraper.STATE_DIR, olx_scraper.CONFIG_FILE
            olx_scraper.STATE_DIR,olx_scraper.CONFIG_FILE=sd,cf
            try: return olx_scraper._orig_save_config(patch)
            finally: olx_scraper.STATE_DIR,olx_scraper.CONFIG_FILE=old_sd,old_cf
        return olx_scraper._orig_save_config(patch)
class FakeConfigRepository:
    def __init__(self,i=None):
        import copy,olx_scraper; self._c=copy
        self._d=self._c.deepcopy(i) if i else {"products":[dict(olx_scraper.DEFAULT_PRODUCT)],"telegram_token":None,"telegram_chat_id":None,"notify_price_change_pct":olx_scraper.DEFAULT_NOTIFY_PRICE_CHANGE_PCT,"tuner_llm":dict(olx_scraper.DEFAULT_LLM_CONFIG)}
    def load(self): return self._c.deepcopy(self._d)
    def save(self,patch):
        import olx_scraper
        base=self._c.deepcopy(self._d)
        if patch and "products" in patch and isinstance(patch["products"],list):
            prods,seen=[],set()
            for r in patch["products"]:
                if isinstance(r,dict):
                    np=olx_scraper._normalize_product(r,seen); seen.add(np["id"]); prods.append(np)
            if prods: base["products"]=prods
        for k in ("telegram_token","telegram_chat_id","notify_price_change_pct","tuner_llm"):
            if patch and k in patch: base[k]=patch[k]
        self._d=base; return self._c.deepcopy(base)
