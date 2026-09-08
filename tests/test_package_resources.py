"""Installed-resource and offline-default regressions."""

from manuscriptforge.audit.journal_audit import load_journal_profiles
from manuscriptforge.config import initialize_project, load_project_config, validate_project
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.pipeline.pilot_prep import prep_pilot
from manuscriptforge.resources import config_resource


def test_bundled_resources_and_pilot_outside_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "generic_biomedical" in load_journal_profiles()
    assert config_resource("pilot_templates/abstract.template.md").is_file()
    result = prep_pilot(tmp_path / "pilot", "bioinformatics_methods")
    assert result.project_dir == tmp_path / "pilot"
    assert validate_project(result.project_dir).ok


def test_local_only_defaults_apply_without_explicit_setting(tmp_path):
    assert ProjectConfig(project_name="Example").privacy.local_only
    initialize_project(tmp_path / "project")
    assert load_project_config(tmp_path / "project").privacy.local_only
