import httpx

from app.config import settings
from app.schemas import CustomerFinancialData


class OnboardingClientError(Exception):
    pass


class CustomerNotFoundInOnboarding(OnboardingClientError):
    pass


class OnboardingForbiddenError(OnboardingClientError):
    """O token repassado não tem permissão no Onboarding (read:customers)."""


class OnboardingClient:
    """Cliente HTTP síncrono para o serviço de Onboarding.

    Repassa o access token do usuário (token relay): o Onboarding valida o mesmo JWT
    e aplica as próprias permissões.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 5.0,
        access_token: str | None = None,
    ):
        self.base_url = (base_url or settings.onboarding_api_url).rstrip("/")
        self.timeout = timeout
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            return {}
        return {"Authorization": f"Bearer {self.access_token}"}

    def get_customer_financials(self, customer_id: int) -> CustomerFinancialData:
        url = f"{self.base_url}/customers/{customer_id}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise OnboardingClientError(f"Falha ao consultar Onboarding: {exc}") from exc

        if resp.status_code == 404:
            raise CustomerNotFoundInOnboarding(f"Cliente {customer_id} não encontrado")
        if resp.status_code == 403:
            raise OnboardingForbiddenError(
                "Sem permissão para consultar clientes no Onboarding (read:customers)"
            )
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
