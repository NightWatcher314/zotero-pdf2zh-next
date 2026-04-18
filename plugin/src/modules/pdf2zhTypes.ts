export type OutputMode = "mono" | "dual";
export type ServerTaskStatus =
    | "queued"
    | "running"
    | "cancelling"
    | "completed"
    | "failed"
    | "cancelled";

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
    noWatermark: string;
    fontFamily: string;
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
