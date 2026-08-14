"""Quota discipline, tested as behaviour rather than trusted as a habit.

The rule these tests protect: a run that would exceed its ceiling stops *before* the
request goes out, because a call refused after the fact has already been charged.
"""
import inspect

import httpx
import pytest

from conftest import FakeSearchApi, RecordingSleeper

from propertyfinder.adapters import ZillowAdapter
from propertyfinder.adapters.zillow import PER_CALL_DELAY_S
from propertyfinder.budget import BudgetExceeded, CallBudget
from propertyfinder.config import Settings


# -- the object -----------------------------------------------------------------------


def test_a_budget_counts_down_as_it_is_spent():
    budget = CallBudget(max_calls=3)
    budget.spend()
    budget.spend()
    assert budget.spent == 2 and budget.remaining == 1


def test_a_budget_refuses_the_call_that_would_break_it():
    budget = CallBudget(max_calls=1, label="sweep walsh-aledo")
    budget.spend()
    with pytest.raises(BudgetExceeded) as exc:
        budget.spend()
    assert "sweep walsh-aledo" in str(exc.value)
    assert budget.spent == 1  # the refusal charged nothing


def test_checking_does_not_spend():
    budget = CallBudget(max_calls=2)
    budget.check(2)
    assert budget.spent == 0 and budget.remaining == 2


def test_a_multi_call_batch_is_refused_whole_rather_than_part_spent():
    budget = CallBudget(max_calls=10, spent=8)
    with pytest.raises(BudgetExceeded):
        budget.spend(3)
    assert budget.spent == 8


def test_remaining_never_goes_negative_and_the_ceiling_cannot():
    assert CallBudget(max_calls=0).remaining == 0
    with pytest.raises(ValueError):
        CallBudget(max_calls=-1)


# -- the adapter enforcing it ---------------------------------------------------------


def test_the_third_call_of_a_two_call_budget_never_leaves_the_house(make_adapter):
    transport = FakeSearchApi()
    budget = CallBudget(max_calls=2)
    adapter = make_adapter(transport, budget=budget)

    adapter.search_page("Aledo, TX 76008", "for_sale", 1)
    adapter.search_page("Aledo, TX 76008", "for_sale", 2)
    with pytest.raises(BudgetExceeded):
        adapter.search_page("Aledo, TX 76008", "sold", 1)

    assert adapter.request_count == 2
    assert len(transport.requests) == 2  # the provider never heard the third


def test_a_paging_sweep_stops_at_the_ceiling_instead_of_walking_past_it(make_adapter):
    adapter = make_adapter(budget=CallBudget(max_calls=1))
    with pytest.raises(BudgetExceeded):
        adapter.search("Aledo, TX 76008", "for_sale", max_pages=10)
    assert adapter.request_count == 1


def test_without_a_budget_the_adapter_still_counts_what_it_spends(make_adapter):
    adapter = make_adapter()
    adapter.search("Aledo, TX 76008", "for_sale", max_pages=10)  # two pages
    adapter.property("29584711")
    assert adapter.request_count == 3


def test_the_politeness_delay_is_taken_between_calls(make_adapter):
    sleeper = RecordingSleeper()
    adapter = make_adapter(sleep=sleeper)
    adapter.search("Aledo, TX 76008", "for_sale", max_pages=10)
    assert sleeper.naps == [PER_CALL_DELAY_S, PER_CALL_DELAY_S]
    assert PER_CALL_DELAY_S == 0.35


def test_the_delay_can_be_turned_off_but_defaults_to_polite(make_adapter):
    sleeper = RecordingSleeper()
    make_adapter(delay_s=0, sleep=sleeper).search_page("Aledo, TX 76008")
    assert sleeper.naps == []
    # Off is a decision a caller makes on purpose; the default is the polite one.
    assert inspect.signature(ZillowAdapter).parameters["delay_s"].default == PER_CALL_DELAY_S


def test_a_budget_can_be_handed_over_at_construction_from_settings():
    settings = Settings(_env_file=None, searchapi_api_key="test-key")
    budget = CallBudget(max_calls=settings.quota_cap_searchapi_monthly)
    adapter = ZillowAdapter.from_settings(
        settings,
        client=httpx.Client(transport=FakeSearchApi()),
        budget=budget,
        sleep=RecordingSleeper(),
    )
    adapter.search_page("Aledo, TX 76008")
    assert budget.spent == 1 and budget.remaining == 999
