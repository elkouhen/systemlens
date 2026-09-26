"""Architecture fitness rules for the SystemLens Python packages."""

from archunitpython import CheckOptions, assert_passes, project_files


_OPTIONS = CheckOptions(
    clear_cache=True,
    ignore_type_checking_imports=True,
)


def test_source_packages_have_no_import_cycles() -> None:
    rule = (
        project_files("src/")
        .should()
        .have_no_cycles()
        .because("package dependencies must remain acyclic")
    )
    assert_passes(rule, _OPTIONS)


def test_domain_does_not_depend_on_delivery_or_storage() -> None:
    rule = (
        project_files("src/")
        .in_folder("**/domain/**")
        .should_not()
        .depend_on_files()
        .in_folder("**/delivery/**")
        .because("domain models must not depend on delivery adapters")
    )
    assert_passes(rule, _OPTIONS)

    rule = (
        project_files("src/")
        .in_folder("**/domain/**")
        .should_not()
        .depend_on_files()
        .in_folder("**/storage/**")
        .because("domain models must not depend on persistence")
    )
    assert_passes(rule, _OPTIONS)


def test_scanners_do_not_depend_on_delivery() -> None:
    rule = (
        project_files("src/")
        .in_folder("**/scanner/**")
        .should_not()
        .depend_on_files()
        .in_folder("**/delivery/**")
        .because("source extractors must remain usable without delivery adapters")
    )
    assert_passes(rule, _OPTIONS)


def test_renderers_do_not_depend_on_indexing() -> None:
    rule = (
        project_files("src/")
        .in_folder("**/render/**")
        .should_not()
        .depend_on_files()
        .in_folder("**/indexing/**")
        .because("renderers must consume snapshots rather than run indexing")
    )
    assert_passes(rule, _OPTIONS)
