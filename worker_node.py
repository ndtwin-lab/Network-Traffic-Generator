"""
Copyright (c) 2025-present
 
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
 
  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

NDTwin core contributors (as of January 15, 2026):
    Prof. Shie-Yuan Wang <National Yang Ming Chiao Tung University; CITI, Academia Sinica> 
    Ms. Xiang-Ling Lin <CITI, Academia Sinica>
    Mr. Po-Yu Juan <CITI, Academia Sinica>
    Mr. Tsu-Li Mou <CITI, Academia Sinica> 
    Mr. Zhen-Rong Wu <National Taiwan Normal University>
    Mr. Ting-En Chang <University of Wisconsin, Milwaukee>
    Mr. Yu-Cheng Chen <National Yang Ming Chiao Tung University>
"""
import asyncio
import json
import os
import time
import uuid
import subprocess
from typing import Optional, Literal, Dict, List, Set
from concurrent.futures import ThreadPoolExecutor
import random

from fastapi import FastAPI, HTTPException
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field, validator
from loguru import logger

# ------------------------- Config -------------------------
RESULT_DIR = "./results"
IPERF3_BIN = os.getenv("IPERF3_BIN", "iperf3")

MAX_SENDER_CONCURRENCY = int(os.getenv("MAX_THREAD_CONCURRENCY", "500"))
MAX_RECEIVER_CONCURRENCY = int(os.getenv("MAX_THREAD_CONCURRENCY", "500"))
SHUTDOWN_FLUSH_SECS = float(os.getenv("SHUTDOWN_FLUSH_SECS", "2.0"))  # bounded wait
RETRIES = int(os.getenv("RETRIES", "10"))  # retry attempts for transient EAGAIN failures
BACKOFF_MIN_MS = float(os.getenv("BACKOFF_MIN_MS", "250.0"))
BACKOFF_MAX_MS = float(os.getenv("BACKOFF_MAX_MS", "500.0"))

os.makedirs(RESULT_DIR, exist_ok=True)

LOG_PATH = os.path.join(RESULT_DIR, "rest_server.log")
logger.remove()
logger.add(LOG_PATH, rotation="50 MB", retention=10, compression="gz", enqueue=True, backtrace=False, diagnose=False)
logger.add(lambda msg: print(msg, end=""), level="INFO")

app = FastAPI(title="Iperf3 Flow Orchestrator", version="2.3", default_response_class=ORJSONResponse)

# ------------------------- Models -------------------------
class SenderReq(BaseModel):
    c: str = Field(..., description="-c destination server address")
    port: int = Field(5201, ge=1, le=65535, description="-p destination server port")
    u: bool = Field(False, description="Use UDP if true, else TCP")
    B: Optional[str] = Field(None, description="-B local bind address")
    cport: Optional[int] = Field(None, ge=1, le=65535, description="--cport client local source port")
    b: Optional[str] = Field(None, description='-b sending rate like "100M"')
    n: Optional[str] = Field(None, description='-n total bytes like "1G"')
    t: Optional[int] = Field(None, ge=0, le=36000, description="-t duration seconds")
    start_time: Optional[str] = None
    iperf_version: Optional[str] = None
    fixed_traffic_duration: Optional[int] = None
    skip_resource_check: Optional[bool] = Field(False, description="Skip resource availability check")

    @validator("t")
    def check_n_t_exclusive(cls, v, values):
        if v is not None and values.get("n") is not None:
            raise ValueError("-n and -t are mutually exclusive")
        return v

class ReceiverReq(BaseModel):
    bind: Optional[str] = Field(None, description="-B bind address")
    port: int = Field(5201, ge=1, le=65535, description="-p listen port")
    u: bool = Field(False, description="UDP server if true, else TCP")
    one_off: bool = Field(True, description="Ignored. Always -1 enforced.")
    start_time: Optional[str] = None
    fixed_traffic_duration: Optional[int] = None
    wait_offset: Optional[float] = 0.0
    iperf_version: Optional[str] = None
    skip_resource_check: Optional[bool] = Field(False, description="Skip resource availability check")

class SenderReqs(BaseModel):
    reqs: List[SenderReq] = Field(default_factory=list)

class ReceiverReqs(BaseModel):
    reqs: List[ReceiverReq] = Field(default_factory=list)

class JobStatus(BaseModel):
    job_id: str
    role: Literal["sender", "receiver"]
    pid: Optional[int]
    cmd: List[str]
    started_at: float
    ended_at: Optional[float]
    returncode: Optional[int]
    result_path: Optional[str]
    status: Literal["running", "finished", "failed", "stopped"]
    ip: Optional[str] = None  # h1,h2.... not full ip
    port: Optional[int] = None

# ------------------------- State -------------------------
JOBS: Dict[str, JobStatus] = {}
PROCS: Dict[str, subprocess.Popen] = {}
RUNNERS: Dict[str, asyncio.Task] = {}
SENDER_SEM = asyncio.Semaphore(MAX_SENDER_CONCURRENCY)
RECEIVER_SEM = asyncio.Semaphore(MAX_RECEIVER_CONCURRENCY)
LEASES: Dict[tuple, float] = {}
STOP_REASON: Dict[str, str] = {}  # job_id -> reason
FINISHED_PORTS: Dict[str, Set[int]] = {}  # ip -> set of finished ports

# ------------------------- Helpers -------------------------

def _mark_finished_receiver_port(job: 'JobStatus') -> None:
    """Mark a finished/stopped receiver port in the in-memory dictionary."""
    try:
        if not (job and job.role == "receiver" and job.ip and job.port is not None and job.status in ("finished", "failed", "stopped")):
            return
        if job.ip not in FINISHED_PORTS:
            FINISHED_PORTS[job.ip] = set()
        FINISHED_PORTS[job.ip].add(job.port)
    except Exception as e:
        try:
            logger.warning(f"[mark-finished] failed to mark port for job={getattr(job,'job_id',None)}: {e}")
        except Exception:
            pass

def _new_job(role: Literal["sender", "receiver"], cmd: List[str], result_path: str, ip: Optional[str] = None, port: Optional[int] = None, start_t: Optional[float] = None) -> JobStatus:
    job_id = uuid.uuid4().hex
    js = JobStatus(
        job_id=job_id, role=role, pid=None, cmd=cmd,
        started_at=(time.perf_counter_ns() if start_t is None else start_t), ended_at=None, returncode=None,
        result_path=result_path, status="running",
        ip=ip, port=port
    )
    JOBS[job_id] = js
    # logger.info(f"[{role}] job={job_id} created cmd={cmd} result={result_path}")
    return js

async def _terminate_proc(job_id: str, reason: str = "server_shutdown", timeout: float = 2.0):
    job = JOBS.get(job_id)
    proc = PROCS.get(job_id)

    if not job or not proc or proc.poll() is not None:
        return
    
    STOP_REASON[job_id] = reason
    loop = asyncio.get_event_loop()
    
    try:
        proc.terminate()
    except ProcessLookupError:
        pass  # Already terminated

    try:
        await asyncio.wait_for(loop.run_in_executor(THREAD_EXECUTOR, proc.wait), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # Already killed
        try:
            # Final wait after kill
            await asyncio.wait_for(loop.run_in_executor(THREAD_EXECUTOR, proc.wait), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning(f"[shutdown] failed to kill job={job_id} pid={proc.pid}")

    job.status = "stopped"
    job.ended_at = time.time()
    job.returncode = proc.returncode

# ------------------------- ThreadPoolExecutor for subprocess management -------------------------
# Global thread pool executor for subprocess operations (reduced workers to prevent resource exhaustion)
THREAD_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_SENDER_CONCURRENCY+MAX_RECEIVER_CONCURRENCY)
# Dedicated executors per role to mirror test.py's per-executor gating
SENDER_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_SENDER_CONCURRENCY)
RECEIVER_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_RECEIVER_CONCURRENCY)


def _start_subprocess_and_wait(cmd: List[str], job_id: str ,re = False) -> tuple:
    """Start a subprocess and wait for it to complete."""
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Associate process with job_id for potential termination
        if process.pid:
            PROCS[job_id] = process
            JOBS[job_id].pid = process.pid
        
        
        stdout, stderr = process.communicate()
        return process.returncode, stdout, stderr
    except Exception as e:
        logger.error(f"Exception in _start_subprocess_and_wait for job {job_id}: {e}")
        return -1, "", str(e)

def _json_text_mentions_eagain(out: Optional[str], err: Optional[str]) -> bool:
    """Detect EAGAIN-like errors in captured stdout/stderr or JSON 'error' field."""
    text = (out or "") + "\n" + (err or "")
    markers = (
        "Resource temporarily unavailable",
        "Connection refused",
    )
    for m in markers:
        if m in text:
            # logger.info(f"get error : {m}")
            return True
    # Try parse JSON and check error field if present
    try:
        if out:
            obj = json.loads(out)
            errf = obj.get("error")
            if isinstance(errf, str):
                for m in markers:
                    if m in errf:
                        return True
    except Exception:
        pass
    return False

def _cmd_has_one_off(cmd: List[str]) -> bool:
    """Detect if the receiver command uses --one-off (-1)."""
    return ("-1" in cmd) or ("--one-off" in cmd)

def _start_receiver_with_persistence(cmd: List[str], job_id: str) -> tuple:
    attempt = 0
    rc, out, err = _start_subprocess_and_wait(cmd, job_id, (attempt>1))
    return rc, out, err


def _start_subprocess_and_wait_with_retry(cmd: List[str], job_id: str) -> tuple:
    """Run iperf3 with limited retries if EAGAIN occurs. Runs synchronously in a worker thread."""
    attempts = max(1,  RETRIES + 1)
    last_rc, last_out, last_err = -1, "", ""
    for i in range(attempts):
        rc, out, err = _start_subprocess_and_wait(cmd, job_id,(i>=1))
        last_rc, last_out, last_err = rc, out, err
        if rc == 0:
            return rc, out, err
        # Check for EAGAIN signature
        err_is_eagain = False
        if _json_text_mentions_eagain(out, err):
            err_is_eagain = True
        if err_is_eagain:
            logger.info("failed...")
            if i < attempts - 1:
                backoff_ms = random.uniform( BACKOFF_MIN_MS,  BACKOFF_MAX_MS)
                try:
                    time.sleep(backoff_ms / 1000.0)
                except Exception:
                    pass
                continue
        # Non-EAGAIN or out of retries
        break
    return last_rc, last_out, last_err

def _start_receiver_with_timeout(cmd: List[str], job_id: str, fixed_traffic_duration: int, wait_offset: float) -> tuple:
    """Start iperf3 server and wait for (fixed_traffic_duration + wait_offset) seconds, then terminate it.
    This function starts the iperf3 server, waits for the specified time duration, and then shuts it down.
    """
    try:
        # Start the iperf3 server process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Associate process with job_id for potential termination
        if process.pid:
            PROCS[job_id] = process
            JOBS[job_id].pid = process.pid
        
        # Wait for (fixed_traffic_duration + wait_offset) seconds
        total_wait_time = fixed_traffic_duration + wait_offset
        
        
        # Time is up, terminate the process
        try:
            # Give it a moment to terminate gracefully
            try:
                stdout, stderr = process.communicate(timeout=total_wait_time)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate gracefully
                process.kill()
                stdout, stderr = process.communicate()
            return process.returncode, stdout, stderr
        except Exception as e:
            logger.warning(f"[receiver_timeout] error terminating process for job={job_id}: {e}")
            return -1, "", str(e)
            
    except Exception as e:
        logger.error(f"Exception in _start_receiver_with_timeout for job {job_id}: {e}")
        return -1, "", str(e)



# ------------------------- Runner -------------------------
async def _run_and_capture_json(job_id: str, cmd: List[str], sem: asyncio.Semaphore, fixed_traffic_duration: Optional[int] = None, wait_offset: Optional[float] = 0.0, test_time: Optional[float] = None):
    async with sem:
        rc = -1
        stdout = ""
        stderr = ""
        
        try:
            if fixed_traffic_duration is None:
                
                loop = asyncio.get_event_loop()
                # Choose executor per role to match test.py pattern
                exec_pool = SENDER_EXECUTOR if JOBS[job_id].role == "sender" else RECEIVER_EXECUTOR
                # For receivers using one-off, keep relaunching until success; otherwise use standard runner
                runner_fn = _start_subprocess_and_wait_with_retry
                if JOBS[job_id].role == "receiver" and _cmd_has_one_off(cmd):
                    runner_fn = _start_receiver_with_persistence

                logger.info(f"[{JOBS[job_id].role}] job={job_id} running command: {' '.join(cmd)}")
                rc, stdout, stderr = await loop.run_in_executor(
                    exec_pool,
                    runner_fn,
                    cmd, job_id
                )

            else:

                if JOBS[job_id].role == "sender":
                    cur_time = time.perf_counter_ns()
                    logger.info(f"[fixed_traffic_sender] job={job_id}, cmd={' '.join(cmd)} running for {fixed_traffic_duration} seconds")
                    while time.perf_counter_ns() - cur_time < fixed_traffic_duration * 1e9:
                        loop = asyncio.get_event_loop()
                        rc,stdout_temp,stderr_temp = await loop.run_in_executor(
                            THREAD_EXECUTOR,
                            _start_subprocess_and_wait,
                            cmd,job_id
                        )

                        stdout = stdout + stdout_temp
                        stderr = stderr + stderr_temp
                        

                elif JOBS[job_id].role == "receiver":
                    logger.info(f"[fixed_traffic_receiver] job={job_id}, cmd={' '.join(cmd)} running for {fixed_traffic_duration + wait_offset} seconds")
                    
                    loop = asyncio.get_event_loop()
                    # Start iperf3 server and wait for (fixed_traffic_duration + wait_offset) seconds before shutting down
                    rc, stdout, stderr = await loop.run_in_executor(
                        RECEIVER_EXECUTOR,
                        _start_receiver_with_timeout,
                        cmd, job_id, fixed_traffic_duration, wait_offset
                    )


        except asyncio.CancelledError:
            logger.warning(f"[cancel] job={job_id} cancelling")
            STOP_REASON[job_id] = "task_cancelled"
            rc = -1
        except Exception as e:
            logger.exception(f"[error] job={job_id} err={e}, and {stdout}")
            stderr = str(e)
            rc = -1
        finally:
            job = JOBS.get(job_id)
            
            if job:
                if job.status != "stopped":
                    job.status = "finished" if rc == 0 else "failed"
                job.returncode = rc
                job.ended_at = time.time()
                # Persist the captured stdout JSON to the job's result_path
                if stdout is not None and job.result_path:
                    try:
                        # Ensure directory exists (should already, but be safe)
                        os.makedirs(os.path.dirname(job.result_path), exist_ok=True)
                        with open(job.result_path, "w") as f:
                            f.write(stdout)
                        logger.info(f"[flowEnd] job={job_id} result written to {job.result_path}")
                    except Exception as e:
                        logger.warning(f"[flowEnd] failed to write result for job={job_id}: {e}")
                # Mark finished receiver port in memory
                try:
                    _mark_finished_receiver_port(job)
                except Exception:
                    pass

            PROCS.pop(job_id, None)
            RUNNERS.pop(job_id, None)

# ------------------------- Endpoints -------------------------
@app.post("/flow/sender", response_model=List[str])
async def start_senders(reqs: SenderReqs):
    
    jobs = []
    
    async def _start_single_sender(req: SenderReq):
        test_time = time.perf_counter_ns()
        if not os.path.exists(RESULT_DIR+f"/{req.start_time}"):
            os.makedirs(RESULT_DIR+f"/{req.start_time}",exist_ok=True)

        base = f"{RESULT_DIR}/{req.start_time}/sender_{req.c.replace('.', '_')}_{req.port}_{'udp' if req.u else 'tcp'}_{int(time.time())}.json"
        
        iperf = req.iperf_version if req.iperf_version else "iperf3"
        cmd: List[str] = [iperf, "-c", req.c,
                           "-p", str(req.port),
                           "-J",
                           "-4",
                           "-l","10K"
                        ]

        if req.u:
            cmd += ["-u"]
        if req.B:
            cmd += ["-B", req.B]
        if req.b:
            cmd += ["-b", req.b]
        if req.n is not None:
            cmd += ["-n", req.n]
        if req.t is not None:
            cmd += ["-t", str(req.t)]

        job = _new_job("sender", cmd, base)
        RUNNERS[job.job_id] = asyncio.create_task(_run_and_capture_json(job.job_id, cmd, SENDER_SEM, req.fixed_traffic_duration,test_time=test_time))
        return job.job_id

    if reqs.reqs:
        tasks = [_start_single_sender(req) for req in reqs.reqs]
        jobs = await asyncio.gather(*tasks)
        
    return jobs

@app.post("/flow/receiver", response_model=List[str])
async def start_receivers(reqs: ReceiverReqs):

    jobs = []

    async def _start_single_receiver(req: ReceiverReq):
        test_time = time.perf_counter_ns()
        
        if not os.path.exists(RESULT_DIR+f"/{req.start_time}"):
            os.makedirs(RESULT_DIR+f"/{req.start_time}",exist_ok=True)

        base = f"{RESULT_DIR}/{req.start_time}/receiver_{(req.bind or '0.0.0.0').replace('.','_')}_{req.port}_{'udp' if req.u else 'tcp'}_{int(time.time())}.json"
        iperf = req.iperf_version if req.iperf_version else "iperf3"
        cmd: List[str] = [iperf, "-s", "-p", str(req.port), "-J", "-4",
                        ]
        fixed_traffic_duration = req.fixed_traffic_duration
        if req.bind:
            cmd += ["-B", req.bind]
        if req.u:
            cmd += ["-u"]
        if req.one_off:
            fixed_traffic_duration = None
            cmd += ["-1"]

        ip = ("h"+req.bind.split(".")[-1])
        port = req.port

        job = _new_job("receiver", cmd, base, ip, port)
        RUNNERS[job.job_id] = asyncio.create_task(_run_and_capture_json(job.job_id, cmd, RECEIVER_SEM, fixed_traffic_duration,req.wait_offset,test_time))
        return job.job_id

    if reqs.reqs:
        tasks = [_start_single_receiver(req) for req in reqs.reqs]
        jobs = await asyncio.gather(*tasks)
    return jobs

@app.get("/flow/finished_ports", response_model=Dict[str, List[int]])
async def get_finished_ports():
    """Get finished receiver ports from in-memory dictionary and clear it."""
    result = {ip: sorted(list(ports)) for ip, ports in FINISHED_PORTS.items()}
    FINISHED_PORTS.clear()
    return result

@app.get("/jobs", response_model=List[JobStatus])
async def list_jobs():
    return list(JOBS.values())

@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job

@app.post("/jobs/{job_id}/stop", response_model=JobStatus)
async def stop_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    proc = PROCS.get(job_id)
    if not proc or proc.returncode is not None:
        return job
    await _terminate_proc(job_id, "manual_stop")
    logger.info(f"[stopped] job={job_id}")
    return JOBS[job_id]

@app.post("/jobs/stop_all", response_model=bool)
async def stop_all_jobs():
    running_ids = [jid for jid, j in JOBS.items() if j.status == "running"]
    logger.warning(f"[stop_all] stopping {len(running_ids)} running jobs")
    await asyncio.gather(*[_terminate_proc(jid, "manual_stop_all") for jid in running_ids], return_exceptions=True)
    STOP_REASON.clear()
    return True

@app.get("/health")
async def health():
    running = sum(1 for j in JOBS.values() if j.status == "running")
    return {"ok": True, "inflight": running, "results_dir": os.path.abspath(RESULT_DIR)}


# ------------------------- Lifecycle -------------------------
@app.on_event("shutdown")
async def _on_shutdown():
    running_ids = [jid for jid, j in JOBS.items() if j.status == "running"]
    logger.warning(f"[shutdown] stopping {len(running_ids)} running jobs")
    await asyncio.gather(*[_terminate_proc(jid, "server_shutdown") for jid in running_ids], return_exceptions=True)
    # bounded wait for runners to flush
    if RUNNERS:
        try:
            await asyncio.wait_for(asyncio.gather(*RUNNERS.values(), return_exceptions=True), timeout=SHUTDOWN_FLUSH_SECS)
        except asyncio.TimeoutError:
            logger.warning("[shutdown] flush timeout; exiting")
    # Shutdown thread pool executors
    THREAD_EXECUTOR.shutdown(wait=True)
    SENDER_EXECUTOR.shutdown(wait=True)
    RECEIVER_EXECUTOR.shutdown(wait=True)
