from typing import Protocol,runtime_checkable
import os
@runtime_checkable
class RunsRepository(Protocol):
    def load(self)->list: ...
    def save(self,r:list)->list: ...
    def append(self,e:dict)->dict: ...
    def get(self,i:str)->dict|None: ...
    def delete(self,before=None,product_id=None,status=None,source=None)->int: ...
class FileRunsRepository:
    def __init__(self,sd=None,rf=None,lf=None,state_dir=None,runs_file=None,lock_file=None):
        self._sd=sd or state_dir; self._rf=rf or runs_file; self._lf=lf or lock_file
    def _paths(self):
        if self._sd or self._rf: sd=self._sd or os.path.expanduser("~/.olx"); rf=self._rf or os.path.join(sd,"runs.json"); lf=self._lf or os.path.join(sd,".runs.lock"); return sd,rf,lf
        import olx_scraper; return olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE
    def load(self):
        import olx_scraper
        sd,rf,lf=self._paths()
        if self._sd or self._rf:
            old=(olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE)
            olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE=sd,rf,lf
            try: return olx_scraper._orig_load_runs()
            finally: olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE=old
        return olx_scraper._orig_load_runs()
    def save(self,runs):
        import olx_scraper
        sd,rf,lf=self._paths()
        if self._sd or self._rf:
            old=(olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE)
            olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE=sd,rf,lf
            try: return olx_scraper._orig_save_runs(runs)
            finally: olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE=old
        return olx_scraper._orig_save_runs(runs)
    def append(self,e):
        import olx_scraper
        sd,rf,lf=self._paths()
        if self._sd or self._rf:
            old=(olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE)
            olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE=sd,rf,lf
            try: return olx_scraper._orig_append_run(e)
            finally: olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE=old
        return olx_scraper._orig_append_run(e)
    def get(self,rid):
        import olx_scraper
        sd,rf,lf=self._paths()
        if self._sd or self._rf:
            old=(olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE)
            olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE=sd,rf,lf
            try: return olx_scraper._orig_get_run(rid)
            finally: olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE=old
        return olx_scraper._orig_get_run(rid)
    def delete(self,before=None,product_id=None,status=None,source=None):
        import olx_scraper
        sd,rf,lf=self._paths()
        if self._sd or self._rf:
            old=(olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE)
            olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE=sd,rf,lf
            try: return olx_scraper._orig_delete_runs(before,product_id,status,source)
            finally: olx_scraper.STATE_DIR,olx_scraper.RUNS_FILE,olx_scraper.RUNS_LOCK_FILE=old
        return olx_scraper._orig_delete_runs(before,product_id,status,source)
class FakeRunsRepository:
    def __init__(self,i=None):
        import copy; self._c=copy; self._d=list(i) if i else []
    def load(self): return self._c.deepcopy(self._d)
    def save(self,r):
        if not isinstance(r,list): raise ValueError()
        self._d=self._c.deepcopy(r[-1000:] if len(r)>1000 else r); return self._c.deepcopy(self._d)
    def append(self,e):
        if not isinstance(e,dict) or not e.get("id"): raise ValueError()
        self._d.append(dict(e)); return dict(e)
    def get(self,rid):
        rid=str(rid or "").strip()
        for x in self._d:
            if x.get("id")==rid: return self._c.deepcopy(x)
        return None
    def delete(self,before=None,product_id=None,status=None,source=None):
        keep=[]; rem=0
        for r in list(self._d):
            act=[]
            if before: act.append(True)
            if product_id: act.append(product_id in set(str(x) for x in (r.get("product_ids_scraped") or [])+(r.get("product_ids_requested") or [])))
            if status: act.append(r.get("status")==status)
            if source: act.append(r.get("source")==source)
            if not act: keep.append(r); continue
            if all(act): rem+=1
            else: keep.append(r)
        self._d=keep; return rem
