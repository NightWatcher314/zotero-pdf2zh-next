# Bundled runtime snapshots

`zotero-pdf2zh-next` bundles fixed, source-readable runtime snapshots so its
PyPI package does not resolve a newer `pdf2zh-next` release or install the
upstream Gradio/FastAPI web UI.

| Project | Version | Upstream | Wheel SHA256 | License |
| --- | --- | --- | --- | --- |
| pdf2zh-next | 2.8.2 | https://github.com/PDFMathTranslate-next/PDFMathTranslate-next | `5416f8e65828783df9a2323893380145d30846ed7c201539f847307b8689b770` | AGPL-3.0 |
| BabelDOC | 0.5.24 | https://github.com/funstory-ai/BabelDOC | `8810b9d8faecbe9b3f3e41f7af1f5d83cbee060ac16c9f17aa2a81abb149c6f2` | AGPL-3.0 |
| rapidocr-onnxruntime | 1.4.4 | https://github.com/RapidAI/RapidOCR | `971d7d5f223a7a808662229df1ef69893809d8457d834e6373d3854bc1782cbf` | Apache-2.0 |

The `pdf2zh-next` Gradio GUI modules and BabelDOC development tools are not
included. Translation providers, PDF processing, OCR models, table handling,
and glossary extraction remain included. Exact license texts are under
`LICENSES/` and are copied into wheel metadata.

Regenerate snapshots from the pinned public URLs and hashes:

```bash
uv run python scripts/vendor_pdf2zh_runtime.py
```
