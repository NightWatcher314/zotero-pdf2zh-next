import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import vm from "node:vm";
import { URL } from "node:url";
import ts from "typescript";

function loadModule(file, mocks = {}, globals = {}) {
    const source = fs.readFileSync(
        new URL(`../src/modules/${file}`, import.meta.url),
        "utf8",
    );
    const { outputText } = ts.transpileModule(source, {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2022,
        },
    });
    const exports = {};
    vm.runInNewContext(outputText, {
        exports,
        require: (name) => {
            if (!(name in mocks)) throw Error(`Unexpected import: ${name}`);
            return mocks[name];
        },
        ...globals,
    });
    return exports;
}
const manager = loadModule("llmApiManager.ts");
const defaults = {
    qps: "10",
    poolSize: "0",
    retryCount: "-1",
    retryInterval: "2",
};
const plain = (value) => JSON.parse(JSON.stringify(value));

test("old profiles inherit defaults; individual zero overrides survive persistence", () => {
    assert.deepEqual(plain(manager.resolveRuntimeSettings(defaults)), defaults);
    const profile = manager.createLLMApiFromFormData({
        runtime: { poolSize: 6, retryCount: 0, retryInterval: 0 },
    });
    const restored = plain(profile);
    assert.deepEqual(
        plain(manager.resolveRuntimeSettings(defaults, restored.runtime)),
        {
            qps: "10",
            poolSize: "6",
            retryCount: "0",
            retryInterval: "0",
        },
    );
});

test("switching active profiles changes submitted task settings without changing defaults", () => {
    const a = {
        key: "a",
        service: "openai",
        activate: true,
        runtime: { qps: 2, poolSize: 4 },
    };
    const b = {
        key: "b",
        service: "openai",
        activate: false,
        runtime: { qps: 20, poolSize: 30 },
    };
    const addon = {
        data: {
            llmApis: {
                map: new Map([
                    ["a", a],
                    ["b", b],
                ]),
            },
        },
    };
    const { PDF2zhHelperFactory } = loadModule(
        "pdf2zhHelper.ts",
        {
            "../utils/prefs": {},
            "./preferenceScript": { loadLLMApisFromPrefs() {} },
            "./llmApiManager": manager,
        },
        { addon },
    );
    const config = { ...defaults, service: "openai" };
    const submit = () =>
        PDF2zhHelperFactory.buildTaskRequestBody(
            { fileName: "x.pdf", base64: "" },
            config,
        );
    assert.equal(submit().qps, "2");
    assert.equal(submit().poolSize, "4");
    a.activate = false;
    b.activate = true;
    assert.equal(submit().qps, "20");
    assert.equal(submit().poolSize, "30");
    assert.equal(config.poolSize, "0");
    b.runtime = {};
    assert.equal(submit().qps, "10");
});
