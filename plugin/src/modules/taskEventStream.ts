import { ServerTaskEvent } from "./pdf2zhTypes";

export type TaskEventStreamState = "connecting" | "open" | "error" | "closed";

type TaskEventStreamEntry = {
    source: EventSource;
    state: TaskEventStreamState;
};

type TaskEventStreamCallbacks = {
    onTaskEvent: (serverUrl: string, event: ServerTaskEvent) => void;
    onStateChange?: (serverUrl: string, state: TaskEventStreamState) => void;
};

export class TaskEventStream {
    private streams = new Map<string, TaskEventStreamEntry>();
    private states = new Map<string, TaskEventStreamState>();

    constructor(private callbacks: TaskEventStreamCallbacks) {}

    sync(serverUrls: Set<string>): void {
        for (const [serverUrl, entry] of this.streams) {
            if (!serverUrls.has(serverUrl)) {
                entry.source.close();
                this.streams.delete(serverUrl);
                this.setState(serverUrl, "closed");
            }
        }

        for (const serverUrl of serverUrls) {
            if (!this.streams.has(serverUrl)) {
                this.open(serverUrl);
            }
        }
    }

    getSummary(): { connected: number; total: number; hasErrors: boolean } {
        let connected = 0;
        let hasErrors = false;

        for (const [serverUrl] of this.streams) {
            const state = this.states.get(serverUrl);
            if (state === "open") {
                connected += 1;
            }
            if (state === "error") {
                hasErrors = true;
            }
        }

        return {
            connected,
            total: this.streams.size,
            hasErrors,
        };
    }

    private open(serverUrl: string): void {
        const mainWindow = Zotero.getMainWindow() as Window & {
            EventSource?: typeof EventSource;
        };
        const EventSourceConstructor =
            typeof EventSource === "undefined"
                ? mainWindow.EventSource
                : EventSource;
        if (!EventSourceConstructor) {
            return;
        }

        this.setState(serverUrl, "connecting");
        const source = new EventSourceConstructor(`${serverUrl}/tasks/events`);
        this.streams.set(serverUrl, {
            source,
            state: "connecting",
        });

        source.onopen = () => {
            this.setState(serverUrl, "open");
        };
        source.onmessage = (message) => {
            let event: ServerTaskEvent;
            try {
                event = JSON.parse(message.data) as ServerTaskEvent;
            } catch (_error) {
                return;
            }
            this.callbacks.onTaskEvent(serverUrl, event);
        };
        source.onerror = () => {
            this.setState(serverUrl, "error");
            ztoolkit.log(`任务进度事件连接异常: ${serverUrl}`);
        };
    }

    private setState(serverUrl: string, state: TaskEventStreamState): void {
        this.states.set(serverUrl, state);
        const entry = this.streams.get(serverUrl);
        if (entry) {
            entry.state = state;
        }
        this.callbacks.onStateChange?.(serverUrl, state);
    }
}
