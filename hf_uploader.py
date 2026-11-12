"""
HF Queue-Finish Uploader for ComfyUI.
Author: Tharinda Marasingha (https://github.com/TharindaMarasingha)

How it works
------------
1. Put the "HF Upload On Queue Finish" node anywhere in your workflow
   (it does not need to be connected to anything).
2. Every time a queued prompt runs, the node *arms* an upload job with the
   folders / repo settings you entered. Nothing is zipped or uploaded yet.
3. A background watcher checks the ComfyUI queue. Only when the queue is
   completely empty (nothing running, nothing pending) for `idle_seconds`
   does it zip every selected folder and upload the zips to Hugging Face.

So if you queue 50 prompts, the upload happens once, after the 50th finishes,
never in the middle of the queue.
"""

import hashlib
import os
import tempfile
import threading
import time
import traceback
import zipfile
from datetime import datetime

import folder_paths

try:
    from server import PromptServer
except Exception:  # pragma: no cover - only outside ComfyUI
    PromptServer = None

LOG_PREFIX = "[HF Uploader]"

# Pending jobs, keyed by a hash of their settings. Re-arming the same job
# during a queue just refreshes it, so each folder is uploaded once per queue.
_jobs = {}
_jobs_lock = threading.Lock()
_upload_lock = threading.Lock()
_watcher_started = False

# Actions to run after all uploads of a queue are finished (e.g. a RunPod
# shutdown node from another package). Each value is a callable:
#     fn(all_ok: bool, results: list[dict])
_post_actions = {}

# Version of the HF_UPLOAD_JOB handle passed to other node packages.
HANDLE_VERSION = 1


def arm_post_action(key, fn):
    """Register a callback that runs once after the next queue's uploads."""
    with _jobs_lock:
        _post_actions[key] = fn


def _make_handle(key, job):
    """Plain-dict handle given to downstream nodes through the HF_UPLOAD_JOB output.

    Other packages only use this dict, so they never need to import this module.
      handle["version"]                    -> int (HANDLE_VERSION)
      handle["repo_id"], ["repo_type"], ["folders"]
      handle["on_finished"](name, fn)      -> fn(all_ok, results) runs once, on the
                                              watcher thread, after this queue's
                                              uploads are done and verified.
    results: list of {"repo_id", "ok", "uploaded": [...], "failed": [...], "skipped": [...]}
    """
    return {
        "version": HANDLE_VERSION,
        "key": key,
        "repo_id": job["repo_id"],
        "repo_type": job["repo_type"],
        "folders": list(job["folders"]),
        "on_finished": lambda name, fn: arm_post_action(f"{name}", fn),
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _log(msg, level="info"):
    print(f"{LOG_PREFIX} {msg}")
    _notify(msg, level)


def _notify(msg, level="info"):
    """Send a toast message to the browser (handled by web/js/hf_uploader.js)."""
    try:
        if PromptServer is not None and PromptServer.instance is not None:
            PromptServer.instance.send_sync(
                "hf_uploader.status", {"message": msg, "level": level}
            )
    except Exception:
        pass


def _tasks_remaining():
    """Number of prompts running + pending. Returns None if unknown."""
    try:
        q = PromptServer.instance.prompt_queue
        return q.get_tasks_remaining()
    except Exception:
        return None


def _resolve_folder(path):
    path = path.strip().strip('"').strip("'")
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(folder_paths.get_output_directory(), path)
    return os.path.normpath(path)


def _parse_folders(text):
    folders = []
    for line in text.replace(",", "\n").splitlines():
        if line.strip().startswith("#"):
            continue
        p = _resolve_folder(line)
        if p and p not in folders:
            folders.append(p)
    return folders


def _zip_folder(folder, zip_path, compress):
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    count = 0
    with zipfile.ZipFile(zip_path, "w", compression=method, allowZip64=True) as zf:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                full = os.path.join(root, name)
                if os.path.abspath(full) == os.path.abspath(zip_path):
                    continue
                arc = os.path.relpath(full, os.path.dirname(folder))
                zf.write(full, arc)
                count += 1
    return count


def _verify_remote(api, repo_id, repo_type, remote, local_size):
    """Check the file exists on the Hub with the same size as the local zip."""
    last = None
    for _ in range(5):
        try:
            for info in api.get_paths_info(repo_id, [remote], repo_type=repo_type):
                if getattr(info, "path", None) == remote:
                    last = getattr(info, "size", None)
                    if last == local_size:
                        return True, last
        except Exception as e:
            last = e
        time.sleep(3)
    return False, last


# --------------------------------------------------------------------------- #
# Upload logic (runs on the watcher thread)
# --------------------------------------------------------------------------- #
def _run_job(job):
    """Zip + upload + verify every folder of one job.

    Returns {"repo_id", "ok", "uploaded": [...], "failed": [...], "skipped": [...]}.
    ok is True only if every existing, non-empty folder was uploaded AND verified.
    """
    result = {"repo_id": job["repo_id"], "ok": False, "uploaded": [], "failed": [], "skipped": []}
    try:
        from huggingface_hub import HfApi
    except ImportError:
        _log("huggingface_hub is not installed. Run: pip install huggingface_hub", "error")
        result["failed"].append("huggingface_hub missing")
        return result

    token = job["hf_token"] or os.environ.get("HF_TOKEN") or None
    repo_id = job["repo_id"]
    repo_type = job["repo_type"]
    api = HfApi(token=token)

    if job["create_repo_if_missing"]:
        try:
            api.create_repo(
                repo_id=repo_id,
                repo_type=repo_type,
                private=job["private_repo"],
                exist_ok=True,
            )
        except Exception as e:
            _log(f"Could not create/check repo '{repo_id}': {e}", "error")
            result["failed"].append(f"repo {repo_id}")
            return result

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for folder in job["folders"]:
        name = os.path.basename(folder.rstrip("\\/")) or "folder"
        if not os.path.isdir(folder):
            _log(f"Folder not found: {folder}", "warn")
            result["failed"].append(folder)
            continue

        zip_name = f"{name}_{stamp}.zip" if job["add_timestamp"] else f"{name}.zip"
        tmp_dir = tempfile.mkdtemp(prefix="hf_uploader_")
        zip_path = os.path.join(tmp_dir, zip_name)

        try:
            _log(f"Zipping '{name}' ...")
            n = _zip_folder(folder, zip_path, job["compress"])
            if n == 0:
                _log(f"Folder is empty, skipped: {folder}", "warn")
                result["skipped"].append(folder)
                continue

            local_size = os.path.getsize(zip_path)
            remote = "/".join(p for p in (job["path_in_repo"].strip("/"), zip_name) if p)
            _log(f"Uploading {zip_name} ({n} files, {local_size / 1048576:.1f} MB) -> {repo_id}/{remote}")

            api.upload_file(
                path_or_fileobj=zip_path,
                path_in_repo=remote,
                repo_id=repo_id,
                repo_type=repo_type,
                commit_message=f"{job['commit_message']} ({name})",
            )

            ok, remote_size = _verify_remote(api, repo_id, repo_type, remote, local_size)
            if ok:
                result["uploaded"].append(remote)
                _log(f"Uploaded + verified {zip_name} ({remote_size} bytes)", "success")
            else:
                result["failed"].append(folder)
                _log(f"Verify FAILED for {zip_name}: local {local_size} bytes, remote {remote_size}", "error")
        except Exception as e:
            result["failed"].append(folder)
            _log(f"Failed for '{name}': {e}", "error")
            traceback.print_exc()
        finally:
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                os.rmdir(tmp_dir)
            except Exception:
                pass

    result["ok"] = not result["failed"]
    level = "success" if result["ok"] else "warn"
    _log(
        f"Finished {repo_id}: {len(result['uploaded'])} uploaded, "
        f"{len(result['failed'])} failed, {len(result['skipped'])} empty.",
        level,
    )
    return result


def _watcher_loop():
    idle_since = None
    while True:
        time.sleep(1.0)
        try:
            with _jobs_lock:
                has_jobs = bool(_jobs)
            if not has_jobs:
                idle_since = None
                continue

            remaining = _tasks_remaining()
            if remaining is None or remaining > 0:
                idle_since = None
                continue

            # Queue is empty. Wait for it to stay empty for idle_seconds.
            if idle_since is None:
                idle_since = time.time()

            with _jobs_lock:
                wait = max(j["idle_seconds"] for j in _jobs.values())
            if time.time() - idle_since < wait:
                continue

            with _jobs_lock:
                jobs = list(_jobs.values())
                _jobs.clear()
                actions = list(_post_actions.values())
                _post_actions.clear()
            idle_since = None

            with _upload_lock:
                _log(f"Queue finished. Starting {len(jobs)} upload job(s).")
                results = []
                for job in jobs:
                    try:
                        results.append(_run_job(job))
                    except Exception as e:
                        traceback.print_exc()
                        results.append({"repo_id": job["repo_id"], "ok": False,
                                        "uploaded": [], "failed": [str(e)], "skipped": []})

                all_ok = bool(results) and all(r["ok"] for r in results)
                for fn in actions:
                    try:
                        fn(all_ok, results)
                    except Exception:
                        traceback.print_exc()
        except Exception:
            traceback.print_exc()


def _start_watcher():
    global _watcher_started
    if _watcher_started:
        return
    _watcher_started = True
    threading.Thread(target=_watcher_loop, name="HFUploaderWatcher", daemon=True).start()


# --------------------------------------------------------------------------- #
# The node
# --------------------------------------------------------------------------- #
class HFUploadOnQueueFinish:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": True}),
                "folders": ("STRING", {
                    "multiline": True,
                    "default": "my_folder\n# one folder per line\n# relative paths = inside ComfyUI/output\n# absolute paths also work, e.g. D:\\renders\\set1",
                }),
                "repo_id": ("STRING", {"default": "username/my-repo"}),
                "repo_type": (["dataset", "model", "space"], {"default": "dataset"}),
                "hf_token": ("STRING", {
                    "default": "",
                    "tooltip": "Hugging Face write token (hf_...). Leave empty to use the HF_TOKEN "
                               "environment variable or `huggingface-cli login`. NOTE: a token typed "
                               "here is saved inside the workflow and PNG metadata.",
                }),
                "path_in_repo": ("STRING", {"default": "", "tooltip": "Sub-folder inside the repo. Empty = root."}),
                "add_timestamp": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "On: name_20261112_101500.zip (keeps every upload). Off: name.zip (overwrites).",
                }),
                "compress": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Off = store only (fast, PNG/MP4 are already compressed). On = deflate.",
                }),
                "create_repo_if_missing": ("BOOLEAN", {"default": True}),
                "private_repo": ("BOOLEAN", {"default": True, "tooltip": "Only used when creating a new repo."}),
                "idle_seconds": ("INT", {
                    "default": 5, "min": 1, "max": 600,
                    "tooltip": "Queue must stay empty this many seconds before uploading.",
                }),
                "commit_message": ("STRING", {"default": "Auto upload from ComfyUI"}),
            },
        }

    RETURN_TYPES = ("STRING", "HF_UPLOAD_JOB")
    RETURN_NAMES = ("status", "hf_job")
    FUNCTION = "arm"
    OUTPUT_NODE = True
    CATEGORY = "Tharinda Marasingha/HuggingFace"
    DESCRIPTION = ("Zips the selected folders and uploads them to a Hugging Face repo after the "
                   "whole ComfyUI queue has finished. By Tharinda Marasingha.")

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Always run so the job is re-armed on every queued prompt.
        return float("nan")

    def arm(self, enabled, folders, repo_id, repo_type, hf_token, path_in_repo,
            add_timestamp, compress, create_repo_if_missing, private_repo,
            idle_seconds, commit_message):
        if not enabled:
            return ("disabled", None)

        folder_list = _parse_folders(folders)
        repo_id = repo_id.strip()
        if not folder_list:
            return ("no folders given", None)
        if not repo_id or "/" not in repo_id:
            raise ValueError(f"{LOG_PREFIX} repo_id must look like 'username/repo-name'")

        job = {
            "folders": folder_list,
            "repo_id": repo_id,
            "repo_type": repo_type,
            "hf_token": hf_token.strip(),
            "path_in_repo": path_in_repo.strip(),
            "add_timestamp": add_timestamp,
            "compress": compress,
            "create_repo_if_missing": create_repo_if_missing,
            "private_repo": private_repo,
            "idle_seconds": idle_seconds,
            "commit_message": commit_message.strip() or "Auto upload from ComfyUI",
        }
        key = hashlib.sha1(
            f"{repo_id}|{repo_type}|{path_in_repo}|{'|'.join(folder_list)}".encode()
        ).hexdigest()

        with _jobs_lock:
            new = key not in _jobs
            _jobs[key] = job

        _start_watcher()
        status = f"armed: {len(folder_list)} folder(s) -> {repo_id} (uploads when queue finishes)"
        if new:
            print(f"{LOG_PREFIX} {status}")
        return (status, _make_handle(key, job))


_start_watcher()

NODE_CLASS_MAPPINGS = {"HFUploadOnQueueFinish": HFUploadOnQueueFinish}
NODE_DISPLAY_NAME_MAPPINGS = {"HFUploadOnQueueFinish": "HF Upload On Queue Finish (by Tharinda Marasingha)"}
