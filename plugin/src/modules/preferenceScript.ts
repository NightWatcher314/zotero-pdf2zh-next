import { config } from "../../package.json";
import { getPref, setPref } from "../utils/prefs";
import {
    getActiveLLMApiByService,
    llmApiManager,
    LLMApiData,
    emptyLLMApi,
    formatExtraDataForDisplay,
} from "./llmApiManager";
import axios from "axios";

type ValidateConfigResponse = {
    status?: string;
    service?: string;
    model?: string | null;
    message?: string;
};

function normalizeServiceName(value: string): string {
    return value.trim().toLowerCase().replace(/[-_]/g, "");
}

export async function registerPrefsScripts(_window: Window) {
    if (!addon.data.prefs) {
        addon.data.prefs = {
            window: _window,
            columns: [],
            rows: [],
        };
    } else {
        addon.data.prefs.window = _window;
    }
    if (!addon.data.llmApis) {
        addon.data.llmApis = {
            map: new Map<string, LLMApiData>(),
            cachedKeys: [],
        };
    }
    const normalizedService = normalizeServiceName(
        getPref("service")?.toString() || "siliconflowfree",
    );
    if (normalizedService !== getPref("service")) {
        setPref("service", normalizedService);
    }
    bindPrefEvents();
    initTableUI();
}

function bindPrefEvents() {
    const { window } = addon.data.prefs ?? {};
    if (!window) return;
    const doc = window.document;
    if (!doc) return;

    const sourceLangSelect = doc.getElementById(
        `zotero-prefpane-${config.addonRef}-sourceLangSelect`,
    );
    const targetLangSelect = doc.getElementById(
        `zotero-prefpane-${config.addonRef}-targetLangSelect`,
    );
    const outputMonoCheckbox = doc.getElementById(
        `zotero-prefpane-${config.addonRef}-outputMono`,
    ) as XUL.Checkbox | null;
    const outputDualCheckbox = doc.getElementById(
        `zotero-prefpane-${config.addonRef}-outputDual`,
    ) as XUL.Checkbox | null;

    for (const [langName, langCode] of Object.entries(lang_map)) {
        const option = doc.createElement("option");
        option.value = langCode;
        option.textContent = langName;
        sourceLangSelect?.appendChild(option.cloneNode(true));
        targetLangSelect?.appendChild(option.cloneNode(true));
    }

    const ensureOutputModes = (fallbackKey: "outputMono" | "outputDual") => {
        if (!outputMonoCheckbox || !outputDualCheckbox) {
            return;
        }
        if (outputMonoCheckbox.checked || outputDualCheckbox.checked) {
            return;
        }

        if (fallbackKey === "outputMono") {
            outputMonoCheckbox.checked = true;
        } else {
            outputDualCheckbox.checked = true;
        }
        setPref(fallbackKey, true);
    };

    outputMonoCheckbox?.addEventListener("command", () => {
        ensureOutputModes("outputDual");
    });
    outputDualCheckbox?.addEventListener("command", () => {
        ensureOutputModes("outputMono");
    });

    doc
        .querySelector(`#zotero-prefpane-${config.addonRef}-checkConnection`)
        ?.addEventListener("click", async () => {
            await checkServerConnection();
        });

    doc
        .querySelector(
            `#zotero-prefpane-${config.addonRef}-llmapi-table-container`,
        )
        ?.addEventListener("showing", () => {
            updateLLMApiTableUI();
        });

    const addButton = doc.getElementById(
        `zotero-prefpane-${config.addonRef}-llmapi-add`,
    );
    const removeButton = doc.getElementById(
        `zotero-prefpane-${config.addonRef}-llmapi-remove`,
    );
    const editButton = doc.getElementById(
        `zotero-prefpane-${config.addonRef}-llmapi-edit`,
    );
    const activateButton = doc.getElementById(
        `zotero-prefpane-${config.addonRef}-llmapi-activate`,
    );
    const toTopButton = doc.getElementById(
        `zotero-prefpane-${config.addonRef}-llmapi-totop`,
    );

    addButton?.addEventListener("command", async () => {
        await openLLMApiEditDialog();
    });
    removeButton?.addEventListener("command", () => {
        const selectedKeys = getLLMApiSelection();
        selectedKeys.forEach((key) => {
            if (key) {
                llmApiManager.deleteLLMApi(key);
                addon.data.llmApis?.map.delete(key);
            }
        });
        updateCachedLLMApiKeys();
        saveLLMApisToPrefs();
        updateLLMApiTableUI();
    });
    editButton?.addEventListener("command", async () => {
        const selectedKeys = getLLMApiSelection();
        if (selectedKeys.length === 1) {
            await openLLMApiEditDialog(selectedKeys[0]);
        }
    });
    activateButton?.addEventListener("command", () => {
        const selectedKeys = getLLMApiSelection();
        if (selectedKeys.length !== 1) {
            return;
        }
        const key = selectedKeys[0];
        const llmApi = addon.data.llmApis?.map.get(key);
        if (!llmApi) {
            return;
        }
        if (llmApi.activate) {
            llmApiManager.deactivateLLMApi(key);
        } else {
            llmApiManager.activateLLMApi(key);
        }
        addon.data.llmApis?.map.set(key, llmApiManager.getLLMApi(key)!);
        saveLLMApisToPrefs();
        updateLLMApiTableUI();
    });
    toTopButton?.addEventListener("command", () => {
        const selectedKeys = getLLMApiSelection();
        if (selectedKeys.length !== 1) {
            return;
        }
        const key = selectedKeys[0];
        const llmApi = addon.data.llmApis?.map.get(key);
        if (!llmApi) {
            return;
        }
        const llmApis = Array.from(addon.data.llmApis?.map.values() || []);
        const index = llmApis.findIndex((entry) => entry.key === key);
        if (index === -1) {
            return;
        }
        llmApis.splice(index, 1);
        llmApis.unshift(llmApi);
        addon.data.llmApis?.map.clear();
        llmApis.forEach((entry) => addon.data.llmApis?.map.set(entry.key, entry));
        updateCachedLLMApiKeys();
        saveLLMApisToPrefs();
        updateLLMApiTableUI();
    });
}

export async function initTableUI() {
    if (!addon.data.prefs?.window) return;
    loadLLMApisFromPrefs();
    const renderLock = Zotero.Promise.defer();
    addon.data.prefs.tableHelper = new ztoolkit.VirtualizedTable(
        addon.data.prefs.window!,
    )
        .setContainerId(
            `zotero-prefpane-${config.addonRef}-llmapi-table-container`,
        )
        .setProp({
            id: `zotero-prefpane-${config.addonRef}-llmapi-table`,
            columns: [
                { dataKey: "service", label: "服务", width: 160 },
                { dataKey: "model", label: "模型", width: 220 },
                { dataKey: "apiUrl", label: "API URL", width: 170 },
                { dataKey: "apiKey", label: "API Key", width: 100 },
                { dataKey: "activate", label: "激活", width: 70 },
                { dataKey: "extraData", label: "额外参数", width: 200 },
            ],
            showHeader: true,
            multiSelect: true,
            staticColumns: false,
            disableFontSizeScaling: true,
        })
        .setProp(
            "getRowCount",
            () => addon.data.llmApis?.cachedKeys.length || 0,
        )
        .setProp("getRowData", getRowData)
        .setProp("onSelectionChange", () => {
            const selectedKeys = getLLMApiSelection();
            addon.data.llmApis.selectedKey = selectedKeys[0];
            addon.data.prefs?.window?.document
                .querySelectorAll(".llmapi-selection")
                ?.forEach((e) =>
                    setButtonDisabled(
                        e as XULButtonElement,
                        selectedKeys.length === 0,
                    ),
                );
            addon.data.prefs?.window?.document
                .querySelectorAll(".llmapi-selection-single")
                ?.forEach((e) =>
                    setButtonDisabled(
                        e as XULButtonElement,
                        selectedKeys.length !== 1,
                    ),
                );
        })
        .render(-1, () => renderLock.resolve());
    await renderLock.promise;
}

function updateCachedLLMApiKeys() {
    addon.data.llmApis.cachedKeys = Array.from(
        addon.data.llmApis?.map.keys() || [],
    );
}

async function openLLMApiEditDialog(key?: string): Promise<boolean> {
    const llmApi = key ? addon.data.llmApis?.map.get(key) : emptyLLMApi;
    const dialogData = {
        service: llmApi?.service || "",
        model: llmApi?.model || "",
        apiKey: llmApi?.apiKey || "",
        apiUrl: llmApi?.apiUrl || "",
        activate: llmApi?.activate || false,
        extraData: llmApi?.extraData || {},
    };

    const windowArgs: {
        _initPromise: any;
        data: {
            service: string;
            model: string;
            apiKey: string;
            apiUrl: string;
            activate: boolean;
            extraData: any;
        };
        isEdit: boolean;
        result?: {
            success: boolean;
            data: {
                service: string;
                model: string;
                apiKey: string;
                apiUrl: string;
                activate: boolean;
                extraData?: Record<string, any>;
            };
        };
    } = {
        _initPromise: Zotero.Promise.defer(),
        data: dialogData,
        isEdit: !!key,
    };

    const dialogWindow = Zotero.getMainWindow().openDialog(
        `chrome://${config.addonRef}/content/llmApiEditor.xhtml`,
        `${config.addonRef}-llmApiEditor`,
        `chrome,centerscreen,resizable,status,dialog=no`,
        windowArgs,
    );
    if (!dialogWindow) {
        return false;
    }
    await windowArgs._initPromise.promise;

    const result = await new Promise<any>((resolve) => {
        const checkClosed = () => {
            if (dialogWindow.closed) {
                resolve(windowArgs.result);
            } else {
                setTimeout(checkClosed, 100);
            }
        };
        checkClosed();
    });

    if (!result || !result.success) {
        return false;
    }

    const userData = result.data;
    const newLLMApi: LLMApiData = {
        key: key || Zotero.Utilities.generateObjectKey(),
        service: normalizeServiceName(userData.service || ""),
        model: userData.model || "",
        apiKey: userData.apiKey,
        apiUrl: userData.apiUrl,
        activate: userData.activate,
        extraData: userData.extraData || {},
    };
    addon.data.llmApis?.map.set(newLLMApi.key, newLLMApi);
    updateCachedLLMApiKeys();
    llmApiManager.updateLLMApi(newLLMApi);
    saveLLMApisToPrefs();
    updateLLMApiTableUI();
    return true;
}

function saveLLMApisToPrefs() {
    if (!addon.data.llmApis) return;
    const llmApisArray = Array.from(addon.data.llmApis.map.values());
    setPref("llmApis", JSON.stringify(llmApisArray) as string);
}

export function loadLLMApisFromPrefs() {
    const llmApisJson = getPref("llmApis");
    if (!llmApisJson || typeof llmApisJson !== "string") {
        return;
    }
    try {
        const llmApisArray = JSON.parse(llmApisJson);
        if (!Array.isArray(llmApisArray)) {
            return;
        }
        addon.data.llmApis?.map.clear();
        llmApisArray.forEach((llmApi: LLMApiData) => {
            if (llmApi.key && llmApi.service) {
                if (llmApi.activate === undefined) {
                    llmApi.activate = false;
                }
                if (!llmApi.extraData) {
                    llmApi.extraData = {};
                }
                llmApi.service = normalizeServiceName(llmApi.service);
                addon.data.llmApis?.map.set(llmApi.key, llmApi);
                llmApiManager.updateLLMApi(llmApi);
            }
        });
        updateCachedLLMApiKeys();
    } catch (error) {
        ztoolkit.log("Error loading LLM APIs from prefs:", error);
    }
}

function updateLLMApiTableUI() {
    setTimeout(() => addon.data.prefs?.tableHelper?.treeInstance.invalidate());
}

function setButtonDisabled(button: XUL.Button, disabled: boolean) {
    if (button) {
        button.disabled = disabled;
    }
}

function getRowData(index: number) {
    const keys = addon.data.llmApis?.cachedKeys || [];
    let llmApi = emptyLLMApi;
    if (keys.length > index) {
        const key = keys[index];
        llmApi = addon.data.llmApis?.map.get(key) || emptyLLMApi;
    }
    return {
        key: llmApi.key || "",
        service: llmApi.service || "",
        model: llmApi.model || "",
        apiUrl: llmApi.apiUrl || "",
        apiKey: llmApi.apiKey || "",
        extraData: formatExtraDataForDisplay(llmApi.extraData),
        activate: llmApi.activate ? "✅" : "",
    };
}

function getLLMApiSelection() {
    const indices =
        addon.data.prefs?.tableHelper?.treeInstance?.selection.selected;
    if (!indices) {
        return [];
    }
    const keys = addon.data.llmApis?.cachedKeys || [];
    return Array.from(indices).map((i) => keys[i]) || [];
}

const lang_map = {
    English: "en",
    "Simplified Chinese": "zh-CN",
    "Traditional Chinese - Hong Kong": "zh-HK",
    "Traditional Chinese - Taiwan": "zh-TW",
    Japanese: "ja",
    Korean: "ko",
    Polish: "pl",
    Russian: "ru",
    Spanish: "es",
    Portuguese: "pt",
    "Brazilian Portuguese": "pt-BR",
    French: "fr",
    Malay: "ms",
    Indonesian: "id",
    Turkmen: "tk",
    "Filipino (Tagalog)": "tl",
    Vietnamese: "vi",
    "Kazakh (Latin)": "kk",
    German: "de",
    Dutch: "nl",
    Irish: "ga",
    Italian: "it",
    Greek: "el",
    Swedish: "sv",
    Danish: "da",
    Norwegian: "no",
    Icelandic: "is",
    Finnish: "fi",
    Ukrainian: "uk",
    Czech: "cs",
    Romanian: "ro",
    Hungarian: "hu",
    Slovak: "sk",
    Croatian: "hr",
    Estonian: "et",
    Latvian: "lv",
    Lithuanian: "lt",
    Belarusian: "be",
    Macedonian: "mk",
    Albanian: "sq",
    "Serbian (Cyrillic)": "sr",
    Slovenian: "sl",
    Catalan: "ca",
    Bulgarian: "bg",
    Maltese: "mt",
    Swahili: "sw",
    Amharic: "am",
    Oromo: "om",
    Tigrinya: "ti",
    "Haitian Creole": "ht",
    Latin: "la",
    Lao: "lo",
    Malayalam: "ml",
    Gujarati: "gu",
    Thai: "th",
    Burmese: "my",
    Tamil: "ta",
    Telugu: "te",
    Oriya: "or",
    Armenian: "hy",
    "Mongolian (Cyrillic)": "mn",
    Georgian: "ka",
    Khmer: "km",
    Bosnian: "bs",
    Luxembourgish: "lb",
    Romansh: "rm",
    Turkish: "tr",
    Sinhala: "si",
    Uzbek: "uz",
    Kyrgyz: "ky",
    Tajik: "tg",
    Abkhazian: "ab",
    Afar: "aa",
    Afrikaans: "af",
    Akan: "ak",
    Aragonese: "an",
    Avaric: "av",
    Ewe: "ee",
    Aymara: "ay",
    Ojibwa: "oj",
    Occitan: "oc",
    Ossetian: "os",
    Pali: "pi",
    Bashkir: "ba",
    Basque: "eu",
    Breton: "br",
    Chamorro: "ch",
    Chechen: "ce",
    Chuvash: "cv",
    Tswana: "tn",
    "Ndebele, South": "nr",
    Ndonga: "ng",
    Faroese: "fo",
    Fijian: "fj",
    "Frisian, Western": "fy",
    Ganda: "lg",
    Kongo: "kg",
    Kalaallisut: "kl",
    "Church Slavic": "cu",
    Guarani: "gn",
    Interlingua: "ia",
    Herero: "hz",
    Kikuyu: "ki",
    Rundi: "rn",
    Kinyarwanda: "rw",
    Galician: "gl",
    Kanuri: "kr",
    Cornish: "kw",
    Komi: "kv",
    Xhosa: "xh",
    Corsican: "co",
    Cree: "cr",
    Quechua: "qu",
    "Kurdish (Latin)": "ku",
    Kuanyama: "kj",
    Limburgan: "li",
    Lingala: "ln",
    Manx: "gv",
    Malagasy: "mg",
    Marshallese: "mh",
    Maori: "mi",
    Navajo: "nv",
    Nauru: "na",
    Nyanja: "ny",
    "Norwegian Nynorsk": "nn",
    Sardinian: "sc",
    "Northern Sami": "se",
    Samoan: "sm",
    Sango: "sg",
    Shona: "sn",
    Esperanto: "eo",
    "Scottish Gaelic": "gd",
    Somali: "so",
    "Southern Sotho": "st",
    Tatar: "tt",
    Tahitian: "ty",
    Tongan: "to",
    Twi: "tw",
    Walloon: "wa",
    Welsh: "cy",
    Venda: "ve",
    Volapük: "vo",
    Interlingue: "ie",
    "Hiri Motu": "ho",
    Igbo: "ig",
    Ido: "io",
    Inuktitut: "iu",
    Inupiaq: "ik",
    "Sichuan Yi": "ii",
    Yoruba: "yo",
    Zhuang: "za",
    Tsonga: "ts",
    Zulu: "zu",
};

async function checkServerConnection() {
    const serverUrl = getPref("new_serverip")?.toString() || "";
    if (!serverUrl) {
        ztoolkit.getGlobal("alert")("请先设置Server地址");
        return;
    }

    const progressWindow = new ztoolkit.ProgressWindow("Server连接检查", {
        closeOnClick: false,
        closeTime: -1,
    }).createLine({
        text: "正在检查Server连接...",
        type: "default",
        progress: 20,
    });
    progressWindow.show();

    try {
        const healthResponse = await axios.get(`${serverUrl}/health`, {
            timeout: 10000,
            headers: { "Content-Type": "application/json" },
        });

        if (healthResponse.status !== 200 || !healthResponse.data) {
            throw new Error(`Server返回错误状态: ${healthResponse.status}`);
        }

        progressWindow.changeLine({
            text: "Server已连接，正在检查当前LLM配置...",
            type: "default",
            progress: 60,
        });

        const service = normalizeServiceName(
            getPref("service")?.toString() || "siliconflowfree",
        );
        const llmApi = getActiveLLMApiByService(service);
        const validateResponse = await axios.post<ValidateConfigResponse>(
            `${serverUrl}/validate-config`,
            {
                service,
                sourceLang: getPref("sourceLang")?.toString() || "en",
                targetLang: getPref("targetLang")?.toString() || "zh-CN",
                qps: getPref("qps")?.toString() || "1",
                poolSize: getPref("poolSize")?.toString() || "0",
                ocr: getPref("ocr")?.toString() || "false",
                autoOcr: getPref("autoOcr")?.toString() || "true",
                noWatermark: getPref("noWatermark")?.toString() || "true",
                fontFamily: getPref("fontFamily")?.toString() || "auto",
                llm_api: llmApi
                    ? {
                          service,
                          model: llmApi.model,
                          apiKey: llmApi.apiKey,
                          apiUrl: llmApi.apiUrl,
                          extraData: llmApi.extraData || {},
                      }
                    : {},
            },
            {
                timeout: 15000,
                headers: { "Content-Type": "application/json" },
            },
        );

        if (validateResponse.status !== 200 || !validateResponse.data) {
            throw new Error(`配置检查失败: ${validateResponse.status}`);
        }

        const healthData = healthResponse.data;
        const validateData = validateResponse.data;
        progressWindow.changeLine({
            text: `✓ 检查通过：${validateData.service || service}${validateData.model ? ` / ${validateData.model}` : ""}`,
            type: "success",
            progress: 100,
        });

        setTimeout(() => {
            progressWindow.close();
            ztoolkit.getGlobal("alert")(
                `✓ 检查通过！\n\nServer地址: ${serverUrl}\nServer版本: ${healthData.version || "未知"}\n翻译服务: ${validateData.service || service}\n模型: ${validateData.model || "未返回"}\nLLM配置: 正常`,
            );
        }, 1000);
    } catch (error) {
        let errorMsg = "未知错误";
        let troubleshooting = "";

        if (axios.isAxiosError(error)) {
            if (
                error.code === "ECONNABORTED" ||
                error.message.includes("timeout")
            ) {
                errorMsg = "连接超时（10秒）";
                troubleshooting =
                    "请确认Server已启动、网络可达，并检查是否有防火墙拦截。";
            } else if (error.response) {
                const responseMessage =
                    typeof error.response.data?.message === "string"
                        ? error.response.data.message
                        : "";
                errorMsg = responseMessage
                    ? responseMessage
                    : `Server返回错误: ${error.response.status}`;
                troubleshooting = "请检查Server地址、当前服务对应的LLM配置，以及Server日志。";
            } else if (error.request) {
                errorMsg = "无法连接到Server";
                troubleshooting =
                    "请确认Server已运行，并检查地址格式，例如: http://localhost:8890";
            } else {
                errorMsg = error.message;
            }
        } else if (error instanceof Error) {
            errorMsg = error.message;
        }

        progressWindow.changeLine({
            text: `✗ 连接失败: ${errorMsg}`,
            type: "error",
            progress: 100,
        });

        setTimeout(() => {
            progressWindow.close();
            ztoolkit.getGlobal("alert")(
                `✗ 连接失败\n\n错误信息: ${errorMsg}\n\n${troubleshooting}`,
            );
        }, 1500);
    }
}
