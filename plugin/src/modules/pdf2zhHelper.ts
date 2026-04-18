import { getPref } from "../utils/prefs";
import { ServerConfig, PDFOperationOptions, OutputMode } from "./pdf2zhTypes";
import { loadLLMApisFromPrefs } from "./preferenceScript";

type ActiveLLMApiConfig = {
    service: string;
    model: string;
    apiKey: string;
    apiUrl: string;
    extraData: Record<string, any>;
} | null;

export type TaskOutputResponse = {
    fileName: string;
    outputMode: OutputMode;
    bytes: Uint8Array;
};

export class PDF2zhHelperFactory {
    private static readonly MAX_RETRIES = 1;
    private static readonly RETRY_DELAY = 2000;

    static async prepareFileData(
        item: Zotero.Item,
    ): Promise<{ fileName: string; base64: string }> {
        const filepath = await this.validatePDFAttachment(item);
        const fileName = PathUtils.filename(filepath);
        const base64 = await this.readPDFAsBase64(filepath);
        return { fileName, base64 };
    }

    static buildTaskRequestBody(
        fileData: { fileName: string; base64: string },
        config: ServerConfig,
    ): Record<string, unknown> {
        const llmApiConfig = this.getActiveLLMApiConfig(config.service);
        const requestBody: Record<string, unknown> = {
            fileName: fileData.fileName,
            fileContent: fileData.base64,
            sourceLang: config.sourceLang,
            targetLang: config.targetLang,
            outputModes: config.outputModes,
            service: config.service,
            skipLastPages: config.skipLastPages,
            qps: config.qps,
            poolSize: config.poolSize,
            ocr: config.ocr,
            autoOcr: config.autoOcr,
            noWatermark: config.noWatermark,
            fontFamily: config.fontFamily,
        };
        if (llmApiConfig) {
            requestBody.llm_api = llmApiConfig;
        }
        return requestBody;
    }

    static async handleOutputResponse(
        response: TaskOutputResponse,
        item: Zotero.Item,
        config: ServerConfig,
    ) {
        const options = this.getPDFOptions();
        const tempPath = PathUtils.join(PathUtils.tempDir, response.fileName);
        await IOUtils.write(tempPath, response.bytes);
        try {
            await this.addAttachment({
                item,
                filePath: tempPath,
                options,
                outputMode: response.outputMode,
                service: config.service,
            });
        } finally {
            if (await this.safeExists(tempPath)) {
                await IOUtils.remove(tempPath);
            }
        }
    }

    static async blobToBase64(blob: Blob): Promise<string> {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result as string);
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });
    }

    static async safeExists(path: string) {
        try {
            return await IOUtils.exists(path);
        } catch (error) {
            ztoolkit.log(`检查路径 ${path} 时出错:`, error);
            return false;
        }
    }

    static async getAttachmentItem(
        item: Zotero.Item,
    ): Promise<Zotero.Item | false> {
        let attachItem;
        if (item.isAttachment()) {
            attachItem = item;
        } else if (item.isRegularItem()) {
            attachItem = await item.getBestAttachment();
        }
        return attachItem || false;
    }

    static async validatePDFAttachment(item: Zotero.Item): Promise<string> {
        const attachItem = await this.getAttachmentItem(item);
        if (!attachItem) {
            throw new Error("No valid attachment found");
        }
        const filepath = attachItem.getFilePath().toString();
        if (!filepath?.endsWith(".pdf")) {
            throw new Error("Please select a PDF attachment");
        }
        const exists = await this.safeExists(filepath);
        if (!exists) {
            throw new Error("PDF file not found");
        }
        return filepath;
    }

    static async readPDFAsBase64(filepath: string): Promise<string> {
        const contentRaw = await IOUtils.read(filepath);
        const normalized = new Uint8Array(contentRaw.byteLength);
        normalized.set(contentRaw);
        const blob = new Blob([normalized], { type: "application/pdf" });
        return this.blobToBase64(blob);
    }

    static getPDFOptions(): PDFOperationOptions {
        return {
            rename: this.isTrue(getPref("rename")),
            openAfterProcess: this.isTrue(getPref("openAfterTranslate")),
        };
    }

    static async addAttachment(params: {
        item: Zotero.Item;
        filePath: string;
        options: PDFOperationOptions;
        outputMode: OutputMode;
        service: string;
    }) {
        const { item, filePath, options, outputMode, service } = params;
        const parentItemID = this.getParentItemID(item);
        let targetItem = item;
        if (item.isAttachment() && parentItemID) {
            targetItem = Zotero.Items.get(parentItemID);
        }

        let newTitle = `${service}-${outputMode}`;
        const shortTitle = targetItem.getField("shortTitle");
        if (shortTitle && shortTitle.length > 0) {
            newTitle = `${shortTitle}-${service}-${outputMode}`;
        }

        const attachment = await Zotero.Attachments.importFromFile({
            file: filePath,
            parentItemID: parentItemID == undefined ? undefined : parentItemID,
            libraryID: item.libraryID,
            collections:
                parentItemID == undefined
                    ? this.getCollections(item)
                    : undefined,
            title: options.rename ? newTitle : PathUtils.filename(filePath),
        });

        if (options.openAfterProcess && attachment?.id) {
            Zotero.Reader.open(attachment.id);
        }
    }

    static getServerConfig(): ServerConfig {
        return {
            serverUrl: getPref("new_serverip")?.toString() || "",
            service: this.normalizeServiceName(
                getPref("service")?.toString() || "siliconflowfree",
            ),
            sourceLang: getPref("sourceLang")?.toString() || "en",
            targetLang: getPref("targetLang")?.toString() || "zh-CN",
            outputModes: this.getOutputModesFromPrefs(),
            skipLastPages: getPref("skipLastPages")?.toString() || "0",
            qps: getPref("qps")?.toString() || "10",
            poolSize: getPref("poolSize")?.toString() || "0",
            ocr: getPref("ocr")?.toString() || "false",
            autoOcr: getPref("autoOcr")?.toString() || "true",
            noWatermark: getPref("noWatermark")?.toString() || "true",
            fontFamily: getPref("fontFamily")?.toString() || "auto",
        };
    }

    static getActiveLLMApiConfig(service: string): ActiveLLMApiConfig {
        loadLLMApisFromPrefs();
        if (!addon.data.llmApis?.map) {
            return null;
        }
        for (const [, llmApi] of addon.data.llmApis.map) {
            if (
                llmApi.activate &&
                this.normalizeServiceName(llmApi.service) === service
            ) {
                return {
                    service,
                    model: llmApi.model,
                    apiKey: llmApi.apiKey,
                    apiUrl: llmApi.apiUrl,
                    extraData: llmApi.extraData || {},
                };
            }
        }
        return null;
    }

    static isTrue(value: string | number | boolean | undefined): boolean {
        if (value == undefined) return false;
        return (
            value == true ||
            value == "true" ||
            value == "1" ||
            value == "True" ||
            value == "TRUE" ||
            value == 1
        );
    }

    static async retryOperation<T>(
        operation: () => Promise<T>,
        maxRetries: number = this.MAX_RETRIES,
        delay: number = this.RETRY_DELAY,
    ): Promise<T> {
        let lastError: Error;
        for (let attempt = 1; attempt <= maxRetries; attempt++) {
            try {
                return await operation();
            } catch (error) {
                lastError =
                    error instanceof Error ? error : new Error(String(error));
                if (attempt === maxRetries) {
                    throw lastError;
                }
                ztoolkit.log(
                    `操作失败，第 ${attempt} 次重试 (共 ${maxRetries} 次): ${lastError.message}`,
                );
                await new Promise((resolve) =>
                    setTimeout(resolve, delay * attempt),
                );
            }
        }
        throw lastError!;
    }

    static getParentItemID(item: Zotero.Item): number | undefined {
        if (item.isAttachment()) {
            const parentItemID = item.parentItemID;
            return parentItemID != null && parentItemID !== false
                ? parentItemID
                : undefined;
        }
        return item.id;
    }

    static getCollections(item: Zotero.Item): number[] | undefined {
        const collections = item.getCollections();
        return collections.length > 0 ? [collections[0]] : undefined;
    }

    static normalizeServiceName(value: string): string {
        return value.trim().toLowerCase().replace(/[-_]/g, "");
    }

    static getOutputModesFromPrefs(): OutputMode[] {
        const modes: OutputMode[] = [];
        if (this.isTrue(getPref("outputMono"))) {
            modes.push("mono");
        }
        if (this.isTrue(getPref("outputDual"))) {
            modes.push("dual");
        }
        if (modes.length === 0) {
            return ["dual"];
        }
        return modes;
    }
}
