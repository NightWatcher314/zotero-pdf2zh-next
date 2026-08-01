export type OutputMode = "mono" | "dual";
export type ServerTaskStatus =
    "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";

export interface ServerConfig {
    serverUrl: string;
    service: string;
    sourceLang: string;
    targetLang: string;
    outputModes: OutputMode[];
    skipLastPages: string;
    qps: string;
    poolSize: string;
    ocr: string;
    autoOcr: string;
    translateTableText: string;
    skipTextChecks: string;
    noWatermark: string;
    disableTermExtraction: string;
    fontFamily: string;
}

export interface DiagnosticMessage {
    code: string;
    severity: "info" | "warning" | "error";
    message: string;
    suggestion?: string;
}

export interface ServerHealthResponse {
    status?: string;
    version?: string;
    pythonVersion?: string;
    pdf2zhVersion?: string;
    babeldocVersion?: string;
    workspace?: {
        path?: string;
        writable?: boolean;
        freeBytes?: number;
    };
    tasks?: {
        total?: number;
        active?: number;
        failed?: number;
        completed?: number;
    };
}

export interface ValidateConfigResponse {
    status?: string;
    service?: string;
    model?: string | null;
    message?: string;
    diagnostics?: DiagnosticMessage[];
    liveTest?: {
        enabled: boolean;
        ok?: boolean;
        message?: string;
    };
}

export interface ServerErrorResponse {
    status?: "error" | string;
    message?: string;
    diagnostics?: DiagnosticMessage[];
}

export interface PDFOperationOptions {
    rename: boolean;
    openAfterProcess: boolean;
}

export interface ServerTaskSnapshot {
    taskId: string;
    fileName: string;
    service: string;
    outputModes: OutputMode[];
    status: ServerTaskStatus;
    stage: string | null;
    stageCurrent: number;
    stageTotal: number;
    stageProgress: number;
    overallProgress: number;
    error: string | null;
    errorDiagnostics?: DiagnosticMessage[];
    attempt?: number;
    resultFiles: Partial<Record<OutputMode, string>>;
    createdAt: string;
    updatedAt: string;
    canCancel: boolean;
    cancelRequested: boolean;
}

export interface PluginTask extends ServerTaskSnapshot {
    itemID?: number;
    serverUrl: string;
    source: "local" | "remote";
    importState: "pending" | "importing" | "imported" | "failed" | "none";
    importError?: string;
}

export interface ServerTaskEvent {
    type: "snapshot" | "task" | "deleted";
    task?: ServerTaskSnapshot;
    taskId?: string;
}
