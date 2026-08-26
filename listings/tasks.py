import logging
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

from celery import shared_task
from django.core.files import File

from accounts.utils import decrypt_token

log = logging.getLogger(__name__)


def _force_rmtree(path: Path):
    """Remove a directory tree — handles Windows read-only files (e.g. git .pack)."""
    if not path.exists():
        return
    for root, _dirs, files in os.walk(path, topdown=False):
        root = Path(root)
        for name in files:
            (root / name).chmod(stat.S_IWRITE)
        for name in _dirs:
            (root / name).chmod(stat.S_IWRITE)
    shutil.rmtree(path, ignore_errors=True)


def build_project_snapshot(project_id):
    """
    Clone the project's GitHub repo, zip it, and save as a ProjectSnapshot.
    Runs synchronously — does NOT use Celery.
    Returns the ProjectSnapshot instance on success, raises on failure.
    """
    from .models import Project, ProjectSnapshot

    project = Project.objects.get(pk=project_id)
    token = decrypt_token(project.seller.github_token_encrypted)
    repo_url = f"https://x-access-token:{token}@github.com/{project.github_repo_full_name}.git"

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"cooplink-snapshot-{project_id}-"))
    repo_path = tmp_dir / "repo"

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(repo_path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )

        git_dir = repo_path / ".git"
        if git_dir.is_dir():
            _force_rmtree(git_dir)

        archive_path = tmp_dir / "archive.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(repo_path):
                root = Path(root)
                for fn in files:
                    fpath = root / fn
                    arcname = fpath.relative_to(repo_path)
                    zf.write(fpath, arcname)

        file_size = archive_path.stat().st_size

        latest_version = (
            ProjectSnapshot.objects.filter(project=project)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
        )
        next_version = (latest_version or 0) + 1

        with archive_path.open("rb") as f:
            snapshot = ProjectSnapshot(
                project=project,
                version=next_version,
                file_size=file_size,
            )
            snapshot.archive.save(
                f"snapshots/{project_id}/v{next_version}.zip",
                File(f),
                save=True,
            )

        log.info(
            "Snapshot v%s created for project %s (%s) — %s bytes",
            next_version,
            project_id,
            project.github_repo_full_name,
            file_size,
        )
        return snapshot

    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Git clone failed for {project.github_repo_full_name}: "
            f"stdout={exc.stdout} stderr={exc.stderr}"
        ) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def create_project_snapshot(self, project_id):
    """
    Celery task wrapper around build_project_snapshot.
    """
    from .models import Project

    try:
        Project.objects.get(pk=project_id)
    except Project.DoesNotExist:
        log.warning(
            "create_project_snapshot: Project %s not found — attempt %s/%s",
            project_id,
            self.request.retries + 1,
            self.max_retries + 1,
        )
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=10) from None
        log.error(
            "create_project_snapshot: Project %s not found after %s attempts — giving up.",
            project_id,
            self.max_retries + 1,
        )
        return

    try:
        build_project_snapshot(project_id)
    except subprocess.CalledProcessError as exc:
        log.error(
            "Git clone failed for project %s: stdout=%s stderr=%s",
            project_id,
            exc.stdout,
            exc.stderr,
        )
        raise self.retry(exc=exc, countdown=30) from None
    except Exception as exc:
        log.error("Snapshot creation failed for project %s: %s", project_id, exc)
        raise
