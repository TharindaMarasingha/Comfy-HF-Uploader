// Comfy-HF-Uploader by Tharinda Marasingha (https://github.com/TharindaMarasingha)
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Shows upload progress from the Python side as toasts in the ComfyUI UI.
app.registerExtension({
    name: "TharindaMarasingha.HFQueueFinishUploader",
    setup() {
        api.addEventListener("hf_uploader.status", ({ detail }) => {
            const msg = detail?.message ?? "";
            const level = detail?.level ?? "info";
            console.log(`[HF Uploader] ${msg}`);
            const toast = app.extensionManager?.toast;
            if (toast?.add) {
                const severity = { success: "success", warn: "warn", error: "error" }[level] ?? "info";
                toast.add({
                    severity,
                    summary: "HF Uploader",
                    detail: msg,
                    life: level === "error" ? 10000 : 4000,
                });
            }
        });
    },
});
