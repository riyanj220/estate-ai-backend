"""Train the valuation model from the synthetic dataset and write its artifact and manifest.

    python manage.py train_valuation_model --model-version 2

Needs no database. A version already in the repository is never overwritten: pick the next one,
commit both files, then run `register_valuation_model`. See docs/valuation.md.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.valuation import model_store
from apps.valuation.data import training


class Command(BaseCommand):
    help = "Train the valuation model and write its artifact and manifest."
    requires_system_checks: list = []

    def add_arguments(self, parser):
        parser.add_argument("--model-version", type=int, required=True)
        parser.add_argument("--output", type=Path, default=model_store.ARTIFACTS)

    def handle(self, *args, **options):
        version, directory = options["model_version"], options["output"]
        if version < 1:
            raise CommandError("The model version starts at 1.")
        if model_store.manifest_path(version, directory).exists():
            raise CommandError(f"Version {version} exists already; use a new version.")
        try:
            manifest = training.train_and_save(version, directory)
        except training.TrainingFailed as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(manifest["metrics"], indent=2))
        size = model_store.artifact_path(version, directory).stat().st_size
        self.stdout.write(f"Model version {version} written ({size} bytes).")
