import { config } from "../../package.json";
import { getPref } from "../utils/prefs";
import {
    OutputMode,
    PluginTask,
    ServerConfig,
    ServerTaskSnapshot,
    ServerTaskStatus,
} from "./pdf2zhTypes";
import {
    PDF2zhHelperFactory,
    TaskOutputResponse,
} from "./pdf2zhHelper";

type TaskDialogArgs = {
    _initPromise: any;
    getTasks: () => PluginTask[];
    refreshTasks: () => Promise<void>;
    cancelTask: (taskId: string) => Promise<void>;
    deleteTask: (taskId: string) => Promise<void>;
    clearFailedTasks: () => Promise<void>;
};

type TaskListResponse = {
    status?: string;
    tasks?: ServerTaskSnapshot[];
    message?: string;
};

type TaskCreateResponse = {
    status?: string;
    task?: ServerTaskSnapshot;
    message?: string;
};

const ACTIVE_STATUSES: ServerTaskStatus[] = ["queued", "running", "cancelling"];

export class PDF2zhTaskManager {
    private static tasks = new Map<string, PluginTask>();
    private static pollTimer: ReturnType<typeof setInterval> | undefined;
    private static pollPromise: Promise<void> | null = null;
    private static dialogWindow: Window | undefined;

    static async processWorker() {
        const pane = ztoolkit.getGlobal("ZoteroPane");
        const selectedItems = pane.getSelectedItems();
        if (selectedItems.length === 0) {
            ztoolkit.getGlobal("alert")("请先选择一个条目或附件。");
            return;
        }

        const progressWindow = new ztoolkit.ProgressWindow(
            "zotero-pdf2zh-next 任务",
        ).createLine({
            text: "正在提交翻译任务...",
            type: "default",
            progress: 0,
        });
        progressWindow.show();

        this.openWindow();

        let submitted = 0;
        const errors: string[] = [];
        const total = selectedItems.length;
        const serverConfig = PDF2zhHelperFactory.getServerConfig();

        if (serverConfig.outputModes.length === 0) {
            ztoolkit.getGlobal("alert")("请至少选择一种输出PDF模式。");
            return;
        }

        for (let index = 0; index < selectedItems.length; index++) {
            const item = selectedItems[index];
            try {
                await this.submitTask(item, serverConfig);
                submitted += 1;
            } catch (error) {
                const message =
                    error instanceof Error ? error.message : String(error);
                errors.push(message);
            }

            progressWindow.changeLine({
                text: `已提交 ${index + 1}/${total} 个任务...`,
                type: errors.length > 0 ? "warning" : "default",
                progress: Math.round(((index + 1) / total) * 100),
            });
        }

        await this.refreshTasks();

        progressWindow.changeLine({
            text: `任务已提交：成功 ${submitted}，失败 ${errors.length}`,
            type: errors.length > 0 ? "warning" : "success",
            progress: 100,
        });

        if (errors.length > 0) {
            ztoolkit.getGlobal("alert")(
                `部分任务提交失败：\n${errors.slice(0, 5).join("\n")}`,
            );
        }
    }

    static openWindow() {
        if (this.dialogWindow && !this.dialogWindow.closed) {
            this.dialogWindow.focus();
            return;
        }

        const windowArgs: TaskDialogArgs = {
            _initPromise: Zotero.Promise.defer(),
            getTasks: () => this.getTasks(),
            refreshTasks: () => this.refreshTasks(),
            cancelTask: (taskId: string) => this.cancelTask(taskId),
            deleteTask: (taskId: string) => this.deleteTask(taskId),
            clearFailedTasks: () => this.clearFailedTasks(),
        };

        const dialogWindow = Zotero.getMainWindow().openDialog(
            `chrome://${config.addonRef}/content/taskManager.xhtml`,
            `${config.addonRef}-taskManager`,
            "chrome,centerscreen,resizable,status,dialog=no,width=980,height=640",
            windowArgs,
        );
        if (!dialogWindow) {
            return;
        }

        this.dialogWindow = dialogWindow;
        dialogWindow.addEventListener("unload", () => {
            if (this.dialogWindow === dialogWindow) {
                this.dialogWindow = undefined;
            }
        });
    }

    static closeWindow() {
        if (this.dialogWindow && !this.dialogWindow.closed) {
            this.dialogWindow.close();
        }
        this.dialogWindow = undefined;
    }

    static getTasks(): PluginTask[] {
        return Array.from(this.tasks.values()).sort((left, right) =>
            right.createdAt.localeCompare(left.createdAt),
        );
    }

    static async refreshTasks(): Promise<void> {
        if (this.pollPromise) {
            return this.pollPromise;
        }

        this.pollPromise = this.refreshTasksInternal();
        try {
            await this.pollPromise;
        } finally {
            this.pollPromise = null;
        }
    }

    static async cancelTask(taskId: string): Promise<void> {
        const task = this.tasks.get(taskId);
        if (!task) {
            throw new Error("任务不存在");
        }

        const response = await fetch(`${task.serverUrl}/tasks/${taskId}/cancel`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        });
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }

        const result = (await response.json()) as { task?: ServerTaskSnapshot };
        if (result.task) {
            this.upsertTask(result.task, task.serverUrl, {
                itemID: task.itemID,
                source: task.source,
                importState: task.importState,
                importError: task.importError,
            });
        }
    }

    static async deleteTask(taskId: string): Promise<void> {
        const task = this.tasks.get(taskId);
        if (!task) {
            throw new Error("任务不存在");
        }

        const response = await fetch(`${task.serverUrl}/tasks/${taskId}`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
        });
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }

        this.tasks.delete(taskId);
        this.ensurePolling();
    }

    static async clearFailedTasks(): Promise<void> {
        const failedTasks = this.getTasks().filter((task) => task.status === "failed");
        if (failedTasks.length === 0) {
            return;
        }

        const serverUrls = new Set(failedTasks.map((task) => task.serverUrl));
        for (const serverUrl of serverUrls) {
            const response = await fetch(`${serverUrl}/tasks/clear-failed`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
            });
            if (!response.ok) {
                throw new Error(await this.readErrorMessage(response));
            }

            for (const task of failedTasks) {
                if (task.serverUrl === serverUrl) {
                    this.tasks.delete(task.taskId);
                }
            }
        }

        this.ensurePolling();
    }

    private static async submitTask(item: Zotero.Item, config: ServerConfig) {
        const fileData = await PDF2zhHelperFactory.prepareFileData(item);
        const requestBody = PDF2zhHelperFactory.buildTaskRequestBody(
            fileData,
            config,
        );

        const response = await PDF2zhHelperFactory.retryOperation(() =>
            fetch(`${config.serverUrl}/tasks`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(requestBody),
            }),
        );

        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }

        const result = (await response.json()) as TaskCreateResponse;
        if (!result.task) {
            throw new Error("服务器没有返回任务信息");
        }

        this.upsertTask(result.task, config.serverUrl, {
            itemID: item.id,
            source: "local",
            importState: "pending",
        });
        this.ensurePolling();
    }

    private static async refreshTasksInternal(): Promise<void> {
        const serverUrls = new Set<string>();
        const currentServerUrl = getPref("new_serverip")?.toString() || "";
        if (currentServerUrl) {
            serverUrls.add(currentServerUrl);
        }
        for (const task of this.tasks.values()) {
            if (task.serverUrl) {
                serverUrls.add(task.serverUrl);
            }
        }

        for (const serverUrl of serverUrls) {
            const response = await fetch(`${serverUrl}/tasks`);
            if (!response.ok) {
                continue;
            }
            const payload = (await response.json()) as TaskListResponse;
            const snapshots = payload.tasks || [];
            const serverTaskIds = new Set(snapshots.map((snapshot) => snapshot.taskId));
            snapshots.forEach((snapshot) => {
                const existing = this.tasks.get(snapshot.taskId);
                this.upsertTask(snapshot, serverUrl, {
                    itemID: existing?.itemID,
                    source: existing?.source || "remote",
                    importState: existing?.importState || "none",
                    importError: existing?.importError,
                });
            });
            for (const task of this.getTasks()) {
                if (task.serverUrl !== serverUrl) {
                    continue;
                }
                if (!serverTaskIds.has(task.taskId)) {
                    this.tasks.delete(task.taskId);
                }
            }
        }

        for (const task of this.getTasks()) {
            if (
                task.source === "local" &&
                task.status === "completed" &&
                task.importState === "pending"
            ) {
                await this.importTaskOutputs(task.taskId);
            }
        }

        this.ensurePolling();
    }

    private static async importTaskOutputs(taskId: string) {
        const task = this.tasks.get(taskId);
        if (!task || task.importState !== "pending") {
            return;
        }

        if (!task.itemID) {
            this.updateLocalTask(taskId, {
                importState: "failed",
                importError: "无法找到原始条目",
            });
            return;
        }

        const item = Zotero.Items.get(task.itemID);
        if (!item) {
            this.updateLocalTask(taskId, {
                importState: "failed",
                importError: "原始条目已不存在",
            });
            return;
        }

        this.updateLocalTask(taskId, {
            importState: "importing",
            importError: undefined,
        });

        try {
            for (const outputMode of task.outputModes) {
                const response = await fetch(
                    `${task.serverUrl}/tasks/${task.taskId}/result?mode=${outputMode}`,
                );
                if (!response.ok) {
                    throw new Error(await this.readErrorMessage(response));
                }

                const bytes = new Uint8Array(await response.arrayBuffer());
                const fileName =
                    task.resultFiles[outputMode] ||
                    `${task.fileName}.${outputMode}.pdf`;
                const output: TaskOutputResponse = {
                    fileName,
                    outputMode,
                    bytes,
                };
                await PDF2zhHelperFactory.handleOutputResponse(
                    output,
                    item,
                    {
                        ...PDF2zhHelperFactory.getServerConfig(),
                        service: task.service,
                        outputModes: task.outputModes,
                    },
                );
            }

            this.updateLocalTask(taskId, {
                importState: "imported",
                importError: undefined,
            });
        } catch (error) {
            this.updateLocalTask(taskId, {
                importState: "failed",
                importError:
                    error instanceof Error ? error.message : String(error),
            });
        }
    }

    private static upsertTask(
        snapshot: ServerTaskSnapshot,
        serverUrl: string,
        overrides: Partial<PluginTask> = {},
    ) {
        const existing = this.tasks.get(snapshot.taskId);
        const nextTask: PluginTask = {
            taskId: snapshot.taskId,
            fileName: snapshot.fileName,
            service: snapshot.service,
            outputModes: snapshot.outputModes,
            status: snapshot.status,
            stage: snapshot.stage,
            stageCurrent: snapshot.stageCurrent,
            stageTotal: snapshot.stageTotal,
            stageProgress: snapshot.stageProgress,
            overallProgress: snapshot.overallProgress,
            error: snapshot.error,
            resultFiles: snapshot.resultFiles,
            createdAt: snapshot.createdAt,
            updatedAt: snapshot.updatedAt,
            canCancel: snapshot.canCancel,
            cancelRequested: snapshot.cancelRequested,
            serverUrl,
            source: existing?.source || "remote",
            importState: existing?.importState || "none",
            itemID: existing?.itemID,
            importError: existing?.importError,
            ...overrides,
        };
        this.tasks.set(snapshot.taskId, nextTask);
    }

    private static updateLocalTask(
        taskId: string,
        patch: Partial<PluginTask>,
    ): void {
        const current = this.tasks.get(taskId);
        if (!current) {
            return;
        }
        this.tasks.set(taskId, {
            ...current,
            ...patch,
        });
    }

    private static ensurePolling() {
        const hasActiveTasks = Array.from(this.tasks.values()).some(
            (task) =>
                ACTIVE_STATUSES.includes(task.status) ||
                task.importState === "pending" ||
                task.importState === "importing",
        );

        if (!hasActiveTasks) {
            if (this.pollTimer != undefined) {
                clearInterval(this.pollTimer);
                this.pollTimer = undefined;
            }
            return;
        }

        if (this.pollTimer == undefined) {
            this.pollTimer = setInterval(() => {
                void this.refreshTasks();
            }, 1000);
        }
    }

    private static async readErrorMessage(response: Response): Promise<string> {
        try {
            const payload = (await response.json()) as {
                message?: string;
                status?: string;
            };
            return payload.message || `服务器返回错误: ${response.status}`;
        } catch (_error) {
            return `服务器返回错误: ${response.status}`;
        }
    }
}
