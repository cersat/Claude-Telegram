#!/usr/bin/env python3
"""Общее хранилище запланированных пробуждений Claude — используется и ботом
(bot.py, фоновый scheduler_loop, читает и исполняет джобы), и MCP-сервером
(bot_scheduler_mcp_server.py, которым Claude сам создаёт/смотрит/отменяет
джобы из текущей темы). Два разных процесса, поэтому лок — файловый (fcntl),
не threading.Lock."""

import contextlib
import fcntl
import json
import os
import uuid

SCHEDULED_JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduled_jobs.json")
_LOCK_FILE = SCHEDULED_JOBS_FILE + ".lock"


@contextlib.contextmanager
def jobs_lock():
    """Открывает файл джобов под эксклюзивным flock-локом, отдаёт список jobs
    для правки, сохраняет обратно при выходе из блока (даже путём tmp+replace)."""
    with open(_LOCK_FILE, "a+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            if os.path.exists(SCHEDULED_JOBS_FILE):
                try:
                    with open(SCHEDULED_JOBS_FILE) as f:
                        jobs = json.load(f)
                except (ValueError, OSError):
                    jobs = []
            else:
                jobs = []
            yield jobs
            tmp = SCHEDULED_JOBS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(jobs, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SCHEDULED_JOBS_FILE)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def new_job_id():
    return uuid.uuid4().hex[:8]


def add_job(job):
    with jobs_lock() as jobs:
        jobs.append(job)
    return job


def list_jobs(chat_id=None, thread_id=None):
    with jobs_lock() as jobs:
        if chat_id is None:
            return list(jobs)
        return [j for j in jobs if j.get("chat_id") == chat_id and j.get("thread_id") == thread_id]


def cancel_job(job_id, chat_id=None, thread_id=None):
    """Удаляет джобу по id. Если переданы chat_id/thread_id — только если джоба
    принадлежит этой теме (чтобы одна тема не могла отменить чужую джобу)."""
    removed = [False]

    def _match(j):
        if j.get("id") != job_id:
            return False
        if chat_id is not None and (j.get("chat_id") != chat_id or j.get("thread_id") != thread_id):
            return False
        return True

    with jobs_lock() as jobs:
        for i, j in enumerate(jobs):
            if _match(j):
                jobs.pop(i)
                removed[0] = True
                break
    return removed[0]
