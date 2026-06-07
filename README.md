위 프로젝트를 진행하면서 GÉANT Backbone Network traffic matrix 공개 데이터셋으로 모델을 학습시켰고, 리눅스 공식문서로 프롬프트에 넣을 knowledge를 만들었으며 Alibaba의 Qwen3(1.7B)를 사용하였습니다.
이 코드의 목적은 트래픽을 예측하여, 리눅스 sysctl 설정값을 바꿀 힌트를 slm 기반으로 얻은 뒤에 그걸 사용자가 human in the loop로 수정하는 데에 있습니다.

# AI Net Tuner

## 제출 파일 구성

GitHub 또는 압축본에는 아래 파일과 디렉터리를 포함하면 됩니다.

```text
README.md
WHITEPAPER.md
LICENSE
THIRD_PARTY_NOTICES.md
pyproject.toml
uv.lock
start.sh
src/
config/
data/
drafts/
scripts/
models/traffic_forecaster_geant.npz
```

아래 항목은 용량이 크거나 실행 중 생성되는 파일이므로 포함하지 않습니다.

```text
.cache/
.venv/
datasets/
state/
vendor/
__pycache__/
*.pyc
```

## 1. 필수 환경

- Linux
- `sudo` 권한
- Python 3.11 이상
- `uv`
- `curl`
- `llama.cpp`의 `llama-server`
- 선택 사항: NVIDIA GPU와 정상 동작하는 CUDA driver

`uv`가 없다면 설치합니다.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

설치 후 새 터미널을 열거나 shell PATH를 다시 로드합니다.

## 2. llama.cpp 준비

이 프로젝트는 SSD 중복 사용을 피하기 위해 `llama.cpp`를 자동으로 clone/build하지 않습니다. 이미 설치된 `llama-server`를 사용합니다.

CUDA GPU를 사용할 경우 예시는 다음과 같습니다. GTX 1660 Super는 CUDA architecture `75`를 사용합니다.

```bash
cd ~
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=75 \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-15 \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-15
cmake --build build --config Release --target llama-server -j "$(nproc)"
```

CPU 전용으로 빌드하려면 CUDA를 끕니다.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF
cmake --build build --config Release --target llama-server -j "$(nproc)"
```

빌드가 끝나면 PATH에 추가합니다.

```bash
export PATH="$HOME/llama.cpp/build/bin:$PATH"
```

PATH를 수정하지 않고 실행할 수도 있습니다.

```bash
sudo env LLAMA_SERVER_BIN=$HOME/llama.cpp/build/bin/llama-server ./start.sh
```

## 3. 실행

프로젝트 디렉터리에서 실행합니다.

```bash
sudo ./start.sh
```

`start.sh`는 다음을 순서대로 수행합니다.

```text
uv sync
GÉANT public dataset 준비
traffic forecasting model 확인 또는 학습
Qwen3 1.7B Q5_K_M GGUF llama.cpp server 실행
3분마다 sysctl proposal loop 실행
```

첫 실행은 dataset과 GGUF 모델 다운로드 때문에 시간이 걸릴 수 있습니다.

## 4. 기본 모델과 실행값

기본 Qwen GGUF 모델은 다음입니다.

```text
bartowski/Qwen_Qwen3-1.7B-GGUF:Q5_K_M
```

기본 llama.cpp 옵션은 다음입니다.

```text
ctx-size: 32768
parallel: 1
batch-size: 128
ubatch-size: 128
gpu-layers: 99
GGML_CUDA_ENABLE_UNIFIED_MEMORY=1
```

CPU만 사용하려면 다음처럼 실행합니다.

```bash
sudo env AI_NET_TUNER_DEVICE=cpu ./start.sh
```

context를 줄여서 가볍게 테스트하려면:

```bash
sudo env AI_NET_TUNER_LLAMA_CTX_SIZE=8192 ./start.sh
```

한 cycle당 표시되는 proposal 수를 줄이려면:

```bash
sudo env AI_NET_TUNER_MAX_PROPOSALS_PER_CYCLE=1 ./start.sh
```

Qwen 생성량과 샘플링을 조절하려면:

```bash
sudo env AI_NET_TUNER_QWEN_MAX_TOKENS=512 AI_NET_TUNER_QWEN_TEMPERATURE=0.2 ./start.sh
```

## 5. 수동 점검 명령

dataset 준비:

```bash
uv run ai-net-tuner prepare-dataset
```

traffic forecaster 학습:

```bash
uv run ai-net-tuner train-forecaster --download
```

knowledge 확인:

```bash
uv run ai-net-tuner show-knowledge --key net.core.somaxconn
```

offline demo:

```bash
uv run ai-net-tuner run --once --demo-load --forecast-model geant --qwen-mode offline --auto-decision n
```

llama.cpp server를 수동으로 띄우는 예:

```bash
GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 llama-server \
  -hf bartowski/Qwen_Qwen3-1.7B-GGUF:Q5_K_M \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 32768 \
  --parallel 1 \
  --n-gpu-layers 99 \
  --batch-size 128 \
  --ubatch-size 128 \
  --jinja
```

endpoint 기반 1회 실행:

```bash
uv run ai-net-tuner run \
  --once \
  --demo-load \
  --forecast-model geant \
  --qwen-mode endpoint \
  --qwen-endpoint http://127.0.0.1:8080/v1/chat/completions \
  --auto-decision n
```

## 6. 적용과 로그

CLI에서 `y`를 입력한 proposal만 root 권한 applier를 통해 `/etc/sysctl.d/90-ai-net-tuner.conf`에 반영됩니다.

매 실행 로그는 아래 경로에 저장됩니다.

```text
state/runs/<run_id>/
```

주요 파일은 다음입니다.

- `decisions.csv`: proposal과 사용자 결정 기록
- `initial_sysctls.json`: 실행 시작 시점 sysctl snapshot
- `prompts/`: Qwen에 전달된 prompt
- `proposals/`: policy 검토가 끝난 proposal artifact

## 7. 압축본 만들기

교수님께 압축본을 보낼 때는 캐시와 실행 로그를 제외합니다.

```bash
cd /home/baemo_pc/260603

tar \
  --exclude='.cache' \
  --exclude='.venv' \
  --exclude='.git' \
  --exclude='.agents' \
  --exclude='.codex' \
  --exclude='datasets' \
  --exclude='state' \
  --exclude='vendor' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  -czf ../ai-net-tuner-submission.tar.gz .
```

## 8. 라이선스

이 저장소의 직접 작성 코드는 MIT License로 공개하는 것을 권장합니다. 외부 모델, 외부 데이터셋, llama.cpp는 각자의 라이선스와 이용 조건을 따릅니다. 자세한 출처는 `THIRD_PARTY_NOTICES.md`를 확인합니다.
