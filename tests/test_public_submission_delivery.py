"""Synthetic delivery checkpoints; no database or transport is opened."""
from __future__ import annotations

import copy

import pytest

from public_submissions import Receipt


class MemoryReceipt(Receipt):
    def __init__(self):
        self.row = {"checkpoints": {}, "status": "received"}
        self.lose_acceptance_checkpoint = False

    def checkpoint(self, name, state, result=None, error=None):
        if state == "succeeded" and self.lose_acceptance_checkpoint:
            self.lose_acceptance_checkpoint = False
            raise RuntimeError("synthetic acceptance checkpoint unavailable")
        self.row["checkpoints"][name] = {"state": state, "result": copy.deepcopy(result), "error_type": error}


def invoke(receipt, operation, *, preflight):
    receipt.direct_once("delivery", operation, preflight=preflight)


@pytest.mark.parametrize("configuration", ["missing_smtp", "invalid_destination"])
def test_definite_preflight_failure_is_retryable_without_transport(receipt_factory, configuration):
    receipt, deliveries = receipt_factory
    ready = False
    invoke(receipt, lambda: deliveries.append(configuration) or True, preflight=lambda: ready)
    assert deliveries == []
    assert receipt.row["checkpoints"]["delivery"]["state"] == "failed"
    assert receipt.row["checkpoints"]["delivery"]["result"]["before_transport"] is True
    ready = True
    invoke(receipt, lambda: deliveries.append(configuration) or True, preflight=lambda: ready)
    assert deliveries == [configuration]
    assert receipt.row["checkpoints"]["delivery"]["state"] == "succeeded"


@pytest.fixture
def receipt_factory():
    return MemoryReceipt(), []


def test_successful_preflight_and_delivery_are_not_repeated(receipt_factory):
    receipt, deliveries = receipt_factory
    invoke(receipt, lambda: deliveries.append("synthetic accepted") or True, preflight=lambda: True)
    assert receipt.row["checkpoints"]["delivery"]["state"] == "succeeded"
    invoke(receipt, lambda: pytest.fail("successful delivery replayed"), preflight=lambda: pytest.fail("successful preflight replayed"))
    assert deliveries == ["synthetic accepted"]


def test_false_after_transport_is_indeterminate_and_never_retried(receipt_factory):
    receipt, deliveries = receipt_factory
    invoke(receipt, lambda: deliveries.append("synthetic attempted") or False, preflight=lambda: True)
    assert receipt.row["checkpoints"]["delivery"]["state"] == "indeterminate"
    invoke(receipt, lambda: pytest.fail("ambiguous delivery replayed"), preflight=lambda: pytest.fail("ambiguous preflight replayed"))
    assert deliveries == ["synthetic attempted"]


def test_transport_exception_is_indeterminate_and_never_retried(receipt_factory):
    receipt, deliveries = receipt_factory

    def send():
        deliveries.append("synthetic attempted")
        raise TimeoutError("synthetic lost provider response")

    invoke(receipt, send, preflight=lambda: True)
    assert receipt.row["checkpoints"]["delivery"]["state"] == "indeterminate"
    invoke(receipt, lambda: pytest.fail("ambiguous delivery replayed"), preflight=lambda: True)
    assert len(deliveries) == 1


def test_lost_checkpoint_after_transport_leaves_sending_then_indeterminate(receipt_factory):
    receipt, deliveries = receipt_factory
    receipt.lose_acceptance_checkpoint = True
    with pytest.raises(RuntimeError, match="synthetic acceptance checkpoint"):
        invoke(receipt, lambda: deliveries.append("synthetic accepted") or True, preflight=lambda: True)
    assert receipt.row["checkpoints"]["delivery"]["state"] == "sending"
    invoke(receipt, lambda: pytest.fail("delivery with lost checkpoint replayed"), preflight=lambda: True)
    assert receipt.row["checkpoints"]["delivery"]["state"] == "indeterminate"
    assert len(deliveries) == 1


def test_preflight_exception_is_definite_and_retryable(receipt_factory):
    receipt, deliveries = receipt_factory

    def unavailable():
        raise ValueError("synthetic destination unavailable")

    invoke(receipt, lambda: pytest.fail("transport ran before successful preflight"), preflight=unavailable)
    assert receipt.row["checkpoints"]["delivery"]["state"] == "failed"
    assert receipt.row["checkpoints"]["delivery"]["error_type"] == "ValueError"
    invoke(receipt, lambda: deliveries.append("synthetic accepted") or True, preflight=lambda: True)
    assert deliveries == ["synthetic accepted"]


def test_legacy_direct_once_without_preflight_remains_supported(receipt_factory):
    receipt, deliveries = receipt_factory
    receipt.direct_once("delivery", lambda: deliveries.append("legacy accepted") or True)
    assert deliveries == ["legacy accepted"]
    assert receipt.row["checkpoints"]["delivery"]["state"] == "succeeded"
