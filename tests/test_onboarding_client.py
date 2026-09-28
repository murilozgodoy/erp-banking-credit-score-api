import httpx
import pytest
import respx

from app.clients.onboarding_client import (
    CustomerNotFoundInOnboarding,
    OnboardingClient,
    OnboardingClientError,
)


BASE = "http://onboarding.test"


@respx.mock
def test_get_customer_financials_maps_response(onboarding_response):
    respx.get(f"{BASE}/customers/1").mock(
        return_value=httpx.Response(200, json=onboarding_response)
    )
    data = OnboardingClient(base_url=BASE).get_customer_financials(1)
    assert data.customer_id == 1
    assert data.renda_mensal == 8000.0
    assert data.dividas_ativas == 1500.0


@respx.mock
def test_get_customer_404_raises_not_found():
    respx.get(f"{BASE}/customers/42").mock(
        return_value=httpx.Response(404, json={"detail": "não encontrado"})
    )
    with pytest.raises(CustomerNotFoundInOnboarding):
        OnboardingClient(base_url=BASE).get_customer_financials(42)


@respx.mock
def test_get_customer_500_raises_client_error():
    respx.get(f"{BASE}/customers/1").mock(return_value=httpx.Response(500))
    with pytest.raises(OnboardingClientError):
        OnboardingClient(base_url=BASE).get_customer_financials(1)


@respx.mock
def test_transport_error_raises_client_error():
    respx.get(f"{BASE}/customers/1").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(OnboardingClientError):
        OnboardingClient(base_url=BASE).get_customer_financials(1)


@respx.mock
def test_missing_optional_sections_defaults_to_zero():
    minimal = {
        "id": 9, "nome": "N", "cpf": "0", "email": "n@n.n", "telefone": "0",
        "created_at": "2026-01-01T00:00:00",
        "address": None, "financial_profile": None, "anamnesis": None,
    }
    respx.get(f"{BASE}/customers/9").mock(return_value=httpx.Response(200, json=minimal))
    data = OnboardingClient(base_url=BASE).get_customer_financials(9)
    assert data.renda_mensal == 0.0
    assert data.dividas_ativas == 0.0
