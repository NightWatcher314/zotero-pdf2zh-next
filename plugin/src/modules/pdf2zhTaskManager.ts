import { config } from "../../package.json";
import { getPref } from "../utils/prefs";
import {
    PluginTask,
    ServerConfig,
    ServerTaskEvent,
    ServerTaskSnapshot,
    ServerTaskStatus,
} from "./pdf2zhTypes";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";
import { ServerTaskClient } from "./serverTaskClient";
import { TaskEventStream } from "./taskEventStream";
import { ZoteroTaskImporter } from "./zoteroTaskImporter";

type TaskDialogArgs = {
    _initPromise: any;
    getTasks: () => PluginTask[];
    hasActiveTasks: () => boolean;
    onTasksChanged: (listener: () => void) => () => void;
    refreshTasks: () => Promise<void>;
    cancelTask: (taskId: string) => Promise<void>;
    retryTask: (taskId: string) => Promise<void>;
    retryFailedTasks: () => Promise<void>;
    deleteTask: (taskId: string) => Promise<void>;
    clearFailedTasks: () => Promise<void>;
    getEventStreamState: () => {
        connected: number;
        total: number;
        hasErrors: boolean;
    };
};

const ACTIVE_STATUSES: ServerTaskStatus[] = ["queued", "running", "cancelling"];

export class PDF2zhTaskManager {
    private static tasks = new Map<string, PluginTask>();
    private static pollPromise: Promise<void> | null = null;
    private static taskListeners = new Set<() => void>();
    private static dialogWindow: Window | undefined;
    private static eventStream = new TaskEventStream({
        onTaskEvent: (serverUrl, event) =>
            PDF2zhTaskManager.handleServerTaskEvent(serverUrl, event),
        onStateChange: () => PDF2zhTaskManager.notifyTasksChanged(),
    });
    private static importer = new ZoteroTaskImporter({
        getTask: (taskId) => PDF2zhTaskManager.tasks.get(taskId),
        updateTask: (taskId, patch) =>
            PDF2zhTaskManager.updateLocalTask(taskId, patch),
    });

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
            hasActiveTasks: () => this.hasActiveTasks(),
            onTasksChanged: (listener: () => void) =>
                this.onTasksChanged(listener),
            refreshTasks: () => this.refreshTasks(),
            cancelTask: (taskId: string) => this.cancelTask(taskId),
            retryTask: (taskId: string) => this.retryTask(taskId),
            retryFailedTasks: () => this.retryFailedTasks(),
            deleteTask: (taskId: string) => this.deleteTask(taskId),
            clearFailedTasks: () => this.clearFailedTasks(),
            getEventStreamState: () => this.getEventStreamState(),
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
        this.ensureEventStreams();
        dialogWindow.addEventListener("unload", () => {
            if (this.dialogWindow === dialogWindow) {
                this.dialogWindow = undefined;
                this.ensureEventStreams();
            }
        });
    }

    static closeWindow() {
        if (this.dialogWindow && !this.dialogWindow.closed) {
            this.dialogWindow.close();
        }
        this.dialogWindow = undefined;
        this.ensureEventStreams();
    }

    static getTasks(): PluginTask[] {
        return Array.from(this.tasks.values()).sort((left, right) =>
            right.createdAt.localeCompare(left.createdAt),
        );
    }

    static hasActiveTasks(): boolean {
        return Array.from(this.tasks.values()).some(
            (task) =>
                ACTIVE_STATUSES.includes(task.status) ||
                (task.status === "completed" &&
                    (task.importState === "pending" ||
                        task.importState === "importing")),
        );
    }

    static onTasksChanged(listener: () => void): () => void {
        this.taskListeners.add(listener);
        return () => {
            this.taskListeners.delete(listener);
        };
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

        const snapshot = await ServerTaskClient.cancelTask(
            task.serverUrl,
            taskId,
        );
        if (snapshot) {
            this.upsertTask(snapshot, task.serverUrl, {
                itemID: task.itemID,
                source: task.source,
                importState: task.importState,
                importError: task.importError,
            });
        }
    }

    static async retryTask(taskId: string): Promise<void> {
        const task = this.tasks.get(taskId);
        if (!task) {
            throw new Error("任务不存在");
        }

        if (task.status === "completed" && task.importState === "failed") {
            this.updateLocalTask(taskId, {
                importState: "pending",
                importError: undefined,
            });
            await this.importer.importTaskOutputs(taskId);
            return;
        }

        const snapshot = await ServerTaskClient.retryTask(
            task.serverUrl,
            taskId,
        );
        if (snapshot) {
            this.upsertTask(snapshot, task.serverUrl, {
                itemID: task.itemID,
                source: task.source,
                importState: task.importState,
                importError: task.importError,
            });
        }
        this.ensureEventStreams();
    }

    static async retryFailedTasks(): Promise<void> {
        const failedTasks = this.getTasks().filter(
            (task) =>
                task.status === "failed" ||
                (task.status === "completed" && task.importState === "failed"),
        );
        for (const task of failedTasks) {
            await this.retryTask(task.taskId);
        }
    }

    static async deleteTask(taskId: string): Promise<void> {
        const task = this.tasks.get(taskId);
        if (!task) {
            throw new Error("任务不存在");
        }

        await ServerTaskClient.deleteTask(task.serverUrl, taskId);

        this.tasks.delete(taskId);
        this.notifyTasksChanged();
        this.ensureEventStreams();
    }

    static async clearFailedTasks(): Promise<void> {
        const failedTasks = this.getTasks().filter(
            (task) => task.status === "failed",
        );
        if (failedTasks.length === 0) {
            return;
        }

        const serverUrls = new Set(failedTasks.map((task) => task.serverUrl));
        for (const serverUrl of serverUrls) {
            await ServerTaskClient.clearFailedTasks(serverUrl);

            for (const task of failedTasks) {
                if (task.serverUrl === serverUrl) {
                    this.tasks.delete(task.taskId);
                }
            }
        }

        this.notifyTasksChanged();
        this.ensureEventStreams();
    }

    static getEventStreamState(): {
        connected: number;
        total: number;
        hasErrors: boolean;
    } {
        return this.eventStream.getSummary();
    }

    private static async submitTask(item: Zotero.Item, config: ServerConfig) {
        const fileData = await PDF2zhHelperFactory.prepareFileData(item);
        const requestBody = PDF2zhHelperFactory.buildTaskRequestBody(
            fileData,
            config,
        );

        const task = await ServerTaskClient.createTask(
            config.serverUrl,
            requestBody,
        );
        this.upsertTask(task, config.serverUrl, {
            itemID: item.id,
            source: "local",
            importState: "pending",
        });
        this.ensureEventStreams();
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
            let snapshots: ServerTaskSnapshot[];
            try {
                snapshots = await ServerTaskClient.listTasks(serverUrl);
            } catch (_error) {
                continue;
            }
            const serverTaskIds = new Set(
                snapshots.map((snapshot) => snapshot.taskId),
            );
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
                    this.notifyTasksChanged();
                }
            }
        }

        await this.importCompletedLocalTasks();
        this.ensureEventStreams();
    }

    private static async importCompletedLocalTasks(): Promise<void> {
        for (const task of this.getTasks()) {
            if (
                task.source === "local" &&
                task.status === "completed" &&
                task.importState === "pending"
            ) {
                await this.importer.importTaskOutputs(task.taskId);
            }
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
            attempt: snapshot.attempt,
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
        this.notifyTasksChanged();
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
        this.notifyTasksChanged();
    }

    private static ensureEventStreams() {
        const serverUrls = new Set<string>();
        const currentServerUrl = getPref("new_serverip")?.toString() || "";
        const dialogOpen = Boolean(
            this.dialogWindow && !this.dialogWindow.closed,
        );
        const shouldTrackCurrentServer = dialogOpen || this.hasActiveTasks();
        if (currentServerUrl && shouldTrackCurrentServer) {
            serverUrls.add(currentServerUrl);
        }
        for (const task of this.tasks.values()) {
            const shouldTrackTaskServer =
                dialogOpen ||
                ACTIVE_STATUSES.includes(task.status) ||
                (task.status === "completed" &&
                    (task.importState === "pending" ||
                        task.importState === "importing"));
            if (task.serverUrl && shouldTrackTaskServer) {
                serverUrls.add(task.serverUrl);
            }
        }

        this.eventStream.sync(serverUrls);
    }

    private static handleServerTaskEvent(
        serverUrl: string,
        event: ServerTaskEvent,
    ) {
        if (
            (event.type === "snapshot" || event.type === "task") &&
            event.task
        ) {
            const existing = this.tasks.get(event.task.taskId);
            this.upsertTask(event.task, serverUrl, {
                itemID: existing?.itemID,
                source: existing?.source || "remote",
                importState: existing?.importState || "none",
                importError: existing?.importError,
            });
            void this.importCompletedLocalTasks();
            return;
        }

        if (event.type === "deleted" && event.taskId) {
            this.tasks.delete(event.taskId);
            this.notifyTasksChanged();
            this.ensureEventStreams();
        }
    }

    private static notifyTasksChanged(): void {
        for (const listener of this.taskListeners) {
            try {
                listener();
            } catch (error) {
                ztoolkit.log(error);
            }
        }
    }
}
