# DeepSeek Harness Python

> **Status: under development.** This repository is not ready for production use. APIs, package names, runtime behavior, and installation instructions may change without notice.

Python SDKs and runtime packaging for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness), the plugin-based agent harness developed by DeepSeek AI.

`deepseek-harness-python` will provide a native Python interface for starting and driving a DeepSeek Harness agent. The SDK will communicate with a bundled Harness runtime through newline-delimited JSON-RPC over standard input/output, keeping the Python process boundary explicit and portable.

## Planned packages

| Distribution | Import module | Purpose |
| --- | --- | --- |
| `deepseek-harness-sdk` | `deepseek_harness` | High-level turn API and low-level JSON-RPC client |
| `deepseek-harness-runtime-bin` | `deepseek_harness_runtime` | Platform runtime binaries and default agent configuration |

The SDK is planned to start the matching bundled runtime by default. Advanced users will be able to select a runtime channel and supply a custom Cordis configuration.

## Intended usage

The API below is illustrative and may change before the first release.

```python
from deepseek_harness import DeepSeekHarness

with DeepSeekHarness() as harness:
    result = harness.run("Say hi.")
    print(result.final_response)
```

The default runtime configuration will use the usual DeepSeek Harness environment variables, including `DEEPSEEK_API_KEY` and `DEEPSEEK_BASE_URL`.

## Roadmap

- [ ] Establish the Python package layout and public API.
- [ ] Implement the JSON-RPC stdio client and session lifecycle.
- [ ] Package per-platform runtime binaries.
- [ ] Support zero-configuration startup and custom Cordis configurations.
- [ ] Publish release artifacts and installation documentation.

## Relationship to DeepSeek Harness

This project follows the architecture and Python SDK direction of the upstream [DeepSeek Harness repository](https://github.com/deepseek-ai/deepseek-harness). It is being developed as a dedicated Python-focused repository; it does not currently replace the Python work in the upstream monorepo.

## Contributing

Contribution guidance will be added as the initial implementation takes shape. Until then, please use the upstream repository for DeepSeek Harness product issues and discussion.

## License

The intended license is MIT. The license file will be added before the first release.
