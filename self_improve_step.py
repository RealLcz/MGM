# This file is adapted from https://github.com/jennyzzt/dgm.

import argparse
import datetime
import json
import os
import random
import re
import subprocess

import docker

from llm import create_client, extract_json_between_markers, get_response_from_llm

from prompts.self_improvement_prompt import (
    get_diagnose_prompt_polyglot,
    get_diagnose_prompt_polyglot_shared_task_two_commits,
    get_diagnose_prompt_polyglot_two_entries,
    get_diagnose_prompt_swe,
    get_diagnose_prompt_swe_shared_task_two_commits,
    get_diagnose_prompt_swe_two_entries,
    get_problem_description_prompt,
)

from utils.docker_utils import safe_log

dataset = None
diagnose_llm = ""
self_improve_llm = ""
timeout = 3600
n_evals = 0


def _log_final_diagnose_response(label, response):
    safe_log(f"{label}: final diagnose attempt failed; raw LLM response follows.")
    if response is None:
        safe_log(f"{label}: raw LLM response is <None>.")
        return
    safe_log(f"{label}: raw LLM response start")
    safe_log(str(response))
    safe_log(f"{label}: raw LLM response end")


def diagnose_problem(
    entry, commit, root_dir, out_dir, patch_files=[], max_attempts=2, polyglot=False
):
    client = create_client(diagnose_llm)
    if polyglot:
        diagnose_sys_message, diagnose_prompt = get_diagnose_prompt_polyglot(
            entry,
            commit,
            root_dir,
            out_dir,
            dataset,
            patch_files=patch_files,
        )
    else:
        diagnose_sys_message, diagnose_prompt = get_diagnose_prompt_swe(
            entry,
            commit,
            root_dir,
            out_dir,
            dataset,
            patch_files=patch_files,
        )
    response = None
    try:
        try:
            response, msg_history = get_response_from_llm(
                msg=diagnose_prompt,
                client=client[0],
                model=client[1],
                system_message=diagnose_sys_message,
                print_debug=False,
                msg_history=None,
            )
        except Exception as e:
            safe_log(
                f"Error with get_response_from_llm: {e}"
            )
        # safe_log(f"Message history: {msg_history}")
        response_json = extract_json_between_markers(
            response,
            required_keys={"implementation_suggestion", "problem_description"},
        )
        assert response_json, "empty response json"
        problem_statement = get_problem_description_prompt(response_json, polyglot)
    except Exception as e:
        # Exception most probably due to not having json in the response
        safe_log(f"Error while diagnosing the problem: {e}")
        if max_attempts > 0:
            return diagnose_problem(
                entry,
                commit,
                root_dir,
                out_dir,
                patch_files=patch_files,
                max_attempts=max_attempts - 1,
                polyglot=polyglot,
            )
        else:
            _log_final_diagnose_response("diagnose_problem", response)
            return None
    return problem_statement


def diagnose_two_entries_same_commit(
    entries,
    commit,
    root_dir,
    out_dir,
    patch_files=None,
    max_attempts=2,
    polyglot=False,
):
    if patch_files is None:
        patch_files = []
    if len(entries) != 2:
        raise ValueError("entries must contain exactly two entry ids")
    client = create_client(diagnose_llm)
    if polyglot:
        diagnose_sys_message, diagnose_prompt = get_diagnose_prompt_polyglot_two_entries(
            entries,
            commit,
            root_dir,
            out_dir,
            dataset,
            patch_files=patch_files,
        )
    else:
        diagnose_sys_message, diagnose_prompt = get_diagnose_prompt_swe_two_entries(
            entries,
            commit,
            root_dir,
            out_dir,
            dataset,
            patch_files=patch_files,
        )
    response = None
    try:
        try:
            response, msg_history = get_response_from_llm(
                msg=diagnose_prompt,
                client=client[0],
                model=client[1],
                system_message=diagnose_sys_message,
                print_debug=False,
                msg_history=None,
            )
        except Exception as e:
            safe_log(f"Error with get_response_from_llm (joint two entries): {e}")
        response_json = extract_json_between_markers(
            response,
            required_keys={"implementation_suggestion", "problem_description"},
        )
        assert response_json, "empty response json"
        problem_statement = get_problem_description_prompt(response_json, polyglot)
    except Exception as e:
        safe_log(f"Error while diagnosing (joint two entries): {e}")
        if max_attempts > 0:
            return diagnose_two_entries_same_commit(
                entries,
                commit,
                root_dir,
                out_dir,
                patch_files=patch_files,
                max_attempts=max_attempts - 1,
                polyglot=polyglot,
            )
        _log_final_diagnose_response("diagnose_two_entries_same_commit", response)
        return None
    return problem_statement


def diagnose_shared_task_two_commits(
    task_id,
    commit_primary,
    commit_context,
    root_dir,
    out_dir,
    patch_files=None,
    max_attempts=2,
    polyglot=False,
    primary_label="Primary",
    context_label="Context",
    strategy_c_meta=None,
):
    if patch_files is None:
        patch_files = []
    client = create_client(diagnose_llm)
    if polyglot:
        diagnose_sys_message, diagnose_prompt = (
            get_diagnose_prompt_polyglot_shared_task_two_commits(
                task_id,
                commit_primary,
                commit_context,
                root_dir,
                out_dir,
                dataset,
                patch_files=patch_files,
                primary_label=primary_label,
                context_label=context_label,
                strategy_c_meta=strategy_c_meta,
            )
        )
    else:
        diagnose_sys_message, diagnose_prompt = (
            get_diagnose_prompt_swe_shared_task_two_commits(
                task_id,
                commit_primary,
                commit_context,
                root_dir,
                out_dir,
                dataset,
                patch_files=patch_files,
                primary_label=primary_label,
                context_label=context_label,
                strategy_c_meta=strategy_c_meta,
            )
        )
    response = None
    try:
        try:
            response, msg_history = get_response_from_llm(
                msg=diagnose_prompt,
                client=client[0],
                model=client[1],
                system_message=diagnose_sys_message,
                print_debug=False,
                msg_history=None,
            )
        except Exception as e:
            safe_log(
                f"Error with get_response_from_llm (shared task two commits): {e}"
            )
        response_json = extract_json_between_markers(
            response,
            required_keys={"implementation_suggestion", "problem_description"},
        )
        assert response_json, "empty response json"
        problem_statement = get_problem_description_prompt(response_json, polyglot)
    except Exception as e:
        safe_log(f"Error while diagnosing (shared task two commits): {e}")
        if max_attempts > 0:
            return diagnose_shared_task_two_commits(
                task_id,
                commit_primary,
                commit_context,
                root_dir,
                out_dir,
                patch_files=patch_files,
                max_attempts=max_attempts - 1,
                polyglot=polyglot,
                primary_label=primary_label,
                context_label=context_label,
                strategy_c_meta=strategy_c_meta,
            )
        _log_final_diagnose_response("diagnose_shared_task_two_commits", response)
        return None
    return problem_statement


def save_metadata(metadata, output_dir):
    metadata_file = os.path.join(output_dir, "metadata.json")
    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=4)
