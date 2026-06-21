import {
    DiagnosticMessage,
    OutputMode,
    ServerTaskSnapshot,
} from "./pdf2zhTypes";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";

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

export class ServerTaskClient {
    static async listTasks(serverUrl: string): Promise<ServerTaskSnapshot[]> {
        const response = await fetch(`${serverUrl}/tasks`);
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }

        const payload = (await response.json()) as TaskListResponse;
        return payload.tasks || [];
    }

    static async createTask(
        serverUrl: string,
        requestBody: Record<string, unknown>,
    ): Promise<ServerTaskSnapshot> {
        const response = await PDF2zhHelperFactory.retryOperation(() =>
            fetch(`${serverUrl}/tasks`, {
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
        return result.task;
    }

    static async cancelTask(
        serverUrl: string,
        taskId: string,
    ): Promise<ServerTaskSnapshot | undefined> {
        return this.postTaskAction(serverUrl, taskId, "cancel");
    }

    static async retryTask(
        serverUrl: string,
        taskId: string,
    ): Promise<ServerTaskSnapshot | undefined> {
        return this.postTaskAction(serverUrl, taskId, "retry");
    }

    static async deleteTask(serverUrl: string, taskId: string): Promise<void> {
        const response = await fetch(`${serverUrl}/tasks/${taskId}`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
        });
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }
    }

    static async clearFailedTasks(serverUrl: string): Promise<void> {
        const response = await fetch(`${serverUrl}/tasks/clear-failed`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        });
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }
    }

    static async fetchResult(
        serverUrl: string,
        taskId: string,
        outputMode: OutputMode,
    ): Promise<Uint8Array> {
        const response = await fetch(
            `${serverUrl}/tasks/${taskId}/result?mode=${outputMode}`,
        );
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }

        return new Uint8Array(await response.arrayBuffer());
    }

    private static async postTaskAction(
        serverUrl: string,
        taskId: string,
        action: "cancel" | "retry",
    ): Promise<ServerTaskSnapshot | undefined> {
        const response = await fetch(`${serverUrl}/tasks/${taskId}/${action}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        });
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }

        const result = (await response.json()) as { task?: ServerTaskSnapshot };
        return result.task;
    }

    private static async readErrorMessage(response: Response): Promise<string> {
        try {
            const payload = (await response.json()) as {
                message?: string;
                diagnostics?: DiagnosticMessage[];
                status?: string;
            };
            return [
                payload.message || `服务器返回错误: ${response.status}`,
                this.formatDiagnostics(payload.diagnostics),
            ]
                .filter(Boolean)
                .join("\n\n");
        } catch (_error) {
            return `服务器返回错误: ${response.status}`;
        }
    }

    private static formatDiagnostics(
        diagnostics?: DiagnosticMessage[],
    ): string {
        if (!diagnostics?.length) {
            return "";
        }
        return diagnostics
            .map((diagnostic) => {
                const line = [
                    `[${diagnostic.severity}]`,
                    diagnostic.code,
                    diagnostic.message,
                ]
                    .filter(Boolean)
                    .join(" ");
                if (diagnostic.suggestion) {
                    return `${line}\n建议: ${diagnostic.suggestion}`;
                }
                return line;
            })
            .join("\n");
    }
}
