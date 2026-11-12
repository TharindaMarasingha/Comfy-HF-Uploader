# Comfy-HF-Uploader

**Automatically zip your ComfyUI output folders and upload them to Hugging Face when your queue finishes.**

Created by **Tharinda Marasingha** ([@TharindaMarasingha](https://github.com/TharindaMarasingha))

---

Queue a big batch of generations, walk away, and come back to find every result zipped and safely stored in your Hugging Face repo. The upload only starts after the **whole queue** is finished. It never runs halfway through, so it doesn't slow down your renders or upload half-finished folders.

This is especially handy on cloud GPUs (RunPod, Vast.ai, Colab, etc.), where you want your outputs saved somewhere permanent before you shut the machine down.

## Features

- ⏳ **Waits for the whole queue.** Queue 1 prompt or 500; the upload runs once, after the last one.
- 📁 **Multiple folders.** Each folder becomes its own `.zip`.
- 🤗 **Any repo type.** Works with dataset, model and space repos. It can create the repo for you (private by default).
- ✅ **Verified uploads.** After each upload, the node checks that the file exists on the Hub with the same size as the local zip.
- 🕒 **Keep history or overwrite.** Use timestamped zip names to keep every upload, or fixed names to replace the previous one.
- 🔔 **Progress in the UI.** Status messages show as toasts in ComfyUI and in the console.
- 🔌 **Extensible.** The `hf_job` output lets other nodes run after the uploads finish, for example to shut down a cloud pod.

## Installation

### Option 0: Comfy Registry (recommended)
Search for **Comfy-HF-Uploader** in **ComfyUI Manager → Custom Nodes Manager** and click **Install**. You can also use [comfy-cli](https://github.com/Comfy-Org/comfy-cli):
```bash
comfy node install comfy-hf-uploader
```
Registry page: <https://registry.comfy.org/publishers/tharinda>

### Option 1: ComfyUI Manager (Git URL)
Open **ComfyUI Manager → Install via Git URL** and paste:
```
https://github.com/TharindaMarasingha/Comfy-HF-Uploader.git
```
Then restart ComfyUI.

### Option 2: Manual (git)
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/TharindaMarasingha/Comfy-HF-Uploader.git
pip install -r Comfy-HF-Uploader/requirements.txt
```

**Windows portable build:** install the requirements with ComfyUI's own Python:
```bat
cd ComfyUI_windows_portable
python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\Comfy-HF-Uploader\requirements.txt
```

Restart ComfyUI. The node is listed under **Tharinda Marasingha → HuggingFace → HF Upload On Queue Finish**.

The only dependency is `huggingface_hub`. Many ComfyUI installs already have it.

## Quick start

1. **Create a Hugging Face token** at <https://huggingface.co/settings/tokens>. Choose **Write** access.
2. **Add the node** anywhere in your workflow. It doesn't need to be connected to anything.
3. **Fill in the inputs:**
   - `folders`: the folders to upload, one per line (see [Choosing folders](#choosing-folders))
   - `repo_id`: e.g. `your-username/my-comfy-outputs`
   - `hf_token`: leave empty if you set up the token another way (see [Setting up your token](#setting-up-your-token-safely))
4. **Queue your prompts** as usual.
5. When the last prompt finishes, you'll see messages like:
   ```
   [HF Uploader] Queue finished. Starting 1 upload job(s).
   [HF Uploader] Zipping 'portraits' ...
   [HF Uploader] Uploading portraits_20261112_101500.zip (240 files, 512.3 MB) -> you/my-comfy-outputs/portraits_20261112_101500.zip
   [HF Uploader] Uploaded + verified portraits_20261112_101500.zip
   [HF Uploader] Finished you/my-comfy-outputs: 1 uploaded, 0 failed, 0 empty.
   ```

## How it works

```
 Prompt 1 runs ─┐
 Prompt 2 runs ─┼─ node "arms" the upload job (nothing uploaded yet)
 Prompt N runs ─┘
        │
 Queue is empty for `idle_seconds`
        │
 Zip folder 1 → upload → verify
 Zip folder 2 → upload → verify
 ...
        │
 Run any connected "after upload" nodes
```

Every time a prompt containing the node runs, the node only **registers** the job. A background watcher keeps an eye on the ComfyUI queue. The uploads start only once nothing is running or waiting **and** the queue has stayed empty for `idle_seconds`.

If the same node runs 100 times in one queue, the folders are still uploaded **once**.

## Node reference

### Inputs

| Input | Default | Description |
|---|---|---|
| `enabled` | `true` | Turn the upload on or off without removing the node. |
| `folders` | – | One folder per line (commas also work). Lines starting with `#` are ignored. |
| `repo_id` | – | Target repo, e.g. `username/repo-name`. |
| `repo_type` | `dataset` | `dataset`, `model` or `space`. |
| `hf_token` | empty | Your Hugging Face write token. Leave empty to use `HF_TOKEN` or a saved login. |
| `path_in_repo` | empty | Sub-folder inside the repo, e.g. `renders/day1`. Empty = repo root. |
| `add_timestamp` | `true` | `true` → `name_YYYYMMDD_HHMMSS.zip` (keeps every upload). `false` → `name.zip` (overwrites the previous upload). |
| `compress` | `false` | `false` = store only. This is fast, and PNG/JPG/MP4 files are already compressed. `true` = deflate, which is smaller for text and latents but slower. |
| `create_repo_if_missing` | `true` | Create the repo if it doesn't exist yet. |
| `private_repo` | `true` | Visibility of a newly created repo. Existing repos are not changed. |
| `idle_seconds` | `5` | How long the queue must stay empty before uploading starts. |
| `commit_message` | `Auto upload from ComfyUI` | Commit message on the Hub. The folder name is appended. |

### Outputs

| Output | Type | Description |
|---|---|---|
| `status` | `STRING` | A short status text, e.g. `armed: 2 folder(s) -> user/repo`. |
| `hf_job` | `HF_UPLOAD_JOB` | Connect this to nodes that should run **after** the uploads are done. See [For developers](#for-developers-the-hf_upload_job-output). |

## Choosing folders

```text
portraits                    # → ComfyUI/output/portraits
landscapes/batch_02          # → ComfyUI/output/landscapes/batch_02
D:\renders\final             # absolute Windows path
/workspace/ComfyUI/output/v  # absolute Linux path (RunPod, etc.)
# old_tests                  # ignored (comment)
```

- **Relative paths** are resolved inside ComfyUI's **output** directory.
- **Absolute paths** are used as they are.
- Each folder is zipped **with** its sub-folders. Inside the zip, everything sits under the folder's own name.
- A missing folder is reported as **failed**. An empty folder is **skipped**.

## Setting up your token safely

> ⚠️ **Important:** anything typed into a node input is saved in your workflow `.json` **and** in the metadata of every PNG that ComfyUI saves. If you share those files, people can read your token.

You can give the node your token in three ways. The node checks them in this order:

1. **`hf_token` input.** The easiest, but the least safe (see the warning above).
2. **`HF_TOKEN` environment variable.** Recommended. Leave `hf_token` empty.
   - Windows portable: add `set HF_TOKEN=hf_xxx` at the top of `run_nvidia_gpu.bat`
   - Linux / RunPod: `export HF_TOKEN=hf_xxx` before starting ComfyUI, or add it as a pod environment variable or secret
3. **Saved login.** Also recommended. Run this once with ComfyUI's Python, then leave `hf_token` empty:
   ```bash
   python -m huggingface_hub.commands.huggingface_cli login
   ```

If a token is ever leaked, delete it at <https://huggingface.co/settings/tokens> and create a new one.

## Tips

- **Large outputs:** keep `compress` off. Zipping is then mostly disk speed. The Hub handles large files automatically through Git LFS / Xet.
- **Disk space:** the zip is created in your system temp folder and deleted right after upload. You need free space roughly equal to the size of the largest folder.
- **Several repos:** add one node per repo. They all wait for the same queue to finish.
- **Cancelling the queue:** a cancelled or cleared queue is also an empty queue, so the uploads still start.
- **Keeping the pod/PC awake:** the uploads run inside the ComfyUI process. Don't close ComfyUI until you see `Finished ...`.

## Troubleshooting

| Message / problem | Fix |
|---|---|
| `huggingface_hub is not installed` | Install the requirements with **the same Python that runs ComfyUI** (see [Installation](#installation)). |
| `401` / `Invalid credentials` | The token is wrong or missing. Check `hf_token`, `HF_TOKEN` or your saved login. |
| `403` / forbidden | The token has no **Write** access, or you don't have permission to write to that repo. |
| `repo_id must look like 'username/repo-name'` | Include your username or organisation: `you/repo`. |
| `Folder not found` | Check the path. Relative paths start inside `ComfyUI/output`. |
| `Verify FAILED` | The Hub copy doesn't match the local zip. Re-run the queue. The local files are never deleted. |
| Nothing happens after the queue | Check that `enabled` is on, at least one folder is listed, and the node is part of the workflow you queued. Look in the console for `armed:`. |

## For developers: the `HF_UPLOAD_JOB` output

Other node packages can react when the uploads finish, without importing this package. Accept an input of type `HF_UPLOAD_JOB` and register a callback:

```python
class MyAfterUploadNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"hf_job": ("HF_UPLOAD_JOB",)}}

    RETURN_TYPES = ()
    FUNCTION = "run"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")  # re-arm on every queued prompt

    def run(self, hf_job):
        if hf_job is None:  # uploader disabled / no folders
            return ()

        def on_finished(all_ok, results):
            # runs once, on a background thread, after this queue's uploads
            if all_ok:
                print("All uploads verified:", results)

        hf_job["on_finished"]("my-node-unique-name", on_finished)
        return ()
```

**Handle contract (version 1)**

```python
hf_job["version"]                # 1
hf_job["repo_id"]                # "user/repo"
hf_job["repo_type"]              # "dataset" | "model" | "space"
hf_job["folders"]                # list of absolute folder paths
hf_job["on_finished"](name, fn)  # fn(all_ok: bool, results: list[dict])

# each item in results:
{"repo_id": str, "ok": bool, "uploaded": [paths in repo], "failed": [...], "skipped": [...]}
```

`all_ok` is `True` only when every existing, non-empty folder was uploaded **and** verified.

## License

[MIT](LICENSE) © 2026 Tharinda Marasingha

## Credits

Developed and maintained by **Tharinda Marasingha** – <https://github.com/TharindaMarasingha>

Bug reports and feature requests are welcome in [Issues](https://github.com/TharindaMarasingha/Comfy-HF-Uploader/issues).
