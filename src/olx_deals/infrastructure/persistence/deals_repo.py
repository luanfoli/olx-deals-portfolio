from typing import Protocol,runtime_checkable,Callable
import json,os,shutil
from datetime import datetime,timezone
@runtime_checkable
class DealsRepository(Protocol):
    def load(self)->list: ...
    def save(self, results:list)->list: ...
    def purge(self, pred:Callable, dry_run:bool=False)->dict: ...
class FileDealsRepository:
    def __init__(self, sd=None, hf=None, state_dir=None, history_file=None):
        self._sd=sd or state_dir; self._hf=hf or history_file
    def _paths(self):
        if self._sd or self._hf: sd=self._sd or os.path.expanduser("~/.olx"); hf=self._hf or os.path.join(sd,"deals.json"); return sd,hf
        import olx_scraper; return olx_scraper.STATE_DIR, olx_scraper.HISTORY_FILE
    def load(self):
        sd,hf=self._paths()
        if not os.path.exists(hf): return []
        try:
            with open(hf) as f: d=json.load(f)
        except: return []
        if not isinstance(d,list): return []
        for r in d:
            if not r.get("product_id"): r["product_id"]="hd-6-12tb"; r["product_name"]=r.get("product_name") or "HD 6-12TB"
            if "matched" not in r: r["matched"]=True
            if "discard_reason" not in r: r["discard_reason"]=None if r.get("matched") else "price"
        return d
    def save(self, results):
        import olx_scraper
        sd,hf=self._paths()
        if self._sd or self._hf:
            old_sd,old_hf=olx_scraper.STATE_DIR, olx_scraper.HISTORY_FILE
            olx_scraper.STATE_DIR,olx_scraper.HISTORY_FILE=sd,hf
            try: return olx_scraper._orig_save_history(results)
            finally: olx_scraper.STATE_DIR,olx_scraper.HISTORY_FILE=old_sd,old_hf
        return olx_scraper._orig_save_history(results)
    def purge(self, pred, dry_run=False):
        import olx_scraper
        sd,hf=self._paths()
        if self._sd or self._hf:
            old_sd,old_hf=olx_scraper.STATE_DIR, olx_scraper.HISTORY_FILE
            olx_scraper.STATE_DIR,olx_scraper.HISTORY_FILE=sd,hf
            try: return olx_scraper._orig_purge_deals(pred,dry_run)
            finally: olx_scraper.STATE_DIR,olx_scraper.HISTORY_FILE=old_sd,old_hf
        return olx_scraper._orig_purge_deals(pred,dry_run)
class FakeDealsRepository:
    def __init__(self,i=None):
        import copy; self._c=copy; self._d=list(i) if i else []
    def load(self): return self._c.deepcopy(self._d)
    def save(self, results):
        by={}
        for r in self._d: by[f"{r.get('product_id','')}:{r.get('id')}"]=r
        new=[]
        for r in (results or []):
            k=f"{r.get('product_id','')}:{r.get('id')}"
            if k not in by: new.append(r)
            by[k]=r
        self._d=list(by.values())[-5000:]; return self._c.deepcopy(new)
    def purge(self,pred,dry_run=False):
        to_p=[d for d in self._d if pred(d)]; rem=[d for d in self._d if not pred(d)]
        if dry_run: return {"purged":len(to_p),"remaining":len(rem),"backup_path":None,"to_purge":self._c.deepcopy(to_p),"remaining_deals":self._c.deepcopy(rem)}
        self._d=self._c.deepcopy(rem); return {"purged":len(to_p),"remaining":len(rem),"backup_path":"/fake/bak","to_purge":self._c.deepcopy(to_p),"remaining_deals":self._c.deepcopy(rem)}
