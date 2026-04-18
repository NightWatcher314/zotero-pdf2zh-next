import { MenuitemOptions } from "zotero-plugin-toolkit";
import { getString } from "../utils/locale";

export class PDF2zhBasicFactory {
    static registerPrefs() {
        Zotero.PreferencePanes.register({
            pluginID: addon.data.config.addonID,
            src: rootURI + "content/preferences.xhtml",
            label: getString("prefs-title"),
            image: `chrome://${addon.data.config.addonRef}/content/icons/favicon.svg`,
        });
    }
}

export class PDF2zhUIFactory {
    static registerRightClickMenuItem() {
        const menuIcon = `chrome://${addon.data.config.addonRef}/content/icons/favicon@0.5x.svg`;
        const menuPrefix = `zotero-itemmenu-${addon.data.config.addonRef}`;
        const pdf2zhMenu: MenuitemOptions = {
            tag: "menu",
            id: menuPrefix,
            icon: menuIcon,
            label: "zotero-pdf2zh-next",
            children: [
                {
                    tag: "menuitem",
                    id: `${menuPrefix}-translate-pdf`,
                    label: `zotero-pdf2zh-next: ${getString("prefs-menu-translate")}`,
                    commandListener: () => addon.hooks.onDialogEvents("translatePDF"),
                    icon: menuIcon,
                },
                {
                    tag: "menuitem",
                    id: `${menuPrefix}-task-manager`,
                    label: `zotero-pdf2zh-next: ${getString("prefs-menu-tasks")}`,
                    commandListener: () =>
                        addon.hooks.onDialogEvents("openTaskManager"),
                    icon: menuIcon,
                },
            ],
        };
        ztoolkit.Menu.register("item", pdf2zhMenu);
    }
}
