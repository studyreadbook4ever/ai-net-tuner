from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import gc
from typing import Any

from ai_net_tuner.models import Forecast, Proposal, TrafficSnapshot, utc_like_now
from ai_net_tuner.qwen.prompt_builder import build_qwen_prompt


class QwenProposalClient:
    def __init__(
        self,
        endpoint: str | None = None,
        model: str = "Qwen/Qwen3-1.7B",
        mode: str = "local",
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.mode = mode
        self._local = None
        self._local_device: str | None = None

    def propose(
        self,
        *,
        cycle_id: str,
        traffic: TrafficSnapshot,
        forecast: Forecast,
        current_sysctls: dict[str, str],
        docs: list[dict[str, Any]],
        initial_sysctls: dict[str, str] | None = None,
    ) -> tuple[str, list[Proposal]]:
        prompt = build_qwen_prompt(traffic, forecast, current_sysctls, docs, initial_sysctls)
        if self.mode == "offline":
            proposals = self._offline_stub(cycle_id, traffic, forecast, current_sysctls)
            return prompt, self._attach_authoritative_current_values(proposals, current_sysctls)

        if self.mode == "local":
            payload = self._local_qwen_payload(prompt)
            proposals = self._parse_proposals(cycle_id, payload)
            return prompt, self._attach_authoritative_current_values(proposals, current_sysctls)

        if self.mode == "endpoint" and not self.endpoint:
            raise RuntimeError("--qwen-mode endpoint requires --qwen-endpoint")

        temperature = float(os.environ.get("AI_NET_TUNER_QWEN_TEMPERATURE", "0.30"))
        top_p = float(os.environ.get("AI_NET_TUNER_QWEN_TOP_P", "0.85"))
        max_tokens = int(os.environ.get("AI_NET_TUNER_QWEN_MAX_TOKENS", "640"))
        request_body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a bounded Linux network sysctl advisor for a human-in-the-loop coursework demo. "
                            "Return English JSON only. Do not write Korean. "
                            "Do not include chain-of-thought. Prefer 1 to 2 plausible proposals when supplied signals can map to supplied sysctl docs. "
                            "Use moderate value changes and avoid extreme jumps. The policy layer and human operator will reject poor proposals. /no_think"
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_tokens,
                "stream": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = float(os.environ.get("AI_NET_TUNER_QWEN_HTTP_TIMEOUT_SECONDS", "240"))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Qwen endpoint HTTP {exc.code}: {body[:2000]}"
            ) from exc

        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"]
        payload = self._extract_json_payload(content)
        proposals = self._parse_proposals(cycle_id, payload)
        return prompt, self._attach_authoritative_current_values(proposals, current_sysctls)

    def _load_local(self, force_device: str | None = None) -> tuple[Any, Any, Any]:
        if self._local is not None and (force_device is None or self._local_device == force_device):
            return self._local
        if self._local is not None:
            self._clear_local_model()

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Local Qwen mode requires the slm dependencies. "
                "Install with: uv sync --extra slm"
            ) from exc

        tokenizer = self._from_pretrained_with_cache_preference(
            AutoTokenizer,
            self.model,
            trust_remote_code=True,
        )
        device_request = force_device or os.environ.get("AI_NET_TUNER_DEVICE", "auto").strip().lower()
        device = force_device or self._select_torch_device(torch)
        dtype = torch.float16 if device == "cuda" else torch.float32
        try:
            model = self._from_pretrained_with_cache_preference(
                AutoModelForCausalLM,
                self.model,
                dtype=dtype,
                device_map="auto" if device == "cuda" else None,
                trust_remote_code=True,
            )
        except Exception as exc:
            if device == "cuda" and device_request == "auto" and self._is_cuda_failure(exc):
                print(f"Qwen CUDA load failed; retrying on CPU: {exc}", flush=True)
                return self._load_local(force_device="cpu")
            raise
        if device == "cpu":
            model = model.to(device)
        model.eval()
        self._local = (tokenizer, model, torch)
        self._local_device = device
        print(f"Qwen local device: {device}", flush=True)
        return self._local

    def _select_torch_device(self, torch: Any) -> str:
        requested = os.environ.get("AI_NET_TUNER_DEVICE", "auto").strip().lower()
        if requested == "cpu":
            return "cpu"
        if requested == "cuda":
            if not self._cuda_smoke_test(torch):
                raise RuntimeError("AI_NET_TUNER_DEVICE=cuda was requested, but CUDA is unavailable.")
            return "cuda"
        if requested == "auto":
            return "cuda" if self._cuda_smoke_test(torch) else "cpu"
        raise RuntimeError("AI_NET_TUNER_DEVICE must be one of: cpu, cuda, auto")

    def _cuda_smoke_test(self, torch: Any) -> bool:
        try:
            if not torch.cuda.is_available():
                return False
            probe = torch.empty((1,), device="cuda")
            del probe
            torch.cuda.synchronize()
            return True
        except Exception as exc:
            print(f"Qwen CUDA probe failed; using CPU: {exc}", flush=True)
            return False

    def _is_cuda_failure(self, exc: BaseException) -> bool:
        message = f"{type(exc).__name__}: {exc}".lower()
        markers = (
            "cuda",
            "nvml",
            "cudacachingallocator",
            "driverapi",
            "cublas",
            "cudnn",
            "out of memory",
        )
        return any(marker in message for marker in markers)

    def _clear_local_model(self) -> None:
        local = self._local
        self._local = None
        previous_device = self._local_device
        self._local_device = None
        if local is not None:
            _tokenizer, _model, torch = local
            del _model
            gc.collect()
            if previous_device == "cuda":
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass

    def _from_pretrained_with_cache_preference(self, loader: Any, model: str, **kwargs: Any) -> Any:
        try:
            return loader.from_pretrained(model, local_files_only=True, **kwargs)
        except Exception:
            return loader.from_pretrained(model, **kwargs)

    def prepare_local(self) -> None:
        self._load_local()

    def _local_qwen_payload(self, prompt: str) -> dict[str, Any]:
        try:
            return self._local_qwen_payload_once(prompt)
        except Exception as exc:
            if self._local_device == "cuda" and self._is_cuda_failure(exc):
                print(f"Qwen CUDA generation failed; retrying on CPU: {exc}", flush=True)
                self._clear_local_model()
                self._load_local(force_device="cpu")
                return self._local_qwen_payload_once(prompt)
            raise

    def _local_qwen_payload_once(self, prompt: str) -> dict[str, Any]:
        tokenizer, model, torch = self._load_local()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a bounded Linux network sysctl advisor for a human-in-the-loop coursework demo. "
                    "Return English JSON only. Do not write Korean. "
                    "Prefer 1 to 2 plausible proposals when supplied signals can map to supplied sysctl docs. "
                    "Use moderate value changes and avoid extreme jumps. The policy layer and human operator will reject poor proposals."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        if hasattr(tokenizer, "apply_chat_template"):
            text = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        else:
            text = messages[0]["content"] + "\n\n" + messages[1]["content"] + "\n\nJSON:"

        inputs = tokenizer(text, return_tensors="pt")
        device = next(model.parameters()).device
        inputs = {key: value.to(device) for key, value in inputs.items()}
        temperature = float(os.environ.get("AI_NET_TUNER_QWEN_TEMPERATURE", "0.30"))
        top_p = float(os.environ.get("AI_NET_TUNER_QWEN_TOP_P", "0.85"))
        max_tokens = int(os.environ.get("AI_NET_TUNER_QWEN_MAX_TOKENS", "640"))
        with torch.no_grad():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=temperature > 0,
                temperature=temperature if temperature > 0 else None,
                top_p=top_p if temperature > 0 else None,
                pad_token_id=tokenizer.eos_token_id,
            )
        generated = output_ids[0][inputs["input_ids"].shape[-1] :]
        content = tokenizer.decode(generated, skip_special_tokens=True)
        return self._extract_json_payload(content)

    def _extract_json_payload(self, content: str) -> dict[str, Any]:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return {"proposals": []}
            try:
                payload = json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                return {"proposals": []}

        if not isinstance(payload, dict) or "proposals" not in payload:
            return {"proposals": []}
        if not isinstance(payload["proposals"], list):
            return {"proposals": []}
        return payload

    def _offline_stub(
        self,
        cycle_id: str,
        traffic: TrafficSnapshot,
        forecast: Forecast,
        current_sysctls: dict[str, str],
    ) -> list[Proposal]:
        created_at = utc_like_now()
        proposal: dict[str, str] | None = None

        def readable(key: str) -> bool:
            return current_sysctls.get(key, "unknown") != "unknown"

        def next_int(key: str, *, floor: int, cap: int, factor: float = 2.0) -> str | None:
            if not readable(key):
                return None
            try:
                current = int(current_sysctls.get(key, "0"))
            except ValueError:
                current = 0
            proposed = max(floor, int(current * factor))
            return str(min(proposed, cap))

        def maybe_int_proposal(
            key: str,
            *,
            floor: int,
            cap: int,
            reason_en: str,
            expected_effect_en: str,
        ) -> dict[str, str] | None:
            proposed = next_int(key, floor=floor, cap=cap)
            if proposed is None or proposed.strip() == current_sysctls.get(key, "").strip():
                return None
            return {
                "key": key,
                "proposed": proposed,
                "reason_en": reason_en,
                "expected_effect_en": expected_effect_en,
                "risk_level": "medium",
            }

        if (
            forecast.pred_listen_overflows_per_sec > 0
            or forecast.pred_listen_drops_per_sec > 0
            or forecast.pred_tcp_syn_recv >= max(32, traffic.tcp_syn_recv * 1.15)
        ):
            proposal = maybe_int_proposal(
                "net.core.somaxconn",
                floor=4096,
                cap=65535,
                reason_en="Predicted inbound connection bursts may pressure the accept backlog.",
                expected_effect_en="May reduce pending connection queue pressure.",
            )
        if proposal is None and (
            forecast.pred_rx_drops_per_sec > 0 or forecast.pred_softnet_drops_per_sec > 0
        ):
            proposal = maybe_int_proposal(
                "net.core.netdev_max_backlog",
                floor=10000,
                cap=250000,
                reason_en="Predicted receive-side packet drops may indicate network device backlog pressure.",
                expected_effect_en="May improve short-burst receive queue absorption.",
            )
        if (
            proposal is None
            and readable("net.ipv4.ip_local_port_range")
            and forecast.pred_tcp_time_wait >= max(10000, traffic.tcp_time_wait * 1.10)
        ):
            proposal = {
                "key": "net.ipv4.ip_local_port_range",
                "proposed": "10240 65535",
                "reason_en": "Predicted connection churn may increase ephemeral port pressure.",
                "expected_effect_en": "May expand the outbound ephemeral port pool.",
                "risk_level": "medium",
            }
        if proposal is None and (forecast.pred_udp_rcvbuf_errors_per_sec > 0 or forecast.pred_rx_mbps > 1000):
            proposal = maybe_int_proposal(
                "net.core.rmem_max",
                floor=16777216,
                cap=134217728,
                reason_en="Predicted receive throughput is high enough to justify receive buffer headroom.",
                expected_effect_en="May improve receive-side buffer headroom.",
            )

        if proposal is None:
            return []

        key = proposal["key"]
        return [
            Proposal(
                proposal_id=f"{cycle_id}-p001",
                cycle_id=cycle_id,
                created_at=created_at,
                key=key,
                current=current_sysctls.get(key, "unknown"),
                proposed=proposal["proposed"],
                reason_en=proposal["reason_en"],
                expected_effect_en=proposal["expected_effect_en"],
                risk_level=proposal["risk_level"],
                raw={"offline_stub": True},
            )
        ]

    def _parse_proposals(self, cycle_id: str, payload: dict[str, Any]) -> list[Proposal]:
        created_at = utc_like_now()
        proposals = []
        for index, raw in enumerate(payload.get("proposals", []), start=1):
            proposals.append(
                Proposal(
                    proposal_id=f"{cycle_id}-p{index:03d}",
                    cycle_id=cycle_id,
                    created_at=created_at,
                    key=str(raw["key"]),
                    current=str(raw.get("current", "unknown")),
                    proposed=str(raw["proposed"]),
                    reason_en=str(raw.get("reason_en", "")),
                    expected_effect_en=str(raw.get("expected_effect_en", "")),
                    risk_level=str(raw.get("risk_level", "medium")),
                    source_model=self.model,
                    raw=raw,
                )
            )
        return proposals

    def _attach_authoritative_current_values(
        self,
        proposals: list[Proposal],
        current_sysctls: dict[str, str],
    ) -> list[Proposal]:
        for proposal in proposals:
            actual = current_sysctls.get(proposal.key)
            if actual is None:
                continue
            if proposal.current != actual:
                proposal.raw["model_reported_current"] = proposal.current
                proposal.current = actual
        return proposals
