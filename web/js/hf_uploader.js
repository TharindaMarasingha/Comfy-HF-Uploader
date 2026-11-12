// Comfy-HF-Uploader by Tharinda Marasingha (https://github.com/TharindaMarasingha)
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const CREDIT = "© Tharinda Marasingha";
const CREDIT_HEIGHT = 14;

// Shows upload progress from the Python side as toasts in the ComfyUI UI,
// and draws a small, subtle credit in the bottom-right corner of the node.
app.registerExtension({
    name: "TharindaMarasingha.HFQueueFinishUploader",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "HFUploadOnQueueFinish") return;

        const computeSize = nodeType.prototype.computeSize;
        nodeType.prototype.computeSize = function (...args) {
            const size = computeSize ? computeSize.apply(this, args) : [200, 100];
            size[1] += CREDIT_HEIGHT;
            return size;
        };

        const onDrawForeground = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function (ctx) {
            onDrawForeground?.apply(this, arguments);
            if (this.flags?.collapsed) return;
            ctx.save();
            ctx.font = "italic 10px sans-serif";
            ctx.fillStyle = "rgba(170, 170, 170, 0.45)";
            ctx.textAlign = "right";
            ctx.textBaseline = "bottom";
            ctx.fillText(CREDIT, this.size[0] - 8, this.size[1] - 3);
            ctx.restore();
        };
    },
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
