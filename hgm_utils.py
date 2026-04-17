# This file is adapted from https://github.com/jennyzzt/dgm.

import argparse
import datetime
import json
import os
import random
from collections import Counter
import re
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import stdev

import docker
import numpy as np

import self_improve_step
from polyglot.harness import harness as polyglot_harness
from prompts.self_improvement_prompt import find_selfimprove_eval_logs
from prompts.testrepo_prompt import get_test_description
from self_improve_step import (
    diagnose_problem,
    diagnose_shared_task_two_commits,
    diagnose_two_entries_same_commit,
    save_metadata,
)
from swe_bench.harness import harness as swe_harness
from swe_bench.report import make_report
from utils.common_utils import load_json_file
from utils.docker_utils import (build_hgm_container, cleanup_container,
                                copy_from_container, copy_to_container,
                                log_container_output,
                                remove_existing_container, safe_log,
                                setup_logger)
from utils.eval_utils import get_acc_on_tasks
from utils.evo_utils import (get_all_performance, get_model_patch_paths,
                             is_compiled_self_improve)

dataset = None
alpha = 0.5
K = 0.5
bias_factor = 5
nodes = {}
total_tasks = []
output_dir = ""
polyglot = False
n_task_evals = 0
init_evaluated_tasks = []
llm = ""
timeout = 3600

pending_tasks_lock = threading.Lock()
n_task_evals_lock = threading.Lock()

# Counts completed self-improve runs by strategy (A/B/C); thread-safe.
self_improve_strategy_usage = Counter()
self_improve_strategy_usage_lock = threading.Lock()

# Global pool of task IDs that any agent has ever failed.
# Used to bias future task selection toward previously-failed tasks,
# increasing the overlap of attempted tasks across agents and making
# Strategy C more likely to find eligible peer pairs.
failed_pool = set()
failed_pool_lock = threading.Lock()


def get_self_improve_strategy_usage():
    with self_improve_strategy_usage_lock:
        return {
            "A": int(self_improve_strategy_usage["A"]),
            "B": int(self_improve_strategy_usage["B"]),
            "C": int(self_improve_strategy_usage["C"]),
        }


def init(_polyglot, _output_dir, _tasks, _n_task_evals=0, _llm="", _timeout=3600):
    global output_dir, total_tasks, polyglot, n_task_evals, llm, timeout
    with self_improve_strategy_usage_lock:
        self_improve_strategy_usage.clear()
    with failed_pool_lock:
        failed_pool.clear()
    output_dir = _output_dir
    timeout = _timeout
    seen = set()
    total_tasks = []
    for item in _tasks:
        if item not in seen:
            seen.add(item)
            total_tasks.append(item)
    polyglot = _polyglot
    n_task_evals = _n_task_evals
    llm = _llm


def any_exceeding_context_length(output_dir, commit_id, instance_ids):
    """
    Check if any of the issues have exceeded the context length.
    """
    for instance_id in instance_ids:
        md_logs, _, _, _ = find_selfimprove_eval_logs(
            instance_id, output_dir, commit_id, filter=False
        )
        error_strs = [
            r"Error in get_response_withtools: Error code: 400 - {'message': 'Input is too long for requested model.'}",
            r"Error in get_response_withtools: Error code: 400 - {'object': 'error', 'message': \"This model's maximum context length is \d+ tokens. However, you requested \d+ tokens in the messages, Please reduce the length of the messages. None\", 'type': 'BadRequestError', 'param': None, 'code': 400}",
            r"Error in get_response_withtools: Error code: 400 - {'error': {'message': 'Your input exceeds the context window of this model. Please adjust your input and try again.', 'type': 'invalid_request_error', 'param': 'input', 'code': 'context_length_exceeded'}}",
        ]
        for md_log in md_logs:
            if any(
                re.search(f"{error_str}\n{error_str}", md_log)
                for error_str in error_strs
            ):
                return True
    return False


def failed_task_ids(overall_performance):
    """
    Instance IDs treated as failed (unresolved or empty patch), consistent with
    get_acc_on_tasks using total_resolved_ids for success.
    Used e.g. for strategy C shared-failure intersection.
    """
    if not overall_performance:
        return frozenset()
    unresolved = overall_performance.get("total_unresolved_ids") or []
    empty_patch = overall_performance.get("total_emptypatch_ids") or []
    return frozenset(unresolved) | frozenset(empty_patch)


def submitted_task_ids(overall_performance):
    """All instance IDs that were attempted (submitted) by this commit."""
    if not overall_performance:
        return frozenset()
    return frozenset(overall_performance.get("total_submitted_ids") or [])


def resolved_task_ids(overall_performance):
    """Instance IDs that were successfully resolved by this commit."""
    if not overall_performance:
        return frozenset()
    return frozenset(overall_performance.get("total_resolved_ids") or [])


def update_failed_pool(new_failed_ids):
    """Add newly observed failed task IDs to the global failed_pool."""
    if not new_failed_ids:
        return
    with failed_pool_lock:
        failed_pool.update(new_failed_ids)


def choose_entry(parent_commit, debug=False):
    """
    Choose entry for self-improvement given a parent commit.
    """
    try:
        metadata_path = os.path.join(output_dir, parent_commit, "metadata.json")
        metadata = load_json_file(metadata_path)
        metadata = {
            "accuracy_score": metadata["overall_performance"]["accuracy_score"],
            "total_unresolved_ids": metadata["overall_performance"][
                "total_unresolved_ids"
            ],
            "total_emptypatch_ids": metadata["overall_performance"][
                "total_emptypatch_ids"
            ],
            "total_resolved_ids": metadata["overall_performance"]["total_resolved_ids"],
            "children_count": 0,
        }
    except Exception as e:
        # probably because swe-eval failed, generated code did not compile, etc.
        raise RuntimeError(f"{parent_commit} not eligible for being a parent: {e}")
    if debug:
        safe_log(metadata)

    empty_ids = metadata["total_emptypatch_ids"]
    resolved_ids = metadata["total_resolved_ids"]
    unresolved_ids = metadata["total_unresolved_ids"]

    entry = None

    if polyglot:
        entry_ids = empty_ids + unresolved_ids
        if not entry_ids:
            entry_ids = resolved_ids + empty_ids + unresolved_ids
        entry = random.choice(entry_ids)
    else:
        num_total_ids = len(empty_ids) + len(resolved_ids) + len(unresolved_ids)

        if len(empty_ids) >= 0.1 * num_total_ids and random.random() < 0.25:
            entry = "solve_empty_patches"

        elif random.random() < 0.25:
            entry = "solve_stochasticity"

        elif (
            any_exceeding_context_length(
                output_dir, parent_commit, empty_ids + unresolved_ids
            )
            and random.random() < 0.25
        ):
            entry = "solve_contextlength"

        elif len(unresolved_ids) != 0:
            entry_ids = unresolved_ids
            entry = random.choice(entry_ids)

        else:
            entry = random.choice(resolved_ids + empty_ids + unresolved_ids)
    if entry is None:
        safe_log(metadata)
        raise RuntimeError(
            f"Failed to choose an entry for self-improvement based on {parent_commit}."
        )
    return entry


def choose_two_entries(parent_commit, debug=False):
    """
    Pick two distinct instance IDs for joint self-improve (strategy B).
    Mirrors choose_entry's instance-id pools; never returns special SWE entries
    (solve_empty_patches, etc.). Raises RuntimeError if fewer than two distinct
    IDs exist — caller should fall back to strategy A.
    """
    try:
        metadata_path = os.path.join(output_dir, parent_commit, "metadata.json")
        metadata = load_json_file(metadata_path)
        perf = metadata["overall_performance"]
        metadata = {
            "accuracy_score": perf["accuracy_score"],
            "total_unresolved_ids": perf["total_unresolved_ids"],
            "total_emptypatch_ids": perf["total_emptypatch_ids"],
            "total_resolved_ids": perf["total_resolved_ids"],
            "children_count": 0,
        }
    except Exception as e:
        raise RuntimeError(f"{parent_commit} not eligible for being a parent: {e}")
    if debug:
        safe_log(metadata)

    empty_ids = metadata["total_emptypatch_ids"]
    resolved_ids = metadata["total_resolved_ids"]
    unresolved_ids = metadata["total_unresolved_ids"]

    if polyglot:
        entry_ids = empty_ids + unresolved_ids
        if not entry_ids:
            entry_ids = resolved_ids + empty_ids + unresolved_ids
        pool = list(dict.fromkeys(entry_ids))
    else:
        pool = list(
            dict.fromkeys(unresolved_ids + empty_ids + resolved_ids)
        )

    if len(pool) < 2:
        raise RuntimeError(
            f"choose_two_entries: need at least two distinct instance ids for "
            f"{parent_commit}, got {len(pool)}"
        )
    return random.sample(pool, 2)


def eval_agent(
    commit_id,
    tasks=None,
    num_tasks=5,
    max_workers=20,
    pending_tasks=None,
    random_level=0.5,
    skip=True,
    init_agent_path="./",
):
    if commit_id == "failed":
        return [0] * num_tasks
    global n_task_evals, total_tasks
    metadata_path = os.path.join(output_dir, commit_id, "metadata.json")
    if not os.path.exists(metadata_path):
        metadata = {
                "run_id": commit_id,
                "overall_performance": {
                    "accuracy_score": 0,
                    "total_resolved_instances": 0,
                    "total_submitted_instances": 0,
                    "files": [],
                    "total_unresolved_ids": [],
                    "total_emptypatch_ids": [],
                    "total_resolved_ids": [],
                    "total_submitted_ids": []
                }
            }
        save_metadata(metadata, os.path.join(output_dir, commit_id))
    metadata = load_json_file(metadata_path)
    if tasks is None:
        if commit_id == "initial":
            if len(set(init_evaluated_tasks)) >= len(set(total_tasks)):
                return [metadata["overall_performance"]["accuracy_score"]] * num_tasks
            else:
                if skip:
                    un_evalatued_tasks = [
                        task for task in total_tasks if task not in init_evaluated_tasks
                    ]
                else:
                    un_evalatued_tasks = total_tasks
                order = "random" if random.random() < random_level else "fixed"
                if order == "random":
                    tasks = random.sample(
                        un_evalatued_tasks, min(num_tasks, len(un_evalatued_tasks))
                    )
                else:
                    tasks = un_evalatued_tasks[:num_tasks]
                init_evaluated_tasks.extend(tasks)
                return get_acc_on_tasks(tasks, os.path.join(output_dir, commit_id))
        if pending_tasks is None:
            pending_tasks = []

        with pending_tasks_lock:
            if skip:
                submitted_and_pending = (
                    metadata["overall_performance"]["total_submitted_ids"]
                    + pending_tasks
                )
                un_evalatued_tasks = [
                    task for task in total_tasks if task not in submitted_and_pending
                ]
            else:
                un_evalatued_tasks = total_tasks

            order = "random" if random.random() < random_level else "fixed"
            if len(un_evalatued_tasks) > 0:
                if order == "random":
                    tasks = random.sample(
                        un_evalatued_tasks, min(num_tasks, len(un_evalatued_tasks))
                    )
                else:
                    tasks = un_evalatued_tasks[:num_tasks]
                num_tasks = len(tasks)
            else:
                return [metadata["overall_performance"]["accuracy_score"]] * num_tasks
            pending_tasks.extend(tasks)

    root_dir = os.path.abspath("./")
    metadata = load_json_file(
        os.path.join(root_dir, output_dir, commit_id, "metadata.json")
    )
    prev_submitted_ids = set(
        metadata.get("overall_performance", {}).get("total_submitted_ids", [])
    )

    if polyglot:
        polyglot_harness(
            test_task_list=tasks,
            max_workers=min(max_workers, len(tasks)),
            model_name_or_path=commit_id,
            model_patch_paths=get_model_patch_paths(root_dir, output_dir, commit_id),
            pred_dname=os.path.join(root_dir, output_dir, commit_id, "predictions"),
            output_dir=os.path.join(root_dir, output_dir, commit_id),
            init_agent_path=init_agent_path,
        )
        overall_performance = get_all_performance(
            commit_id, results_dir=os.path.join(output_dir, commit_id)
        )[1]
    else:
        dnames = swe_harness(
            test_task_list=tasks,
            max_workers=min(max_workers, len(tasks)),
            model_name_or_path=commit_id,
            model_patch_paths=get_model_patch_paths(root_dir, output_dir, commit_id),
            pred_dname=os.path.join(root_dir, output_dir, commit_id, "predictions"),
            init_agent_path=init_agent_path,
        )
        safe_log("Start make_report")
        make_report(
            dnames,
            run_ids=[f"{commit_id}_{i}" for i in range(len(dnames))],
            output_dir=os.path.join(output_dir, commit_id),
            num_eval_procs=min(max_workers, len(tasks)),
        )
        safe_log("Start get_performance")

        _, overall_performance = get_all_performance(
            commit_id, results_dir=os.path.join(output_dir, commit_id)
        )
        safe_log("End of evaluation")
        metadata["swe_dnames"] = [str(dn) for dn in dnames]

    metadata["overall_performance"] = overall_performance
    update_failed_pool(failed_task_ids(overall_performance))
    save_metadata(metadata, os.path.join(root_dir, output_dir, commit_id))

    new_submitted_ids = set(
        overall_performance.get("total_submitted_ids", [])
    )
    actually_submitted = [t for t in tasks if t in new_submitted_ids - prev_submitted_ids]

    with n_task_evals_lock:
        n_task_evals += len(actually_submitted)

    return get_acc_on_tasks(actually_submitted, os.path.join(root_dir, output_dir, commit_id))


def sample_child(
    parent_commit,
    image_name,
    force_rebuild=False,
    max_try=1,
    self_improve_strategy="A",
    entries=None,
    peer_commit=None,
    shared_task=None,
    strategy_c_meta=None,
    record_strategy_usage=True,
):
    metadata = {}
    root_dir = os.path.abspath("./")  # root_dir should be /hgm
    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_dir_base = output_dir  # out_dir_base should be /hgm/output_selfimprove/ or /hgm/output_hgm/{hgm_run_id}/
    run_output_dir = os.path.join(root_dir, f"{output_dir}/{run_id}/")

    try:
        if parent_commit == "failed":
            return "failed"

        client = docker.from_env()
        client.ping()

        os.makedirs(run_output_dir, exist_ok=True)

        if polyglot:
            with open("polyglot/polyglot_benchmark_metadata.json") as f:
                dataset = json.loads(f.read())
        else:
            from datasets import load_dataset

            dataset = load_dataset("princeton-nlp/SWE-bench_Verified")
            dataset = dataset["test"]
        self_improve_step.dataset = dataset

        setup_logger(os.path.join(run_output_dir, "self_improve.log"))
        metadata["run_id"] = run_id
        metadata["parent_commit"] = parent_commit

        container_name = f"hgm-container-{run_id}"
        remove_existing_container(client, container_name)
        container = build_hgm_container(
            client,
            root_dir,
            image_name,
            container_name,
            force_rebuild=force_rebuild,
        )
        container.start()
        if polyglot:
            exec_result = container.exec_run("rm /hgm/coding_agent.py", workdir="/")
            log_container_output(exec_result)
            exec_result = container.exec_run(
                "mv /hgm/coding_agent_polyglot.py /hgm/coding_agent.py", workdir="/"
            )
            log_container_output(exec_result)
            exec_result = container.exec_run("rm /hgm/utils/eval_utils.py", workdir="/")
            log_container_output(exec_result)
            exec_result = container.exec_run(
                "rm /hgm/utils/swe_log_parsers.py", workdir="/"
            )
            log_container_output(exec_result)
        else:
            exec_result = container.exec_run(
                "rm /hgm/coding_agent_polyglot.py", workdir="/"
            )

        patch_files = get_model_patch_paths(root_dir, output_dir, parent_commit)
        for patch_file in patch_files:
            copy_to_container(container, patch_file, "/hgm/parent_patch.txt")
            exec_result = container.exec_run(
                "/bin/sh -c 'patch -p1 < /hgm/parent_patch.txt'", workdir="/hgm"
            )
            log_container_output(exec_result)
            exec_result = container.exec_run("rm /hgm/parent_patch.txt", workdir="/hgm")
            log_container_output(exec_result)

        git_setup_cmd = (
            "printf '__pycache__/\\n*.pyc\\n*.backup\\n' > .gitignore && "
            "git init && "
            "git add --all && "
            "git -c user.name='user' -c user.email='you@example.com' "
            "commit -m 'a nonsense commit message' && "
            "git rev-parse HEAD"
        )
        exec_result = container.exec_run(
            ["/bin/sh", "-c", git_setup_cmd], workdir="/hgm/"
        )
        log_container_output(exec_result)
        git_output = exec_result.output.decode("utf-8").strip()
        if not git_output:
            raise RuntimeError(
                "git setup returned empty output — container /hgm/ may be "
                "empty or have no tracked files"
            )
        commit_hash = git_output.strip().split("\n")[-1].strip()

        exec_result = container.exec_run(
            "python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "
            "--timeout 120 -r /hgm/requirements.txt",
            workdir="/",
        )
        log_container_output(exec_result, raise_error=False)

        safe_log("Getting tasks to improve")
        strategy = (self_improve_strategy or "A").strip().upper()
        entry = None

        if strategy == "B" and entries is not None and len(entries) == 2:
            metadata["self_improve_strategy"] = "B"
            metadata["entries"] = list(entries)
            safe_log(f"Strategy B: joint diagnose for entries {entries}")
            problem_statement = diagnose_two_entries_same_commit(
                entries,
                parent_commit,
                root_dir,
                out_dir_base,
                patch_files=patch_files,
                polyglot=polyglot,
            )
        elif strategy == "C" and peer_commit and shared_task:
            metadata["self_improve_strategy"] = "C"
            metadata["peer_commit"] = peer_commit
            metadata["shared_task"] = shared_task
            safe_log(
                f"Strategy C: shared_task={shared_task} primary={parent_commit} "
                f"context={peer_commit} meta={strategy_c_meta}"
            )
            problem_statement = diagnose_shared_task_two_commits(
                shared_task,
                parent_commit,
                peer_commit,
                root_dir,
                out_dir_base,
                patch_files=patch_files,
                polyglot=polyglot,
                primary_label=f"primary({parent_commit})",
                context_label=f"context({peer_commit})",
                strategy_c_meta=strategy_c_meta,
            )
        else:
            if strategy not in ("A", "B", "C"):
                safe_log(
                    f"Unknown self_improve_strategy {self_improve_strategy!r}; using A"
                )
            elif strategy == "B":
                safe_log("Strategy B requested but entries invalid; using A")
            elif strategy == "C":
                safe_log(
                    "Strategy C requested but peer_commit or shared_task missing; using A"
                )
            metadata["self_improve_strategy"] = "A"
            try:
                entry = choose_entry(parent_commit)
            except Exception as e:
                safe_log(f"Error choosing entry: {e}")
            try:
                safe_log(f"Task to improve: {entry}")
            except Exception as e:
                choose_entry(parent_commit, debug=True)
                raise e
            problem_statement = diagnose_problem(
                entry,
                parent_commit,
                root_dir,
                out_dir_base,
                patch_files=patch_files,
                polyglot=polyglot,
            )

        safe_log(f"problem_statement: {problem_statement}")

        if metadata.get("self_improve_strategy") == "A":
            metadata["entry"] = entry
        elif metadata.get("self_improve_strategy") == "B":
            metadata["entry"] = None
        else:
            metadata["entry"] = shared_task
        metadata["problem_statement"] = problem_statement
        if not problem_statement:
            safe_log("Failed to diagnose the problem statement. Exiting.")
            cleanup_container(container)
            save_metadata(metadata, run_output_dir)
            if max_try > 1:
                return sample_child(
                    parent_commit,
                    image_name,
                    force_rebuild=force_rebuild,
                    max_try=max_try - 1,
                    self_improve_strategy=self_improve_strategy,
                    entries=entries,
                    peer_commit=peer_commit,
                    shared_task=shared_task,
                    strategy_c_meta=strategy_c_meta,
                    record_strategy_usage=record_strategy_usage,
                )
            else:
                return "failed"

        safe_log("Running self-improvement")
        chat_history_file_container = "/hgm/self_evo.md"
        test_description = get_test_description(swerepo=False)
        env_vars = {
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),
            "AWS_REGION": os.getenv("AWS_REGION"),
            "AWS_REGION_NAME": os.getenv("AWS_REGION_NAME"),
            "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
            "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            "OpenRouter_API_KEY": os.getenv("OpenRouter_API_KEY"),
            "VLLM_HOST": "127.0.0.1",
            "VLLM_PORT": os.getenv("REMOTE_VLLM_PORT", os.getenv("VLLM_PORT", "8000")),
        }
        cmd = [
            "timeout",
            str(timeout),
            "python",
            "/hgm/coding_agent.py",
            "--problem_statement",
            problem_statement,
            "--git_dir",
            "/hgm/",
            "--chat_history_file",
            chat_history_file_container,
            "--base_commit",
            commit_hash,
            "--outdir",
            "/hgm/",
            "--test_description",
            test_description,
            "--self_improve",
            "--model",
            llm,
            "--timeout",
            str(timeout),
        ]
        exec_result = container.exec_run(cmd, environment=env_vars, workdir="/")
        log_container_output(exec_result, raise_error=False)

        chat_history_file = os.path.join(output_dir, run_id, "self_evo.md")
        copy_from_container(container, chat_history_file_container, chat_history_file)
        model_patch_file = os.path.join(output_dir, run_id, "model_patch.diff")
        copy_from_container(container, "/hgm/model_patch.diff", model_patch_file)

        metadata["overall_performance"] = {
            "accuracy_score": 0.0,
            "total_resolved_instances": 0,
            "total_submitted_instances": 0,
            "files": [],
            "total_submitted_ids": [],
            "total_unresolved_ids": [],
            "total_emptypatch_ids": [],
            "total_resolved_ids": [],
        }
        if not os.path.exists(model_patch_file):
            raise Exception("Model patch file is empty or does not exist")
        with open(model_patch_file, "r") as f:
            patch_content = f.read()
            if not patch_content.strip():
                raise Exception("Model patch file is empty")

        if record_strategy_usage:
            key = metadata.get("self_improve_strategy", "A")
            if key not in ("A", "B", "C"):
                key = "A"
            with self_improve_strategy_usage_lock:
                self_improve_strategy_usage[key] += 1

    except Exception as e:
        if max_try > 1:
            safe_log(f"Error while sampling a child: {str(e)}. Retrying...")
            safe_log(traceback.format_exc())
            return sample_child(
                parent_commit,
                image_name,
                force_rebuild=force_rebuild,
                max_try=max_try - 1,
                self_improve_strategy=self_improve_strategy,
                entries=entries,
                peer_commit=peer_commit,
                shared_task=shared_task,
                strategy_c_meta=strategy_c_meta,
                record_strategy_usage=record_strategy_usage,
            )
        else:
            safe_log(f"Error while sampling a child: {str(e)}")
            safe_log(traceback.format_exc())
            return "failed"
    finally:
        try:
            cleanup_container(container)
        except Exception as e:
            safe_log(f"Error during container cleanup: {e}")
        if os.path.isdir(run_output_dir):
            save_metadata(metadata, run_output_dir)
    return run_id