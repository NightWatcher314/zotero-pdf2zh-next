// lifecycle hooks
import { PDF2zhBasicFactory, PDF2zhUIFactory } from "./modules/pdf2zh";
import { PDF2zhTaskManager } from "./modules/pdf2zhTaskManager";
import { initLocale } from "./utils/locale";
import { createZToolkit } from "./utils/ztoolkit";
import { registerPrefsScripts, initTableUI } from "./modules/preferenceScript";

async function onStartup() {
    await Promise.all([
        Zotero.initializationPromise,
        Zotero.unlockPromise,
        Zotero.uiReadyPromise,
    ]);
    initLocale();
    PDF2zhBasicFactory.registerPrefs();
    await Promise.all(
        Zotero.getMainWindows().map((win) => onMainWindowLoad(win)),
    );
}

async function onMainWindowLoad(_win: Window): Promise<void> {
    addon.data.ztoolkit = createZToolkit();
    PDF2zhUIFactory.registerRightClickMenuItem();
    await new Promise((resolve) => setTimeout(resolve, 200));
    initTableUI();
}

async function onPrefsEvent(type: string, data: { [key: string]: any }) {
    if (type === "load") {
        registerPrefsScripts(data.window);
    }
}

async function onMainWindowUnload(_win: Window): Promise<void> {
    ztoolkit.unregisterAll();
    addon.data.dialog?.window?.close();
    PDF2zhTaskManager.closeWindow();
}

function onShutdown(): void {
    ztoolkit.unregisterAll();
    addon.data.dialog?.window?.close();
    PDF2zhTaskManager.closeWindow();
    addon.data.alive = false;
    // @ts-ignore - Plugin instance is not typed
    delete Zotero[addon.data.config.addonInstance];
}

async function onNotify(
    _event: string,
    _type: string,
    _ids: Array<string | number>,
    _extraData: { [key: string]: any },
) {
    return;
}

function onShortcuts(_type: string) {}

function onDialogEvents(type: string) {
    if (type === "translatePDF") {
        PDF2zhTaskManager.processWorker();
    }
    if (type === "openTaskManager") {
        PDF2zhTaskManager.openWindow();
    }
}

export default {
    onStartup,
    onShutdown,
    onMainWindowLoad,
    onMainWindowUnload,
    onNotify,
    onPrefsEvent,
    onShortcuts,
    onDialogEvents,
};
