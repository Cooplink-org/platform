import logging

from django.core.management.base import BaseCommand, CommandError

from listings.models import Project

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Create source-code snapshots for projects (synchronous, bypasses Celery)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--project-id",
            type=int,
            default=None,
            help="Only create snapshot for this project ID",
        )

    def handle(self, *_args, **options):
        from listings.tasks import build_project_snapshot

        project_id = options["project_id"]

        if project_id:
            projects = Project.objects.filter(pk=project_id)
            if not projects.exists():
                raise CommandError(f"Project with id {project_id} not found.")
        else:
            projects = Project.objects.filter(status=Project.Status.PUBLISHED)

        total = projects.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No projects to process."))
            return

        self.stdout.write(f"Processing {total} project(s)...")

        for i, project in enumerate(projects, 1):
            self.stdout.write(
                f"[{i}/{total}] Creating snapshot for {project.title} (id={project.id})..."
            )
            try:
                build_project_snapshot(project.id)
                self.stdout.write(self.style.SUCCESS("  ✓ Snapshot created"))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ Failed: {exc}"))

        self.stdout.write(self.style.SUCCESS("Done."))
