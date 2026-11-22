"""Process queued recordings in a restartable worker, independent of web requests."""
import logging
import signal
import time
from contextlib import closing

from dean.ai import RouterAI
from dean.clock import now
from dean.db import connect, database_path, write
from dean.domain import identifier, timestamp
from dean.lecture_channel import ANALYSIS_ATTEMPTS, ANALYSIS_INTERRUPTED, ANALYSIS_LEASE, analysis_job

LOG = logging.getLogger(__name__)


def claim(path):
    with closing(connect(path, create=False)) as db, write(db):
        moment = now()
        abandoned = db.execute("""UPDATE lab_lecture SET analysis_until=NULL,analysis_token=NULL,
                                analysis_status=NULL,analysis_error=?
                                WHERE analysis_until<=? AND id IN (
                                    SELECT lecture_id FROM lecture_analysis_job WHERE attempts>=?
                                ) RETURNING id""",
                               (ANALYSIS_INTERRUPTED,timestamp(moment),ANALYSIS_ATTEMPTS)).fetchall()
        db.executemany("DELETE FROM lecture_analysis_job WHERE lecture_id=?", abandoned)
        job = db.execute("""SELECT j.* FROM lecture_analysis_job j JOIN lab_lecture l ON l.id=j.lecture_id
                         WHERE j.attempts<? AND (l.analysis_until IS NULL OR l.analysis_until<=?)
                         ORDER BY j.queued,j.lecture_id LIMIT 1""",
                         (ANALYSIS_ATTEMPTS,timestamp(moment))).fetchone()
        if job is None:
            return None
        token = identifier()
        stage = "Возобновляем обработку" if job["attempts"] else "Готовим запись"
        db.execute("""UPDATE lab_lecture SET analysis_until=?,analysis_token=?,analysis_status=?
                      WHERE id=?""",(timestamp(moment+ANALYSIS_LEASE),token,stage,job["lecture_id"]))
        db.execute("UPDATE lecture_analysis_job SET attempts=attempts+1 WHERE lecture_id=?",(job["lecture_id"],))
        return job["lecture_id"], token, bool(job["cloud"]), bool(job["use_model"])


def main():
    path=database_path()
    engine=RouterAI.from_env()
    while True:
        task=claim(path)
        if not task:
            time.sleep(2)
            continue
        lid, token, cloud, use_model=task
        LOG.info("Processing queued lecture")
        analysis_job(path,lid,token,engine,cloud,use_model)


def stop(_signum, _frame):
    raise SystemExit(0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, stop)
    main()
