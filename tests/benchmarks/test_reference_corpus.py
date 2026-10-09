from systemlens.application.architecture_projection import is_exportable_microservice


def test_reference_corpus_preserves_business_names_and_rejects_artifacts() -> None:
    expected = {
        "attestation-service": True,
        "contest-service": True,
        "orders-service": True,
        "test-fixture": False,
        "${SERVICE_NAME}": False,
    }

    assert {name: is_exportable_microservice(name) for name in expected} == expected
