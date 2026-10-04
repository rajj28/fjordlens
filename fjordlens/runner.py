"""Deadline-safe batch runner: every input always receives exactly one terminal envelope.

Pipeline per company: official stage -> (filing-history slow lane, website stage). The main
thread is a watchdog: it never waits past the wall-clock budget, rewrites provisional outputs
while work is in flight, and finalizes from consistent profile copies even if a worker hangs.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath, PureWindowsPath
import copy
import gzip
import json
import os
import platform
import re
import shutil
import statistics
import threading
import time
import zlib
from . import __version__, official, website
from . import jobs as nav_jobs
from .core import Profile, digest, org_number, now, provenance, validate, write_json
from .net import Budget, Fetcher, read_snapshot_bytes, snapshot_extension
from .refresh import refresh
from .synthesis import summarize

DEADLINE_REASON = "Run time budget ended before this source was checked"

ORG_KEYS = ("organisation_number", "organization_number", "organisasjonsnummer", "orgnr", "org_nr", "orgnummer", "org")
ORG_HEADER = re.compile(r"^\s*(organi[sz]ation[ _-]?(number|no|nr)|organisasjonsnummer|org[ _.-]?(nr|nummer|number|no)?)\s*$", re.I)


def _org_from_row(row):
    if isinstance(row, dict):
        for key in ORG_KEYS:
            if row.get(key) not in (None, ""):
                return str(row[key])
        lowered = {str(k).lower(): v for k, v in row.items()}
        return str(next((lowered[k] for k in ORG_KEYS if lowered.get(k) not in (None, "")), ""))
    return str(row)


def read_inputs(path):
    """Every input row becomes exactly one entry; parsing problems never raise. JSON array/object,
    JSONL, CSV/TSV (with or without a quoted header) and plain text are accepted."""
    import csv
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    stripped = text.strip()
    data = None
    if stripped.startswith(("[", "{")):
        try:
            whole = json.loads(stripped)
            if isinstance(whole, dict):
                whole = next((whole[k] for k in ("organisation_numbers", "companies", "organisations", "inputs") if isinstance(whole.get(k), list)), [whole])
            data = whole if isinstance(whole, list) else [whole]
        except ValueError:
            data = None  # Probably JSONL: fall through to line parsing.
    if data is None:
        lines = [line for line in text.splitlines() if line.strip()]
        column = None
        if lines:
            dialect_delimiter = "\t" if "\t" in lines[0] else (";" if lines[0].count(";") > lines[0].count(",") else ",")
            try:
                header = next(csv.reader([lines[0]], delimiter=dialect_delimiter))
            except csv.Error:
                header = []
            column = next((i for i, cell in enumerate(header) if ORG_HEADER.match(cell or "")), None)
            if column is not None:
                lines = lines[1:]
        data = []
        for line in lines:
            item = line.strip()
            if column is None and item.startswith("{"):
                try:
                    data.append(json.loads(item))
                    continue
                except ValueError:
                    pass
            if column is None and item.startswith('"') and item.endswith('"') and item.count('"') == 2:
                try:
                    data.append(json.loads(item))
                    continue
                except ValueError:
                    pass
            try:
                cells = next(csv.reader([item], delimiter=dialect_delimiter))
                data.append((cells[column] if column is not None and column < len(cells) else (cells[0] if cells else item)).strip())
            except (csv.Error, StopIteration):
                data.append(item)
    normalized = []
    for row in data:
        value = _org_from_row(row)
        try:
            normalized.append(org_number(value))
        except ValueError:
            normalized.append(value)  # Invalid inputs still receive a failed terminal envelope.
    return normalized

def read_previous(path):
    opener = gzip.open if str(path).lower().endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    return {row["organisation_number"]: row for row in rows}

def snapshot_path(root, relative):
    """Resolve only package-local paths, including on Windows and through symlinks."""
    if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
            or any(ord(char) < 32 for char in relative)
            or PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).drive
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise ValueError("Snapshot storage path must be a safe relative package path")
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Snapshot storage path escapes its package")
    return path

def preserve_previous_snapshots(profile, previous, previous_root, fetcher):
    """Carry immutable evidence into this run without claiming unavailable bytes exist."""
    if not previous:
        return
    previous_ids = {s["id"] for s in previous["source_snapshots"]}
    evidence_ids = {eid for claim in profile["claims"] for eid in claim.get("evidence_ids", [])}
    required_snapshots = set()
    for change in profile["changes"]:
        evidence_ids.update(change.get("previous_evidence_ids", []))
        evidence_ids.update(change.get("current_evidence_ids", []))
        required_snapshots.update(change.get("current_source_snapshot_ids", []))
    required_snapshots.update(e["snapshot_id"] for e in profile["evidence"] if e["id"] in evidence_ids)
    for snapshot in profile["source_snapshots"]:
        sid = snapshot["id"]
        if sid not in previous_ids:
            continue
        stored_path = snapshot.get("storage_path")
        record = dict(snapshot)
        record["headers"] = {header: snapshot[key] for key, header in (
            ("content_type", "content-type"), ("etag", "etag"), ("last_modified", "last-modified"))
            if snapshot.get(key) is not None}
        try:
            if not isinstance(sid, str) or not re.fullmatch(r"ss_[0-9a-f]{24}", sid):
                raise ValueError("Invalid snapshot identifier")
            metadata_path = snapshot_path(previous_root, f"snapshots/{sid}.json")
            if metadata_path.exists():
                saved = json.loads(metadata_path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    raise ValueError("Saved snapshot metadata is malformed")
                for key in ("id", "content_sha256", "requested_url", "final_url", "http_status", "retrieved_at", "redirect_chain"):
                    if saved.get(key) != snapshot.get(key):
                        raise ValueError("Saved snapshot metadata differs from the previous envelope")
                record = {**saved, **record, "headers": {**saved.get("headers", {}), **record["headers"]}}
            if not stored_path:
                raise ValueError("Response has no saved snapshot body")
            source = snapshot_path(previous_root, stored_path)
            if not source.is_file():
                raise ValueError("Saved snapshot body is missing from the previous package")
            body = read_snapshot_bytes(source)
            sha = snapshot.get("content_sha256", "")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha) or digest(body) != sha:
                raise ValueError("Saved snapshot body failed SHA-256 verification")
            # Re-stored raw, so the preserved file hashes to its content_sha256 like every new capture.
            destination_relative = f"snapshots/{sha}.{snapshot_extension(snapshot.get('content_type'))}"
            destination = snapshot_path(fetcher.root, destination_relative)
            with fetcher.host_lock(sha):
                if destination.exists():
                    if digest(destination.read_bytes()) != sha:
                        raise ValueError("Destination snapshot body failed SHA-256 verification")
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(body)
            snapshot["storage_path"] = destination_relative
            snapshot["audit_availability"] = {"state": "available", "reason": "Saved body retained and SHA-256 verified"}
        except (OSError, ValueError, TypeError, EOFError, zlib.error) as exc:
            snapshot["storage_path"] = None
            if stored_path:
                snapshot["previous_storage_path"] = stored_path
            required = sid in required_snapshots
            state = "unavailable" if required or stored_path else "metadata_only"
            snapshot["audit_availability"] = {"state": state, "reason": str(exc)[:300]}
            if required:
                profile["errors"].append({"family": "evidence_storage", "type": "snapshot_unavailable",
                                          "snapshot_id": sid, "message": str(exc)[:300]})
                profile["run"]["terminal_status"] = "failed"
        record.update(snapshot)
        if isinstance(sid, str) and re.fullmatch(r"ss_[0-9a-f]{24}", sid):
            write_json(snapshot_path(fetcher.root, f"snapshots/{sid}.json"), record)

def resolve_limits(count, *, seconds=None, max_requests=None, per_company=None):
    """Explicit flags win, then documented environment variables, then size-scaled defaults."""
    env = os.environ
    def pick(value, names, default):
        if value is not None:
            return value
        for name in names:
            if env.get(name, "").strip():
                return float(env[name]) if "." in env[name] else int(env[name])
        return default
    seconds = pick(seconds, ("SIGNALPOST_TIME_BUDGET_SECONDS", "FJORDLENS_SECONDS"), 2600)
    max_requests = pick(max_requests, ("SIGNALPOST_MAX_REQUESTS", "FJORDLENS_MAX_REQUESTS"), max(2000, 30 * max(1, count)))
    per_company = pick(per_company, ("FJORDLENS_PER_COMPANY",), 40)
    if seconds <= 0 or max_requests <= 0 or per_company <= 0:
        raise ValueError("Run limits must be positive")
    return seconds, int(max_requests), int(per_company)

class Job:
    """One input row's research state. All profile mutation happens under profile.lock."""
    def __init__(self, index, org, run_id, previous):
        self.index, self.org, self.previous = index, org, previous
        self.profile = Profile(org, run_id)
        self.entity = None
        self.pending = 0
        self.started = time.monotonic()
        self.finished = None
        self.stages = []
        self.copies = []  # duplicate input rows sharing this research

    def failure(self, family, exc):
        with self.profile.lock:
            self.profile.state(family, "failed", f"Connector error: {type(exc).__name__}")
            self.profile.data["errors"].append({"family": family, "type": type(exc).__name__, "message": str(exc)[:300]})

class Pipeline:
    def __init__(self, jobs, fetcher, *, max_pages, workers):
        self.jobs, self.fetcher, self.max_pages = jobs, fetcher, max_pages
        self.cv = threading.Condition()
        workers = max(1, workers)
        self.official = ThreadPoolExecutor(max_workers=min(12, workers), thread_name_prefix="official")
        self.web = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="web")
        self.slow = ThreadPoolExecutor(max_workers=1, thread_name_prefix="filings")
        self.hiring = ThreadPoolExecutor(max_workers=min(4, workers), thread_name_prefix="hiring")
        self.nav = nav_jobs.NavJobs(fetcher, live=bool(getattr(fetcher, "dns_enabled", False)))
        if isinstance(getattr(fetcher, "caches", None), dict):
            fetcher.caches.setdefault("nav_homepages", self.nav.homepages())
        self.nav_waiting = []
        self.closed = False

    def submit(self, pool, stage, job):
        with self.cv:
            if self.closed:
                return
            job.pending += 1
        def run():
            try:
                stage(job)
            except Exception as exc:  # A stage bug never escapes into the batch.
                job.failure("identity" if stage == self.official_stage else "website", exc)
            finally:
                with self.cv:
                    job.pending -= 1
                    job.stages.append(stage.__name__)
                    if job.pending == 0:
                        job.finished = time.monotonic()
                    self.cv.notify_all()
        try:
            pool.submit(run)
        except RuntimeError:  # Pool already shut down at the deadline.
            with self.cv:
                job.pending -= 1
                self.cv.notify_all()

    def official_stage(self, job):
        profile, fetcher = job.profile, self.fetcher
        try:
            if job.previous is not None and fetcher.cutoff is not None:
                # Reject future or ambiguous prior evidence before any connector can fetch.
                refresh(Profile(job.org, profile.data["run"]["run_id"]).data, job.previous, cutoff=fetcher.cutoff)
            org = org_number(job.org)
            profile.data["organisation_number"] = org
            entity = official.identity(profile, fetcher)
        except Exception as exc:
            with profile.lock:
                profile.data["run"]["terminal_status"] = "failed"
                profile.state("identity", "failed", str(exc)[:250])
                profile.data["errors"].append({"family": "identity", "type": type(exc).__name__, "message": str(exc)[:250]})
            return
        job.entity = entity
        if not entity:
            with profile.lock:
                for family in profile.data["availability"]:
                    if family != "identity":
                        profile.state(family, "blocked", "Research requires an exact official identity anchor")
            return
        for family, function in (("financials", official.financials), ("leadership", official.leadership),
                                 ("locations", official.locations), ("group", lambda p, f: official.group(p, f, entity))):
            try:
                function(profile, fetcher)
            except Exception as exc:
                job.failure(family, exc)
        self.submit(self.slow, self.filings_stage, job)
        self.submit(self.web, self.website_stage, job)
        with self.cv:
            ready = self.nav.ready.is_set()
            if not ready:
                job.pending += 1  # Held until the shared job-feed catch-up finishes.
                self.nav_waiting.append(job)
        if ready:
            self.submit(self.hiring, self.hiring_stage, job)

    def filings_stage(self, job):
        try:
            official.history(job.profile, self.fetcher)
        except Exception as exc:
            job.failure("filing_history", exc)

    def hiring_stage(self, job):
        try:
            nav_jobs.research(job.profile, self.fetcher, self.nav, job.entity)
        except Exception as exc:
            job.failure("hiring", exc)

    def nav_refresh(self):
        try:
            self.nav.refresh()
        finally:
            self.nav.ready.set()
            with self.cv:
                waiting, self.nav_waiting = self.nav_waiting, []
            for job in waiting:
                self.submit(self.hiring, self.hiring_stage, job)
                with self.cv:
                    job.pending -= 1
                    if job.pending == 0:
                        job.finished = time.monotonic()
                    self.cv.notify_all()

    def website_stage(self, job):
        try:
            website.research(job.profile, self.fetcher, job.entity, self.max_pages)
        except Exception as exc:
            job.failure("website", exc)

    def start(self):
        threading.Thread(target=self.nav_refresh, name="job-feed", daemon=True).start()
        for job in self.jobs:
            self.submit(self.official, self.official_stage, job)

    def busy(self):
        with self.cv:
            return sum(job.pending > 0 for job in self.jobs)

    def wait(self, until, tick):
        with self.cv:
            remaining = until - time.monotonic()
            if remaining > 0 and any(job.pending for job in self.jobs):
                self.cv.wait(min(tick, remaining))

    def close(self):
        with self.cv:
            self.closed = True
        for pool in (self.official, self.web, self.slow, self.hiring):
            pool.shutdown(wait=False, cancel_futures=True)

def snapshot_profile(profile, timeout=10.0):
    """Consistent copy of live research state. Never blocks past `timeout`: at the deadline a busy
    worker must not hold the envelope hostage, so an unlocked copy is attempted as a fallback."""
    acquired = profile.lock.acquire(timeout=timeout)
    try:
        if acquired:
            return copy.deepcopy(profile.data)
        for _ in range(5):
            try:
                return copy.deepcopy(profile.data)
            except RuntimeError:  # Mutated during the copy; try again.
                time.sleep(0.05)
        return None
    finally:
        if acquired:
            profile.lock.release()


def finalize(job, fetcher, previous_root, *, final):
    """Build the terminal envelope. Each step is isolated: a failure in one step is recorded and
    never discards the claims and evidence already gathered or carried from the previous run."""
    data = snapshot_profile(job.profile)
    if data is None:
        data = Profile(job.org, job.profile.data["run"]["run_id"]).data
        data["errors"].append({"family": "assembly", "type": "ProfileBusy", "message": "Research state could not be copied before the deadline"})
        data["run"]["terminal_status"] = "failed"
    incomplete = job.pending > 0 or job.finished is None

    def step(name, function):
        try:
            function()
        except Exception as exc:  # noqa: BLE001 - recorded on the envelope
            data["errors"].append({"family": "assembly", "type": type(exc).__name__, "step": name, "message": str(exc)[:300]})
            data["run"]["terminal_status"] = "failed"

    def operations():
        data["run"]["completed_at"] = now()
        if incomplete:
            data["run"]["deadline_reached"] = True
        org = data["organisation_number"]
        with fetcher.budget.lock:
            receipts = [r for r in fetcher.budget.receipts if r["organisation_number"] == org]
        data["operations"] = {"requests": len(receipts),
                              "runtime_ms": round(((job.finished or time.monotonic()) - job.started) * 1000),
                              "third_party_cost_usd": round(sum(r["declared_cost_usd"] for r in receipts), 6)}
        for family, state in data["availability"].items():
            if state.get("reason") == "Not yet researched":
                if incomplete:
                    state.update(state="failed", reason=DEADLINE_REASON)
                else:
                    state["reason"] = "No supported claim found by configured connectors"

    def refreshed():
        if data["run"]["terminal_status"] != "failed" or job.previous:
            refresh(data, job.previous, cutoff=fetcher.cutoff)

    def summary():
        data["summary"] = summarize(data)

    def validated():
        data["validation_errors"] = validate(data)
        if data["validation_errors"]:
            data["run"]["terminal_status"] = "failed"

    def preserved():
        if final and job.previous and previous_root is not None:
            preserve_previous_snapshots(data, job.previous, previous_root, fetcher)

    def provenance_sync():
        # Every evidence record points at the file that holds its bytes in THIS run's folder, and every
        # claim (including stale claims carried from the previous run) carries its primary record's provenance.
        paths = {s.get("id"): s.get("storage_path") for s in data.get("source_snapshots", [])}
        evidence = {}
        for record in data.get("evidence", []):
            if record.get("snapshot_id") in paths:
                record["snapshot_path"] = paths[record["snapshot_id"]]
            evidence[record["id"]] = record
        for claim in data.get("claims", []):
            primary = evidence.get(claim.get("primary_evidence_id")) or next(
                (evidence[i] for i in claim.get("evidence_ids", []) if i in evidence), None)
            if primary:
                claim.update(provenance(primary))

    step("operations", operations)
    step("refresh", refreshed)
    step("summary", summary)
    if "summary" not in data:
        data["summary"] = {"method": "unavailable", "sentences": [], "sections": [], "unknowns": []}
    step("provenance", provenance_sync)
    step("validation", validated)
    step("evidence_preservation", preserved)
    step("provenance_after_preservation", provenance_sync)
    try:
        data["state"] = envelope_state(data)
    except Exception:  # noqa: BLE001
        data["state"] = "failed"
    return data

def envelope_state(data):
    """One of the six required states for the whole result row."""
    if data["run"]["terminal_status"] == "failed":
        return "failed"
    identity = data["availability"].get("identity", {}).get("state")
    if identity in {"ambiguous", "blocked", "failed"}:
        return identity
    if any(c.get("availability") == "available" and not c.get("stale") for c in data["claims"]):
        return "available"
    return "not_available"

def archive_prior_output(output):
    """A rerun into the same folder becomes a refresh: keep the old envelopes as history."""
    prior = output / "envelopes.jsonl"
    report = output / "run-report.json"
    if not (prior.is_file() and report.is_file()):
        return None
    try:
        run_id = json.loads(report.read_text(encoding="utf-8")).get("run_id") or "previous"
    except (OSError, ValueError):
        return None
    if not json.loads(report.read_text(encoding="utf-8")).get("complete", True):
        return None
    folder = output / "history" / re.sub(r"[^A-Za-z0-9_.-]", "_", str(run_id))[:80]
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("envelopes.jsonl", "run-report.json", "manifest.jsonl", "requests.jsonl"):
        if (output / name).exists():
            shutil.copy2(output / name, folder / name)
    return folder / "envelopes.jsonl"

def peak_memory_mb():
    """Best-effort peak resident memory of this process (Linux/macOS/Windows), or None."""
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1024 * 1024 if platform.system() == "Darwin" else 1024), 1)
    except Exception:  # noqa: BLE001 - Windows has no resource module
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        # Declare pointer-sized types: the default int conversion truncates the pseudo-handle on 64-bit Windows.
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = wintypes.HANDLE
        info = ctypes.windll.psapi.GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        info.restype = wintypes.BOOL
        if info(current(), ctypes.byref(counters), counters.cb):
            return round(counters.PeakWorkingSetSize / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        pass
    return None


def write_envelopes(output, results, result_file=None):
    targets = [output / "envelopes.jsonl"] + ([result_file] if result_file else [])
    for target in targets:
        temp = target.with_name(target.name + "." + str(os.getpid()) + ".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            if target.suffix.lower() == ".json":
                json.dump(results, stream, ensure_ascii=False, allow_nan=False)
            else:
                for row in results:
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        temp.replace(target)

def run_batch(inputs, output, *, run_id=None, previous=None, replay=None, cutoff=None, registry_snapshot=None, workers=None,
              max_requests=None, seconds=None, per_company=None, max_pages=7, fresh=False, provisional_every=60, first_provisional=15):
    clock_start = time.monotonic()
    output, result_file = Path(output), None
    if output.suffix.lower() in {".jsonl", ".json"}:
        # The evaluator named a result file: write it there, artifacts beside it.
        result_file = output
        output = output.with_name(output.stem + "-artifacts")
        result_file.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    orgs = read_inputs(inputs)
    seconds, max_requests, per_company = resolve_limits(len(orgs), seconds=seconds, max_requests=max_requests, per_company=per_company)
    # Requests stop early enough to assemble and write every envelope before the hard limit.
    margin = min(max(45.0, seconds * 0.05), seconds * 0.5)
    previous_root = None
    if previous:
        previous_root = Path(previous).parent
    elif not fresh and not replay:
        auto = archive_prior_output(output)
        if auto:
            previous, previous_root = auto, output
    previous_rows = read_previous(previous) if previous else {}
    run_id = run_id or ("fjordlens-" + now().replace(":", "").replace("-", ""))
    budget = Budget(max_requests, max(1.0, seconds - margin), per_company)
    fetcher = Fetcher(output, budget, replay=Path(replay) if replay else None, cutoff=cutoff,
                      registry_snapshot=Path(registry_snapshot) if registry_snapshot else None)
    started_at = now()
    jobs, unique = [], {}
    for i, org in enumerate(orgs):
        if org in unique and re.fullmatch(r"[0-9]{9}", org):
            unique[org].copies.append(i)
            continue
        job = Job(i, org, run_id, previous_rows.get(org))
        unique.setdefault(org, job)
        jobs.append(job)
    pipeline = Pipeline(jobs, fetcher, max_pages=max_pages, workers=workers or 32)
    pipeline.start()
    hard_stop = clock_start + seconds - margin * 0.5
    next_provisional = time.monotonic() + min(first_provisional, provisional_every)  # Early safety net.
    while pipeline.busy() and time.monotonic() < hard_stop:
        pipeline.wait(min(hard_stop, next_provisional), 2.0)
        if time.monotonic() >= next_provisional and pipeline.busy():
            write_envelopes(output, expand(jobs, orgs, [finalize(job, fetcher, previous_root, final=False) for job in jobs]), result_file)
            done = len(jobs) - pipeline.busy()
            print(f"{done}/{len(jobs)} companies | {budget.total} requests | {time.monotonic()-clock_start:.0f}s", flush=True)
            next_provisional = time.monotonic() + provisional_every
    timed_out = pipeline.busy()
    budget.close()
    pipeline.close()
    results = expand(jobs, orgs, [finalize(job, fetcher, previous_root, final=True) for job in jobs])
    for i, row in enumerate(results):
        try:
            checkpoint_org = org_number(orgs[i])
        except ValueError:
            checkpoint_org = "invalid"
        write_json(output / "profiles" / f"{i:04d}-{checkpoint_org}.json", row)
    write_envelopes(output, results, result_file)
    (output / "manifest.jsonl").write_text("".join(json.dumps({"organisation_number": org}) + "\n" for org in orgs), encoding="utf-8")
    with budget.lock:
        receipts = list(budget.receipts)
    (output / "requests.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in receipts), encoding="utf-8")
    durations = sorted(r["operations"]["runtime_ms"] for r in results)
    coverage = {family: sum(r["availability"][family]["state"] == "available" for r in results) for family in results[0]["availability"]} if results else {}
    elapsed = time.monotonic() - clock_start
    report = {"agent": "FjordLens", "version": __version__, "run_id": run_id, "started_at": started_at, "completed_at": now(),
              "complete": True, "input_count": len(orgs), "terminal_envelopes": len(results), "unique_organisations": len(set(orgs)),
              "companies_cut_by_deadline": timed_out,
              "requests": budget.total, "request_limit": max_requests, "per_company_request_limit": per_company,
              "runtime_seconds": round(elapsed, 3), "runtime_limit_seconds": seconds,
              "third_party_cost_usd": round(budget.cost, 6), "cost_per_company_usd": round(budget.cost / max(1, len(orgs)), 6),
              "p50_company_runtime_ms": statistics.median(durations) if durations else 0,
              "p95_company_runtime_ms": durations[min(len(durations)-1, int(len(durations)*.95))] if durations else 0,
              "family_company_coverage": coverage, "published_claims": sum(len(r["claims"]) for r in results),
              "evidence_validation_errors": sum(len(r.get("validation_errors") or []) for r in results),
              "failed_envelopes": sum(r["run"]["terminal_status"] == "failed" for r in results),
              "changes": sum(len(r["changes"]) for r in results),
              "material_changes": sum(sum(1 for c in r["changes"] if c.get("material")) for r in results),
              "peak_memory_mb": peak_memory_mb(), "replay": bool(replay), "python": platform.python_version(),
              "previous_run": str(previous) if previous else None,
              "result_file": str(result_file or output / "envelopes.jsonl"),
              "envelope_states": {state: sum(r.get("state") == state for r in results) for state in ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")},
              "official_score": None, "accuracy_and_recall": "Not measured: requires independent checked labels",
              "models_at_runtime": [], "budget_passed": budget.total <= max_requests and budget.cost <= 10 and elapsed <= seconds,
              "registry_snapshot_supplied": bool(registry_snapshot),
              "cache_policy": "External evidence only when explicitly supplied via --replay or --registry-snapshot; otherwise run-local URL and robots cache only"}
    try:
        from .report import write_report
        report["report"] = write_report(results, output / "report")
    except Exception as exc:  # noqa: BLE001 - the viewer is a convenience; envelopes are already written
        report["report"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    write_json(output / "run-report.json", report)
    return report

def expand(jobs, orgs, finalized):
    """Map finalized jobs back to every input row (duplicates share one research pass)."""
    rows = [None] * len(orgs)
    for job, data in zip(jobs, finalized):
        rows[job.index] = data
        for i in job.copies:
            rows[i] = data
    return rows
