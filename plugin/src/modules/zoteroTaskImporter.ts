import { PDF2zhHelperFactory, TaskOutputResponse } from "./pdf2zhHelper";
import { PluginTask } from "./pdf2zhTypes";
import { ServerTaskClient } from "./serverTaskClient";

type TaskImporterCallbacks = {
    getTask: (taskId: string) => PluginTask | undefined;
    updateTask: (taskId: string, patch: Partial<PluginTask>) => void;
};

export class ZoteroTaskImporter {
    constructor(private callbacks: TaskImporterCallbacks) {}

    async importTaskOutputs(taskId: string): Promise<void> {
        const task = this.callbacks.getTask(taskId);
        if (!task || task.importState !== "pending") {
            return;
        }

        if (!task.itemID) {
            this.callbacks.updateTask(taskId, {
                importState: "failed",
                importError: "无法找到原始条目",
            });
            return;
        }

        const item = Zotero.Items.get(task.itemID);
        if (!item) {
            this.callbacks.updateTask(taskId, {
                importState: "failed",
                importError: "原始条目已不存在",
            });
            return;
        }

        this.callbacks.updateTask(taskId, {
            importState: "importing",
            importError: undefined,
        });

        try {
            for (const outputMode of task.outputModes) {
                const bytes = await ServerTaskClient.fetchResult(
                    task.serverUrl,
                    task.taskId,
                    outputMode,
                );
                const fileName =
                    task.resultFiles[outputMode] ||
                    `${task.fileName}.${outputMode}.pdf`;
                const output: TaskOutputResponse = {
                    fileName,
                    outputMode,
                    bytes,
                };
                await PDF2zhHelperFactory.handleOutputResponse(output, item, {
                    ...PDF2zhHelperFactory.getServerConfig(),
                    service: task.service,
                    outputModes: task.outputModes,
                });
            }

            this.callbacks.updateTask(taskId, {
                importState: "imported",
                importError: undefined,
            });
        } catch (error) {
            this.callbacks.updateTask(taskId, {
                importState: "failed",
                importError:
                    error instanceof Error ? error.message : String(error),
            });
        }
    }
}
