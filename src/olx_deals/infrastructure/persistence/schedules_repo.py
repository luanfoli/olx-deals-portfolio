from typing import Protocol,runtime_checkable
import os
@runtime_checkable
class SchedulesRepository(Protocol):
    def load(self)->dict: ...
    def save(self,d:dict)->dict: ...
    def record(self,pid:str,status:str="ok",error:str=None,at_iso:str=None,matched_count:int=None,results_count:int=None)->dict: ...
    def get_last(self,pid:str)->dict|None: ...
class FileSchedulesRepository:
    def __init__(self,sd=None,sf=None,state_dir=None,schedules_file=None):
        self._sd=sd or state_dir; self._sf=sf or schedules_file
    def _paths(self):
        if self._sd or self._sf: sd=self._sd or os.path.expanduser("~/.olx"); sf=self._sf or os.path.join(sd,"schedules.json"); return sd,sf
        import olx_scraper; return olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE
    def load(self):
        import olx_scraper
        sd,sf=self._paths()
        if self._sd or self._sf:
            old=(olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE)
            olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE=sd,sf
            try: return olx_scraper._orig_load_schedules()
            finally: olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE=old
        return olx_scraper._orig_load_schedules()
    def save(self,data):
        import olx_scraper
        sd,sf=self._paths()
        if self._sd or self._sf:
            old=(olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE)
            olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE=sd,sf
            try: return olx_scraper._orig_save_schedules(data)
            finally: olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE=old
        return olx_scraper._orig_save_schedules(data)
    def record(self,pid,status="ok",error=None,at_iso=None,matched_count=None,results_count=None):
        import olx_scraper
        sd,sf=self._paths()
        if self._sd or self._sf:
            old=(olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE)
            olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE=sd,sf
            try: return olx_scraper._orig_record_schedule_run(pid,status,error,at_iso,matched_count,results_count)
            finally: olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE=old
        return olx_scraper._orig_record_schedule_run(pid,status,error,at_iso,matched_count,results_count)
    def get_last(self,pid):
        import olx_scraper
        sd,sf=self._paths()
        if self._sd or self._sf:
            old=(olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE)
            olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE=sd,sf
            try: return olx_scraper._orig_get_last_run(pid)
            finally: olx_scraper.STATE_DIR,olx_scraper.SCHEDULES_FILE=old
        return olx_scraper._orig_get_last_run(pid)
class FakeSchedulesRepository:
    def __init__(self,i=None):
        import copy; self._c=copy; self._d=dict(i) if i else {"last_runs":{}}
    def load(self): return self._c.deepcopy(self._d)
    def save(self,d): self._d=self._c.deepcopy(d); return self._c.deepcopy(self._d)
    def record(self,pid,status="ok",error=None,at_iso=None,matched_count=None,results_count=None):
        from datetime import datetime,timezone
        pid=str(pid or "").strip()
        if not pid: return
        if at_iso is None: at_iso=datetime.now(timezone.utc).isoformat()
        lr=self._d.setdefault("last_runs",{}); e=dict(lr.get(pid,{})); e["at"]=at_iso; e["status"]=status
        if error is not None: e["error"]=str(error)[:500] if error else None
        elif "error" in e and status=="ok": e.pop("error",None)
        if matched_count is not None: e["matched_count"]=int(matched_count)
        if results_count is not None: e["results_count"]=int(results_count)
        lr[pid]=e; return self._c.deepcopy(e)
    def get_last(self,pid): return self._c.deepcopy(self._d.get("last_runs",{}).get(str(pid)))
