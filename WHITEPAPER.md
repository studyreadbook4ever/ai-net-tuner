# AI Net Tuner Whitepaper

## 1. 프로젝트 목적

이 프로젝트의 목적은 AI 기반 트래픽 예측을 이용해 Linux 네트워크 sysctl 설정 변경 후보를 만들고, 최종 반영은 사용자가 human in the loop 방식으로 승인하도록 하는 것입니다.

핵심 아이디어는 자동 적용이 아니라, 트래픽 예측 모델과 SLM 기반 proposal generator를 이용해 Linux 숙련자에게 sysctl 변경 힌트를 제공하는 것입니다.

## 2. 전체 흐름

```text
local traffic collector
  -> traffic forecasting Model A
  -> Qwen3 1.7B proposal Model B
  -> policy guardrail
  -> Korean CLI human-in-the-loop
  -> sudo sysctl applier
  -> CSV audit log
```

## 3. Model A: Traffic Forecasting

Model A는 공개 GÉANT Backbone Network traffic matrix dataset을 이용해 학습한 traffic ratio forecaster입니다.

```text
Dataset: GÉANT Backbone Network traffic matrix dataset
Source: https://github.com/duchuyle108/SDN-TMprediction
Original family: GÉANT/Abilene traffic matrix traces
Model name: geant-ridge-ar-ratio
```

현재 모델은 다음 구간의 전체 traffic volume이 현재 대비 얼마나 변할지 `forecast_ratio`를 예측합니다. 이후 이 ratio를 현재 로컬 host에서 수집한 네트워크 지표에 적용해 Qwen에게 전달할 pressure summary를 만듭니다.

예측 결과는 다음 계열로 정리됩니다.

- throughput: RX/TX Mbps, RX/TX packet rate
- connection churn: active/passive opens, TIME_WAIT, SYN_RECV
- listen backlog pressure: ListenOverflows, ListenDrops
- loss and retransmission: TCP retransmission, RX drops, softnet drops
- UDP pressure: UDP errors, receive buffer errors
- socket pressure: socket/TCP/UDP 사용량

이 모델은 모든 네트워크 지표를 독립적으로 예측하는 모델이라기보다는, 공개 traffic matrix 기반으로 미래 traffic pressure를 추정하고 로컬 관측값에 결합하는 모델입니다.

## 4. Model B: Qwen3 1.7B Sysctl Advisor

Model B는 Alibaba Qwen3 1.7B를 기반으로 한 sysctl proposal generator입니다. 실행은 llama.cpp의 OpenAI-compatible endpoint를 통해 이루어집니다.

기본 모델은 다음 GGUF quantization을 사용합니다.

```text
bartowski/Qwen_Qwen3-1.7B-GGUF:Q5_K_M
```

Qwen은 영어 JSON만 출력하도록 설계했습니다. 사용자에게 보이는 한글 안내문과 경고문은 Qwen이 아니라 agent policy 계층에서 생성합니다.

Qwen prompt에는 다음 정보가 들어갑니다.

- traffic model metadata
- pressure summary
- 실행 시작 시점 sysctl snapshot
- 현재 sysctl 값
- Linux 공식문서 기반 sysctl knowledge
- traffic signal to sysctl candidate decision guide
- 출력 JSON schema

## 5. Sysctl Knowledge

`data/sysctl_knowledge_en.json`은 Linux 공식문서 기반으로 만든 영어 knowledge pack입니다.

주요 출처는 다음입니다.

- https://www.kernel.org/doc/html/latest/admin-guide/sysctl/net.html
- https://www.kernel.org/doc/html/latest/networking/ip-sysctl.html

knowledge entry는 대략 다음 정보를 포함합니다.

- sysctl key
- scope
- risk
- auto tuning role
- summary
- when it may help
- tradeoffs
- traffic signals
- source URL

이 파일은 allowlist가 아닙니다. Qwen이 sysctl tradeoff를 이해하도록 돕는 grounding context이고, 실제 허용 여부는 policy layer가 결정합니다.

## 6. Policy Guardrail

`config/policy_allowlist.json`은 agent policy 계층의 핵심 설정입니다.

policy layer는 다음을 검사합니다.

- 제안 key가 허용 가능한 network sysctl인지
- blocked key/prefix/glob에 걸리는지
- 현재값을 읽을 수 있는지
- proposed value가 type/range/enum/shape guardrail을 통과하는지
- proposed value가 현재값과 동일하지 않은지

대표 value type은 다음과 같습니다.

- `int`
- `enum`
- `port_range`
- `int_triplet`
- `same_shape`

Qwen이 공격적이거나 이상한 proposal을 내더라도, policy layer가 값 형식과 허용 범위를 다시 검사합니다.

## 7. Human In The Loop

CLI human-in-the-loop 계층은 사용자가 최종적으로 `y` 또는 `n`을 입력하도록 강제합니다.

주요 특징은 다음입니다.

- proposal마다 현재값과 제안값 표시
- 기대효과와 근거 표시
- 한글 경고문 표시
- timeout 시 자동 `n`
- timeout은 로컬 시간 기준 만료 시각으로 표시
- 사용자 결정은 CSV에 기록

예시는 다음 형태입니다.

```text
sysctl proposal  05:08:18  timeout=150s
net.core.netdev_max_backlog

현재  1000
제안  2000

효과  수신대기 완화 예상
근거  수신폭주 예측됨
주의  메모리 사용 증가 가능
주의  CPU 병목이면 효과 제한
주의  허용값: 0 이상 2147483647 이하 정수

적용할까요? [y/n] (05:10:48에 timeout됩니다):
```

## 8. Sudo Applier

실제 sysctl 변경은 Qwen이 직접 하지 않습니다. 승인된 proposal만 별도 applier가 root 권한으로 처리합니다.

applier는 다음 절차를 따릅니다.

```text
proposal artifact 로드
현재 sysctl 값 재확인
policy guardrail 재검사
rollback 정보 저장
/etc/sysctl.d/90-ai-net-tuner.conf 작성
sysctl -p /etc/sysctl.d/90-ai-net-tuner.conf 실행
```

이 구조는 SLM proposal 계층과 실제 시스템 변경 계층을 분리하기 위한 것입니다.

## 9. Logging

매 실행은 `state/runs/<run_id>/` 아래에 묶입니다.

주요 로그는 다음입니다.

- `decisions.csv`: proposal, policy result, 사용자 결정, 적용 여부
- `initial_sysctls.json`: 실행 시작 시점 sysctl snapshot
- `prompts/`: Qwen에 전달된 prompt 원문
- `proposals/`: policy 검토가 끝난 proposal artifact

CSV 로그를 통해 proposal이 수락됐는지, 거절됐는지, timeout으로 `n` 처리됐는지 추적할 수 있습니다.

## 10. 라이선스와 공개 범위

GitHub 공개 저장소에는 직접 작성한 코드와 작은 학습 결과 artifact만 포함하는 것을 권장합니다.

포함 권장:

```text
src/
config/
data/
drafts/
scripts/
models/traffic_forecaster_geant.npz
README.md
WHITEPAPER.md
pyproject.toml
uv.lock
start.sh
```

제외 권장:

```text
.cache/
.venv/
datasets/
state/
vendor/
Qwen GGUF weight files
```

직접 작성한 코드는 MIT License로 공개하고, 외부 모델과 외부 데이터셋은 각자의 라이선스 및 이용 조건을 따르도록 분리합니다.
