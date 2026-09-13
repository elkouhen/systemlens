from pathlib import Path

from systemlens.indexing.file_inventory import list_repo_files
from systemlens.infrastructure.config import Config


def test_inventory_excludes_maven_and_gradle_build_outputs(tmp_path: Path) -> None:
    (tmp_path / "service/src/main/java").mkdir(parents=True)
    (tmp_path / "service/target/classes").mkdir(parents=True)
    (tmp_path / "service/build/resources/main").mkdir(parents=True)
    (tmp_path / "service/src/main/java/App.java").write_text("class App {}")
    (tmp_path / "service/target/classes/application.yml").write_text("spring: {}")
    (tmp_path / "service/build/resources/main/application.yml").write_text("spring: {}")

    files = list_repo_files(tmp_path, Config())

    assert set(files) == {"service/src/main/java/App.java"}
