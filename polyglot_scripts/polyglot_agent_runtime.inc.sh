# shellcheck shell=bash
# Build a host agent venv for Polyglot eval (DeepSeek API or vLLM).
# Non-Python task containers have no Python; Apptainer auto-mounts $HOME into containers.
#
# Source after REPO_ROOT and PYTHON_BIN are set:
#   . "${REPO_ROOT}/polyglot_scripts/polyglot_agent_runtime.inc.sh"
#   polyglot_agent_runtime_setup

polyglot_agent_runtime_setup() {
    local agent_src="${POLYGLOT_INIT_AGENT_SRC:-${EVAL_INIT_AGENT_SRC:-initial_polyglot/default_agent/src}}"
    local constraints="${POLYGLOT_AGENT_PIP_CONSTRAINTS:-SWEbench_Multilingual/agent_constraints.txt}"
    local index_url="${POLYGLOT_AGENT_PIP_INDEX_URL:-https://mirrors.cloud.tencent.com/pypi/simple}"
    local agent_rt="${POLYGLOT_AGENT_RUNTIME_DIR:-${HOME}/.cache/polyglot_agent_runtime}"

    mkdir -p "${agent_rt}"
    case "${agent_rt}" in
        "${HOME}"/*) : ;;
        *)
            echo "WARNING: agent runtime ${agent_rt} is not under \$HOME; it may not be visible inside task containers." >&2
            ;;
    esac

    local agent_venv_py="${agent_rt}/venv/bin/python"
    local agent_rt_check='import anthropic, openai, backoff, botocore, boto3, pathspec, bs4, pydantic, git, yaml, unidiff, rich, dotenv'

    exec 9>"${agent_rt}/.build.lock"
    flock 9
    if ! "${agent_venv_py}" -c "${agent_rt_check}" >/dev/null 2>&1; then
        echo "Building Polyglot host agent runtime venv at ${agent_rt}/venv ..."
        rm -rf "${agent_rt}/venv"
        "${PYTHON_BIN}" -m venv --copies "${agent_rt}/venv"
        "${agent_venv_py}" -m pip install --upgrade pip >/dev/null 2>&1 || true
        "${agent_venv_py}" -m pip install \
            --index-url "${index_url}" \
            --prefer-binary --disable-pip-version-check --no-input \
            -r "${REPO_ROOT}/${agent_src}/requirements.txt" \
            -c "${REPO_ROOT}/${constraints}" \
            > "${agent_rt}/build_install.log" 2>&1
        "${agent_venv_py}" -c "${agent_rt_check}" && echo "Polyglot agent runtime venv ready"
    fi
    flock -u 9 || true
    exec 9>&-

    export POLYGLOT_HOST_AGENT_PYTHON="${agent_venv_py}"
    export SWE_ML_HOST_AGENT_PYTHON="${agent_venv_py}"
    echo "Host agent python: ${POLYGLOT_HOST_AGENT_PYTHON}"
}
