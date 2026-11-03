import threading
import time
from dataclasses import dataclass

import httpx

from app.config import settings
from app.schemas import CustomerFinancialData


class OnboardingClientError(Exception):
    pass


class CustomerNotFoundInOnboarding(OnboardingClientError):
    pass


class OnboardingForbiddenError(OnboardingClientError):
    """O token repassado não tem permissão no Onboarding (read:customers)."""


@dataclass(frozen=True)
class MeInfo:
    """Resposta de `GET /me` do Onboarding: quem é o cliente dono do token."""

    customer_id: int | None
    onboarding_status: str | None
    empresa: dict | None = None


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

    def get_me(self) -> MeInfo:
        """`GET /me` com o token repassado: resolve o `customer_id` do `sub` do token."""
        url = f"{self.base_url}/me"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise OnboardingClientError(f"Falha ao consultar Onboarding: {exc}") from exc
        if resp.status_code in (401, 403):
            raise OnboardingForbiddenError("Sem permissão para consultar /me no Onboarding (read:self)")
        if resp.status_code >= 400:
            raise OnboardingClientError(f"Onboarding retornou {resp.status_code}")
        body = resp.json()
        customer_id = body.get("customer_id")
        return MeInfo(
            customer_id=int(customer_id) if customer_id is not None else None,
            onboarding_status=body.get("onboarding_status"),
            empresa=body.get("empresa"),
        )


class MeCache:
    """Cache em memória de `GET /me` por `sub` (TTL padrão de 5 min, contrato seção 2).

    Só guarda quem já tem `customer_id` (o vínculo sub → customer não muda); sem customer
    a consulta é sempre refeita, para o cliente que acabou de se cadastrar não esperar o TTL.
    """

    def __init__(self, ttl_seconds: float = 300.0, clock=time.monotonic):
        self.ttl = ttl_seconds
        self.clock = clock
        self._entries: dict[str, tuple[float, MeInfo]] = {}
        self._lock = threading.Lock()

    def get(self, subject: str, fetch, fresh: bool = False) -> MeInfo:
        now = self.clock()
        if not fresh:
            with self._lock:
                entry = self._entries.get(subject)
            if entry is not None and entry[0] > now:
                return entry[1]
        info = fetch()
        if info.customer_id is not None:
            with self._lock:
                self._entries[subject] = (now + self.ttl, info)
        return info

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


me_cache = MeCache()
