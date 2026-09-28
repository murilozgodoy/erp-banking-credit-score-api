import httpx

from app.config import settings
from app.schemas import CustomerFinancialData


class OnboardingClientError(Exception):
    pass


class CustomerNotFoundInOnboarding(OnboardingClientError):
    pass


class OnboardingClient:
    """Cliente HTTP síncrono para o serviço de Onboarding."""

    def __init__(self, base_url: str | None = None, timeout: float = 5.0):
        self.base_url = (base_url or settings.onboarding_api_url).rstrip("/")
        self.timeout = timeout

    def get_customer_financials(self, customer_id: int) -> CustomerFinancialData:
        url = f"{self.base_url}/customers/{customer_id}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url)
        except httpx.HTTPError as exc:
            raise OnboardingClientError(f"Falha ao consultar Onboarding: {exc}") from exc

        if resp.status_code == 404:
            raise CustomerNotFoundInOnboarding(f"Cliente {customer_id} não encontrado")
        if resp.status_code >= 400:
            raise OnboardingClientError(f"Onboarding retornou {resp.status_code}")

        body = resp.json()
        fp = body.get("financial_profile") or {}
        anamnesis = body.get("anamnesis") or {}
        return CustomerFinancialData(
            customer_id=body["id"],
            renda_mensal=float(fp.get("renda_mensal", 0.0)),
            patrimonio=float(fp.get("patrimonio", 0.0)),
            dividas_ativas=float(anamnesis.get("dividas_ativas", 0.0)),
            comprometimento_renda=float(anamnesis.get("comprometimento_renda", 0.0)),
        )
