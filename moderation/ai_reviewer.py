import json
import logging
import re
import zipfile
from pathlib import Path

import requests
from django.conf import settings

from listings.models import Project, ProjectSnapshot

from .models import AICodeReview

logger = logging.getLogger("moderation.ai_reviewer")

TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".html",
    ".css",
    ".json",
    ".sql",
    ".sh",
    ".go",
    ".rs",
    ".php",
    ".cpp",
    ".c",
    ".h",
    ".java",
    ".kt",
    ".dart",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".env.example",
    ".vue",
    ".svelte",
}

IGNORED_DIRS = {
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".next",
    "target",
    "vendor",
}

IGNORED_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock"}


def extract_code_payload(project: Project) -> str:
    """
    Extract a token-optimized representation of source code and metadata
    for AI analysis.
    """
    payload_parts = [
        "=== PROJECT METADATA ===",
        f"Title: {project.title}",
        f"Category: {project.category.name if project.category else 'N/A'}",
        f"Tech Stack: {', '.join(project.tech_stack) if project.tech_stack else 'N/A'}",
        f"Short Description: {project.description}",
        f"Long Description: {project.long_description or 'N/A'}",
        f"GitHub Repo: {project.github_repo_full_name}",
        f"License: {project.get_license_type_display()}",
        "",
        "=== SOURCE CODE SAMPLES ===",
    ]

    code_bytes_collected = 0
    max_code_bytes = 12000  # Cap payload to keep token consumption low

    # 1. Try reading from latest ProjectSnapshot zip archive
    latest_snapshot = ProjectSnapshot.objects.filter(project=project).order_by("-version").first()
    snapshot_extracted = False

    if latest_snapshot and latest_snapshot.archive and Path(latest_snapshot.archive.path).exists():
        try:
            with zipfile.ZipFile(latest_snapshot.archive.path, "r") as zf:
                file_list = zf.namelist()
                # Prioritize key files
                sorted_files = sorted(
                    file_list,
                    key=lambda f: (
                        0
                        if any(k in f.lower() for k in ["main", "app", "index", "views", "urls"])
                        else 1
                    ),
                )
                for zip_info in sorted_files:
                    if zip_info.endswith("/"):
                        continue

                    parts = zip_info.split("/")
                    filename = parts[-1]
                    ext = Path(filename).suffix.lower()

                    if any(ignored in parts for ignored in IGNORED_DIRS):
                        continue
                    if filename in IGNORED_FILES or ext not in TEXT_EXTENSIONS:
                        continue

                    try:
                        content_bytes = zf.read(zip_info)
                        content_text = content_bytes.decode("utf-8", errors="ignore").strip()
                        if not content_text:
                            continue

                        # Truncate content per file to max 80 lines
                        lines = content_text.splitlines()[:80]
                        truncated_text = "\n".join(lines)

                        payload_parts.append(f"--- File: {zip_info} ---")
                        payload_parts.append(truncated_text)
                        payload_parts.append("")

                        code_bytes_collected += len(truncated_text)
                        snapshot_extracted = True
                        if code_bytes_collected >= max_code_bytes:
                            break
                    except Exception as e:
                        logger.debug("Failed reading file %s from snapshot: %s", zip_info, e)
        except Exception as exc:
            logger.warning("Error reading snapshot for project %s: %s", project.id, exc)

    # 2. If snapshot reading didn't yield code, attempt GitHub public API file tree summary
    if not snapshot_extracted and project.github_repo_full_name:
        try:
            repo_url = f"https://api.github.com/repos/{project.github_repo_full_name}/git/trees/{project.github_default_branch}?recursive=1"
            headers = {"User-Agent": "Cooplink-AIReviewer"}
            resp = requests.get(repo_url, headers=headers, timeout=5)
            if resp.status_code == 200:
                tree_data = resp.json()
                tree_files = [
                    item["path"]
                    for item in tree_data.get("tree", [])
                    if item.get("type") == "blob"
                    and not any(ig in item["path"].split("/") for ig in IGNORED_DIRS)
                ]
                payload_parts.append("GitHub File Tree structure:")
                payload_parts.append("\n".join(tree_files[:100]))
        except Exception as exc:
            logger.debug(
                "GitHub tree API fetch failed for %s: %s",
                project.github_repo_full_name,
                exc,
            )

    return "\n".join(payload_parts)


def parse_json_from_text(text: str) -> dict | None:
    """
    Robustly extract JSON dictionary from raw response string.
    """
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    # Regex search for top-level JSON object {...}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return None


def call_alibaba_model_studio(
    messages: list, max_tokens: int = 600
) -> tuple[bool, dict | None, str | None, int, str | None]:
    """
    Call Alibaba Model Studio chat completions API with automatic model fallback queue.
    Returns: (success, parsed_json, model_name, tokens_used, error_msg)
    """
    api_key = getattr(settings, "ALIBABA_MODEL_STUDIO_API_KEY", "")
    endpoint = getattr(
        settings,
        "ALIBABA_MODEL_STUDIO_ENDPOINT",
        "https://ws-4gs8s2ba71r60d4h.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    )
    models_queue = getattr(settings, "AI_REVIEW_MODELS", ["qwen3.6-plus"])

    if not api_key:
        return False, None, None, 0, "Alibaba Model Studio API key is missing."

    url = f"{endpoint.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error = None

    for model in models_queue:
        logger.info("Attempting AI Review with Model Studio candidate model: %s", model)

        # Try response_format first, then fallback if the API rejects JSON mode
        for use_json_format in [True, False]:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": max_tokens,
            }
            if use_json_format:
                payload["response_format"] = {"type": "json_object"}

            try:
                response = requests.post(url, headers=headers, json=payload, timeout=25)

                if response.status_code == 200:
                    res_data = response.json()
                    choice = res_data.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "").strip()

                    parsed_json = parse_json_from_text(content)
                    if parsed_json is not None:
                        usage = res_data.get("usage", {})
                        tokens_used = usage.get("total_tokens", 0)

                        logger.info(
                            "Successfully received response from model %s (tokens: %d)",
                            model,
                            tokens_used,
                        )
                        return True, parsed_json, model, tokens_used, None

                elif response.status_code == 400 and use_json_format:
                    # Retry without response_format if model rejects JSON mode parameter
                    continue

                else:
                    err_text = response.text[:300]
                    logger.warning(
                        "Model %s returned status %d: %s. Trying next model...",
                        model,
                        response.status_code,
                        err_text,
                    )
                    last_error = f"HTTP {response.status_code}: {err_text}"
                    break

            except Exception as exc:
                logger.warning("Exception calling model %s: %s. Trying next model...", model, exc)
                last_error = str(exc)
                break

    return (
        False,
        None,
        None,
        0,
        f"All Model Studio fallback candidates failed. Last error: {last_error}",
    )


def run_ai_review_for_project(project_id: int, user=None) -> AICodeReview:
    """
    Execute AI security audit and description matching on a project listing.
    """
    try:
        project = Project.objects.get(pk=project_id)
    except Project.DoesNotExist as exc:
        raise ValueError(f"Project with ID {project_id} does not exist.") from exc

    code_payload = extract_code_payload(project)

    system_prompt = (
        "You are an expert security auditor and code reviewer. Analyze the project "
        "code and metadata below.\n"
        "Your objectives:\n"
        "1. Malware Inspection: Detect any malicious logic, obfuscation, backdoors, "
        "token scrapers, crypto miners, or command execution vulnerabilities.\n"
        "2. Description Match: Evaluate whether the provided code matches the title "
        "and description, returning a match_percentage (0 to 100).\n\n"
        "Output ONLY valid JSON matching this schema:\n"
        "{\n"
        '  "is_malware": boolean,\n'
        '  "malware_score": integer (0 to 100, 0=clean, 100=dangerous malware),\n'
        '  "malware_findings": ["finding 1", "finding 2"],\n'
        '  "match_percentage": integer (0 to 100),\n'
        '  "description_analysis": "short explanation of description match",\n'
        '  "summary": "overall concise assessment"\n'
        "}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": code_payload},
    ]

    success, result_json, model_used, tokens_used, error_msg = call_alibaba_model_studio(
        messages, max_tokens=600
    )

    if not success or not result_json:
        review = AICodeReview.objects.create(
            project=project,
            status=AICodeReview.Status.ERROR,
            is_malware=False,
            malware_score=0,
            match_percentage=0,
            summary=f"AI Review Failed: {error_msg}",
            model_used=model_used or "None",
            tokens_used=tokens_used,
            raw_response={"error": error_msg},
            reviewed_by=user,
        )
        return review

    is_malware = bool(result_json.get("is_malware", False))
    malware_score = int(result_json.get("malware_score", 0))
    match_percentage = int(result_json.get("match_percentage", 100))
    malware_findings = result_json.get("malware_findings", [])
    if isinstance(malware_findings, str):
        malware_findings = [malware_findings]

    description_analysis = str(result_json.get("description_analysis", ""))
    summary = str(result_json.get("summary", "Analysis completed successfully."))

    # Determine status
    if is_malware or malware_score >= 50:
        status = AICodeReview.Status.FLAGGED_MALWARE
    elif match_percentage < 60:
        status = AICodeReview.Status.DESCRIPTION_MISMATCH
    else:
        status = AICodeReview.Status.PASSED

    review = AICodeReview.objects.create(
        project=project,
        status=status,
        is_malware=is_malware,
        malware_score=malware_score,
        match_percentage=match_percentage,
        summary=summary,
        malware_findings=malware_findings,
        description_analysis=description_analysis,
        model_used=model_used,
        tokens_used=tokens_used,
        raw_response=result_json,
        reviewed_by=user,
    )

    return review
