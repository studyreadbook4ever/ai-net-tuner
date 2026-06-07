# Third Party Notices

This repository contains project code, prompt knowledge, configuration, and a small trained traffic forecasting artifact. It does not bundle Qwen GGUF weights, llama.cpp source, Python package wheels, uv caches, Hugging Face caches, or the full GÉANT dataset.

## Qwen3 1.7B

- Model: Qwen/Qwen3-1.7B
- Provider: Alibaba Qwen
- Page: https://huggingface.co/Qwen/Qwen3-1.7B
- License shown by Hugging Face: Apache-2.0

The runtime uses a GGUF quantization through llama.cpp:

- GGUF page: https://huggingface.co/bartowski/Qwen_Qwen3-1.7B-GGUF

Model weights are downloaded at runtime or through the user's local Hugging Face cache. They are not included in this repository.

## llama.cpp

- Project: ggml-org/llama.cpp
- Page: https://github.com/ggml-org/llama.cpp
- License: MIT

This repository expects an installed `llama-server`, but does not vendor llama.cpp.

## GÉANT Traffic Matrix Dataset

- Source repository: https://github.com/duchuyle108/SDN-TMprediction
- Related work: "An AI-based Traffic Matrix Prediction Solution for Software-Defined Network"
- DOI shown by the source repository: https://doi.org/10.1109/ICC42927.2021.9500331

The full dataset is not committed to this repository. The project downloads or prepares it under `datasets/`, which is ignored for submission.

## Linux Kernel Documentation

The sysctl knowledge file is based on paraphrased notes from official Linux kernel documentation:

- https://www.kernel.org/doc/html/latest/admin-guide/sysctl/net.html
- https://www.kernel.org/doc/html/latest/networking/ip-sysctl.html

The policy layer and Korean HITL text are project code/content, not copied kernel documentation.
