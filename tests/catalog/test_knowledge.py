"""The knowledge base: content (Story 5.6), index (5.5) and grounded retrieval (5.7), all with the
recorded stub. SM-11 on a real embedding model is measured by `manage.py evaluate_knowledge`."""

import io
from dataclasses import replace

import pytest
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog import knowledge, selectors, services
from apps.catalog.models import KnowledgeChunk, KnowledgeDocument
from apps.kernel import runtime
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway
from apps.kernel.ai.redaction import KnownValues, redact
from apps.kernel.ai.stub import stub
from apps.kernel.errors import DomainError
from apps.listings import services as listing_services
from tests.listings.support import make_account, seed_cap, user

DOCUMENTS = knowledge.load_documents()
PASSAGES = {p.id: p for d in DOCUMENTS for p in d.passages}
QUESTIONS = yaml.safe_load(knowledge.QUESTIONS.read_text())


@pytest.fixture(autouse=True)
def clean_stub():
    stub.reset()
    yield
    runtime.shutdown()
    stub.reset()


@pytest.fixture
def indexed(db):
    services.rebuild_knowledge_index(Actor.system())
    stub.reset()


def seller():
    return user(make_account("knowledge@example.org", "individual"))


# --- content ----------------------------------------------------------------------------------


def test_at_least_100_passages_each_versioned_with_provenance():
    assert len(PASSAGES) >= 100
    assert all(d.title and d.version >= 1 and d.provenance for d in DOCUMENTS)


def test_redaction_leaves_every_passage_and_question_unchanged():
    texts = [f"{p.title}. {p.text}" for p in PASSAGES.values()]
    texts += [item["question"] for item in QUESTIONS["questions"]]
    assert [t for t in texts if redact(t, KnownValues()).value != t] == []


def test_the_lock_matches_the_content():
    message = "raise the version, then run scripts/knowledge_lock.py"
    assert knowledge.LOCK.read_text() == knowledge.lock_text(DOCUMENTS), message


def test_a_change_without_a_new_version_is_detected():
    first = DOCUMENTS[0]
    locked = {first.slug: (first.version, first.digest())}
    changed = replace(first, title="Another title")
    assert knowledge.unversioned_changes([changed], locked) == [first.slug]
    assert knowledge.unversioned_changes([replace(changed, version=99)], locked) == []


def test_forty_labelled_questions_name_real_passages():
    labelled = QUESTIONS["questions"]
    assert len(labelled) == 40
    assert {item["passage"] for item in labelled} <= set(PASSAGES)


# --- index ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_rebuild_is_reproducible():
    def snapshot():
        return list(
            KnowledgeChunk.objects.order_by("passage_id").values_list(
                "passage_id", "text", "embedding", "embedding_model"
            )
        )

    assert services.rebuild_knowledge_index(Actor.system()) == len(PASSAGES)
    first = snapshot()
    services.rebuild_knowledge_index(Actor.system())
    assert snapshot() == first
    assert KnowledgeDocument.objects.count() == len(DOCUMENTS)
    assert {row[3] for row in first} == {"stub:recorded-stub"}
    batches = -(-len(PASSAGES) // services.KNOWLEDGE_BATCH)
    assert len(stub.requests) == 2 * batches


@pytest.mark.django_db
def test_only_the_system_or_an_administrator_rebuilds():
    with pytest.raises(DomainError):
        services.rebuild_knowledge_index(seller())
    assert not stub.requests


def test_a_failed_rebuild_keeps_the_old_index(indexed):
    stub.fail_with = RuntimeError("provider down")
    with pytest.raises(CommandError, match="Index unchanged"):
        call_command("rebuild_knowledge_index")
    assert KnowledgeChunk.objects.count() == len(PASSAGES)


@pytest.mark.django_db
def test_an_exhausted_budget_stops_a_provider_rebuild(settings, monkeypatch):
    monkeypatch.setattr(stub, "local_embeddings", False)
    settings.AI_DAILY_BUDGET = 0
    with pytest.raises(services.KnowledgeIndexError) as error:
        services.rebuild_knowledge_index(Actor.system())
    assert error.value.outcome == "budget_exhausted"
    assert not KnowledgeChunk.objects.exists() and not stub.requests


# --- retrieval --------------------------------------------------------------------------------


def test_a_term_is_answered_with_source_and_version(indexed):
    result = selectors.retrieve_knowledge(seller(), "What is a marla?")
    assert result.status == "found" and result.ok and result.notice
    assert 1 <= len(result.passages) <= 3
    assert "area.marla" in [p.id for p in result.passages]
    top = result.passages[0]
    assert top.source == "Areas, units and money" and top.version >= 1 and top.provenance
    assert [p.score for p in result.passages] == sorted(
        (p.score for p in result.passages), reverse=True
    )


def test_nothing_relevant_is_not_found(indexed):
    result = selectors.retrieve_knowledge(Actor.system(), "Chicken biryani recipe?")
    assert (result.status, result.passages, result.ok) == ("not_found", (), True)
    assert result.message
    assert selectors.retrieve_knowledge(Actor.system(), "   ").status == "not_found"


def test_the_question_is_redacted_before_embedding(indexed):
    selectors.retrieve_knowledge(seller(), "What is a marla? Call 0300-000-1201")
    (sent,) = stub.requests[-1].texts
    assert "1201" not in sent and "marla" in sent


def test_a_question_over_the_cap_is_refused(indexed):
    with pytest.raises(gateway.InputTooLong):
        selectors.retrieve_knowledge(seller(), "m" * 2001)


@pytest.mark.django_db
def test_without_an_index_nothing_is_sent():
    assert selectors.retrieve_knowledge(Actor.system(), "marla").status == "unavailable"
    assert not stub.requests


def test_an_index_of_another_model_is_not_used(indexed):
    KnowledgeChunk.objects.update(embedding_model="gemini:other")
    assert selectors.retrieve_knowledge(Actor.system(), "marla").status == "unavailable"
    assert not stub.requests


def test_signed_in_retrieval_may_use_the_reserved_budget(indexed, settings, monkeypatch):
    monkeypatch.setattr(stub, "local_embeddings", False)
    settings.AI_DAILY_BUDGET, settings.AI_RESERVED_PERCENT = 2, 100
    assert selectors.retrieve_knowledge(Actor.system(), "marla").status == "unavailable"
    assert selectors.retrieve_knowledge(seller(), "marla").ok


@pytest.mark.parametrize("fault", ["provider", "database", "adapter"])
def test_a_retrieval_fault_does_not_stop_listing_creation(indexed, monkeypatch, fault):
    seed_cap()
    actor = seller()

    def broken(*args, **kwargs):
        raise RuntimeError("broken")

    if fault == "provider":
        stub.fail_with = RuntimeError("provider down")
    elif fault == "database":
        monkeypatch.setattr(selectors, "rank_knowledge", broken)
    else:
        monkeypatch.setattr(gateway, "embedding_model_key", broken)
    result = selectors.retrieve_knowledge(actor, "What is a marla?")
    assert (result.status, result.passages) == ("unavailable", ())
    listing = listing_services.create_draft(actor, "sale", "house", "house")
    updated = listing_services.apply_updates(
        actor, listing.id, listing.revision, {"title": "A house in Karachi"}
    )
    assert updated.revision == listing.revision + 1


def test_the_labelled_questions_on_the_stub_baseline(indexed):
    """Word overlap only: a floor for the ranking, not the SM-11 measurement."""
    out = io.StringIO()
    call_command("evaluate_knowledge", stdout=out)
    summary = next(line for line in out.getvalue().splitlines() if "in the top 3" in line)
    hits = int(summary.split(": ")[1].split("/")[0])
    assert hits >= 32, out.getvalue()
