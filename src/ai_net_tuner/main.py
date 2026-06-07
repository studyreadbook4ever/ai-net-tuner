from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from ai_net_tuner.apply.applier import apply_proposal_file
from ai_net_tuner.collectors.sysctl_reader import read_many
from ai_net_tuner.collectors.traffic import collect_snapshot
from ai_net_tuner.docs.retriever import SysctlKnowledgeBase
from ai_net_tuner.forecasting.dataset import download_geant_dataset, geant_csv_path
from ai_net_tuner.forecasting.predict import BaselineTrafficForecaster, GeantARForecaster
from ai_net_tuner.forecasting.train_nhits import DEFAULT_MODEL_PATH, train_geant_ar_model
from ai_net_tuner.hitl.cli import ask_yes_no, render_reviewed_proposal
from ai_net_tuner.models import ReviewedProposal, utc_like_now
from ai_net_tuner.paths import state_dir
from ai_net_tuner.policy.rules import PolicyEngine
from ai_net_tuner.qwen.client import QwenProposalClient
from ai_net_tuner.storage.artifacts import write_json_artifact, write_prompt
from ai_net_tuner.storage.csv_log import append_decision, ensure_decisions_csv
from ai_net_tuner.storage.run_context import create_run_context


def _cycle_id() -> str:
    return utc_like_now().replace(":", "").replace("+", "_").replace("-", "").replace("T", "_")


def _prompt_key_limit() -> int:
    raw = os.environ.get("AI_NET_TUNER_PROMPT_KEY_LIMIT", "80")
    try:
        return max(1, int(raw))
    except ValueError:
        return 48


def _select_prompt_sysctls(policy: PolicyEngine, current_sysctls: dict[str, str]) -> dict[str, str]:
    explicit_keys = list(policy.allowlist.get("allowed", {}).keys())
    available_explicit = [
        key for key in explicit_keys
        if current_sysctls.get(key, "unknown") != "unknown"
    ]
    extra_keys = [
        key for key, value in current_sysctls.items()
        if value != "unknown" and key not in set(available_explicit)
    ]
    selected = [*available_explicit, *extra_keys][:_prompt_key_limit()]
    return {key: current_sysctls[key] for key in selected}


def run_cycle(args: argparse.Namespace) -> None:
    policy = getattr(args, "policy", None) or PolicyEngine()
    knowledge = getattr(args, "knowledge", None) or SysctlKnowledgeBase()
    forecaster = getattr(args, "forecaster", None)
    if forecaster is None:
        forecaster = GeantARForecaster() if args.forecast_model == "geant" else BaselineTrafficForecaster()
    qwen = getattr(args, "qwen", None) or QwenProposalClient(endpoint=args.qwen_endpoint, mode=args.qwen_mode)

    cycle_id = _cycle_id()
    traffic = collect_snapshot(interval_seconds=args.interval)
    if args.demo_load:
        traffic.rx_mbps = max(traffic.rx_mbps, 1200.0)
        traffic.tx_mbps = max(traffic.tx_mbps, 800.0)
        traffic.rx_pps = max(traffic.rx_pps, 18000.0)
        traffic.tx_pps = max(traffic.tx_pps, 12000.0)
        traffic.tcp_syn_recv = max(traffic.tcp_syn_recv, 64)
        traffic.tcp_time_wait = max(traffic.tcp_time_wait, 20000)
        traffic.rx_drops = max(traffic.rx_drops, 100)
        traffic.rx_drops_per_sec = max(traffic.rx_drops_per_sec, 2.0)
        traffic.softnet_drops_per_sec = max(traffic.softnet_drops_per_sec, 1.0)
        traffic.tcp_passive_opens_per_sec = max(traffic.tcp_passive_opens_per_sec, 80.0)
        traffic.source = f"{traffic.source}:demo_load"
    forecast = forecaster.predict(traffic, horizon_seconds=args.interval)
    if isinstance(forecaster, GeantARForecaster):
        forecaster.record_snapshot(traffic)

    if args.print_model_info:
        print(f"Traffic Model A: {forecast.model_name}")
        print(f"Trained/public dataset: {forecast.dataset_name}")
        print(f"Dataset source: {forecast.dataset_source}")

    allowed_keys = policy.allowed_keys()
    current_sysctls = read_many(allowed_keys)
    prompt_sysctls = _select_prompt_sysctls(policy, current_sysctls)
    docs = knowledge.retrieve_for_keys(list(prompt_sysctls))

    run_dir = getattr(args, "run_dir", None)
    decisions_csv = getattr(args, "decisions_csv", None)
    initial_sysctls = getattr(args, "initial_sysctls", None)

    prompt, proposals = qwen.propose(
        cycle_id=cycle_id,
        traffic=traffic,
        forecast=forecast,
        current_sysctls=prompt_sysctls,
        docs=docs,
        initial_sysctls={
            key: initial_sysctls.get(key, "unknown")
            for key in prompt_sysctls
        } if initial_sysctls else None,
    )
    write_prompt(f"{cycle_id}.prompt.json", prompt, base_dir=run_dir)

    if not proposals:
        print(f"[{traffic.timestamp}] proposal 없음")
        return

    max_count = policy.max_proposals_per_cycle()
    visible_proposals = 0
    for proposal in proposals[:max_count]:
        decision = policy.evaluate(proposal)
        reviewed = ReviewedProposal(
            proposal=proposal,
            policy=decision,
            traffic=traffic,
            forecast=forecast,
            sysctl_docs=knowledge.retrieve_for_keys([proposal.key]),
        )

        if decision.result == "no_change":
            append_decision(
                reviewed,
                decision="n",
                decision_source="policy_no_change",
                applied=False,
                result=decision.result,
                csv_path=decisions_csv,
            )
            continue

        artifact_path = write_json_artifact(
            f"{proposal.proposal_id}.json",
            reviewed.to_dict(),
            base_dir=run_dir,
        )

        visible_proposals += 1
        render_reviewed_proposal(reviewed, args.timeout)

        if not decision.allowed:
            append_decision(
                reviewed,
                decision="n",
                decision_source="policy_block",
                applied=False,
                result=decision.result,
                csv_path=decisions_csv,
            )
            continue

        if args.auto_decision:
            answer = args.auto_decision
            source = "auto"
            print(f"auto decision -> {answer}")
        else:
            answer, source = ask_yes_no(args.timeout)

        applied = False
        result = "skipped"
        if answer == "y":
            if args.apply:
                command = [
                    sys.executable,
                    "-m",
                    "ai_net_tuner.main",
                    "apply",
                    "--proposal-file",
                    str(artifact_path),
                    "--real",
                ]
                if os.geteuid() != 0:
                    command.insert(0, "sudo")
                completed = subprocess.run(command, check=False)
                applied = completed.returncode == 0
                result = "applied" if applied else f"apply_failed:{completed.returncode}"
            else:
                result = "accepted_dry_run"

        append_decision(
            reviewed,
            decision=answer,
            decision_source=source,
            applied=applied,
            result=result,
            csv_path=decisions_csv,
        )

    if visible_proposals == 0:
        print(f"[{traffic.timestamp}] proposal 없음")


def cmd_run(args: argparse.Namespace) -> int:
    policy = PolicyEngine()
    knowledge = SysctlKnowledgeBase()
    forecaster = GeantARForecaster() if args.forecast_model == "geant" else BaselineTrafficForecaster()
    qwen = QwenProposalClient(endpoint=args.qwen_endpoint, mode=args.qwen_mode)

    if args.qwen_mode == "local":
        qwen.prepare_local()

    initial_sysctls = read_many(policy.allowed_keys())
    run_context = create_run_context(initial_sysctls)
    ensure_decisions_csv(run_context.decisions_csv)
    args.policy = policy
    args.knowledge = knowledge
    args.forecaster = forecaster
    args.qwen = qwen
    args.run_id = run_context.run_id
    args.run_dir = run_context.run_dir
    args.decisions_csv = run_context.decisions_csv
    args.initial_sysctls = run_context.initial_sysctls

    print(f"Run ID: {run_context.run_id}")
    print(f"Run CSV: {run_context.decisions_csv}")
    print(f"Initial sysctl snapshot: {run_context.run_dir / 'initial_sysctls.json'}")

    if args.once:
        run_cycle(args)
        return 0

    while True:
        started = time.monotonic()
        run_cycle(args)
        elapsed = time.monotonic() - started
        sleep_for = max(0.0, args.interval - elapsed)
        time.sleep(sleep_for)


def cmd_apply(args: argparse.Namespace) -> int:
    return apply_proposal_file(Path(args.proposal_file), real=args.real)


def cmd_prepare_dataset(args: argparse.Namespace) -> int:
    path = download_geant_dataset(force=args.force)
    print(f"Dataset ready: {path}")
    return 0


def cmd_train_forecaster(args: argparse.Namespace) -> int:
    dataset_path = download_geant_dataset(force=args.force_download) if args.download else Path(args.dataset)
    metadata = train_geant_ar_model(
        dataset_path=dataset_path,
        model_path=Path(args.output),
        lag=args.lag,
        ridge_alpha=args.ridge_alpha,
    )
    print("Traffic Model A training complete.")
    print(f"Dataset: {metadata['dataset_name']}")
    print(f"Dataset source: {metadata['dataset_url']}")
    print(f"Samples: {metadata['samples']}")
    print(f"Test RMSE ratio: {metadata['test_rmse_ratio']:.6f}")
    return 0


def cmd_prepare_qwen(args: argparse.Namespace) -> int:
    client = QwenProposalClient(model=args.model, mode="local")
    print(f"Preparing local Qwen model: {args.model}")
    client.prepare_local()
    print("Qwen local model is ready.")
    return 0


def cmd_show_knowledge(args: argparse.Namespace) -> int:
    knowledge = SysctlKnowledgeBase()
    entries = knowledge.entries
    if args.key:
        match = knowledge.by_key(args.key)
        entries = [match] if match else []

    if not entries:
        print("no matching sysctl knowledge entry")
        return 1

    for entry in entries:
        print(f"{entry['key']} [{entry['risk']}]")
        if entry.get("keys"):
            print(f"  covers: {len(entry['keys'])} keys")
        print(f"  {entry['summary']}")
        print(f"  role: {entry['auto_tuning_role']}")
        print(f"  source: {entry['source_url']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-net-tuner")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run proposal loop")
    run.add_argument("--once", action="store_true", help="run one 3-minute cycle equivalent")
    run.add_argument("--interval", type=int, default=180, help="cycle interval and forecast horizon")
    run.add_argument("--timeout", type=int, default=150, help="HITL y/n timeout in seconds")
    run.add_argument("--apply", action="store_true", help="call sudo applier after y")
    run.add_argument("--forecast-model", choices=["geant", "baseline"], default="geant")
    run.add_argument("--qwen-endpoint", default=None, help="OpenAI-compatible local Qwen endpoint")
    run.add_argument("--qwen-mode", choices=["local", "endpoint", "offline"], default="local")
    run.add_argument("--auto-decision", choices=["y", "n"], default=None, help="skip prompt for tests")
    run.add_argument("--demo-load", action="store_true", help="inject synthetic traffic pressure for CLI review")
    run.add_argument("--print-model-info", action="store_true", help="print Model A dataset metadata each cycle")
    run.set_defaults(func=cmd_run)

    apply_cmd = sub.add_parser("apply", help="apply one reviewed proposal file")
    apply_cmd.add_argument("--proposal-file", required=True)
    apply_cmd.add_argument("--real", action="store_true", help="really write /etc/sysctl.d and run sysctl -p")
    apply_cmd.set_defaults(func=cmd_apply)

    knowledge = sub.add_parser("show-knowledge", help="print sysctl knowledge entries")
    knowledge.add_argument("--key", default=None)
    knowledge.set_defaults(func=cmd_show_knowledge)

    dataset = sub.add_parser("prepare-dataset", help="download the public GÉANT traffic dataset")
    dataset.add_argument("--force", action="store_true")
    dataset.set_defaults(func=cmd_prepare_dataset)

    train = sub.add_parser("train-forecaster", help="train public-dataset traffic forecasting Model A")
    train.add_argument("--download", action="store_true")
    train.add_argument("--force-download", action="store_true")
    train.add_argument("--dataset", default=str(geant_csv_path()))
    train.add_argument("--output", default=str(DEFAULT_MODEL_PATH))
    train.add_argument("--lag", type=int, default=10)
    train.add_argument("--ridge-alpha", type=float, default=0.05)
    train.set_defaults(func=cmd_train_forecaster)

    qwen = sub.add_parser("prepare-qwen", help="download/load Qwen3 local SLM")
    qwen.add_argument("--model", default="Qwen/Qwen3-1.7B")
    qwen.set_defaults(func=cmd_prepare_qwen)

    state = sub.add_parser("state-dir", help="print state directory")
    state.set_defaults(func=lambda _args: print(state_dir()) or 0)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
