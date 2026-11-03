"""Orquestra a análise do negócio: regras (sempre) + LLM (opcional) + roteamento para análise manual.

Interface do LLM (plugável, sem import direto): qualquer objeto com
`analyze(payload: dict, rules_result: dict) -> dict | None` que devolva
`{enquadramento: "PADRAO"|"ESPECIFICO", confianca: 0..1, resumo, pontos_fortes[], riscos[], insights[]}`.
`None` ou exceção = falha do LLM → o resultado fica só com as regras.
"""
import logging
from dataclasses import dataclass, field
from typing import Protocol

from app.analysis.base import AREAS, SEVERIDADES, BusinessAnalyzer
from app.analysis.rules import REGRAS_CRITICAS, RuleBasedAnalyzer
from app.metrics import BUSINESS_ANALYSES, LLM_REQUESTS, SEGMENTOS

logger = logging.getLogger(__name__)

STATUS_APROVADO = "APROVADO"
STATUS_ANALISE_MANUAL = "ANALISE_MANUAL"
STATUS_EM_ANALISE = "EM_ANALISE"
STATUS_REPROVADO = "REPROVADO"

GERADO_POR_REGRAS = "regras"
GERADO_POR_LLM = "llm+regras"

CONFIANCA_MINIMA = 0.6

# Motivos de análise manual além das regras críticas (ver REGRAS_CRITICAS em rules.py).
MOTIVOS_ROTEAMENTO = {
    "SEGMENTO_OUTRO": "Segmento 'outro' sempre vai para a análise manual",
    "LLM_ESPECIFICO": "O LLM indicou que o negócio não se enquadra num padrão",
    "LLM_BAIXA_CONFIANCA": f"Confiança do LLM abaixo de {CONFIANCA_MINIMA}",
}


class LlmAnalyzerProtocol(Protocol):
    def analyze(self, payload: dict, rules_result: dict) -> dict | None: ...


@dataclass
class AnalysisOutcome:
    """Resultado da análise: `body` é o corpo da resposta do contrato (sem `atualizado_em`)."""

    status: str
    score: int
    faixa_risco: str
    gerado_por: str
    body: dict
    motivos_revisao: list[str] = field(default_factory=list)


def _dedupe(*listas) -> list[str]:
    vistos: set[str] = set()
    saida: list[str] = []
    for lista in listas:
        for item in lista or []:
            if not isinstance(item, str):
                continue
            texto = item.strip()
            chave = texto.casefold()
            if texto and chave not in vistos:
                vistos.add(chave)
                saida.append(texto)
    return saida


def _normalize_insight(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    texto = str(item.get("texto") or "").strip()
    if not texto:
        return None
    area = str(item.get("area") or "").strip().lower()
    severidade = str(item.get("severidade") or "").strip().lower()
    return {
        "area": area if area in AREAS else "geral",
        "titulo": str(item.get("titulo") or "").strip() or "Insight",
        "texto": texto,
        "severidade": severidade if severidade in SEVERIDADES else "info",
    }


def _normalize_verdict(verdict) -> dict | None:
    """Valida o veredito do LLM; qualquer forma inválida = sem veredito."""
    if not isinstance(verdict, dict):
        return None
    enquadramento = str(verdict.get("enquadramento") or "").strip().upper()
    if enquadramento not in ("PADRAO", "ESPECIFICO"):
        return None
    try:
        confianca = float(verdict.get("confianca"))
    except (TypeError, ValueError):
        return None
    if confianca != confianca:  # NaN
        return None
    resumo = verdict.get("resumo")
    raw_insights = verdict.get("insights")
    insights = [_normalize_insight(i) for i in raw_insights] if isinstance(raw_insights, list) else []
    return {
        "enquadramento": enquadramento,
        "confianca": max(0.0, min(1.0, confianca)),
        "resumo": resumo.strip() if isinstance(resumo, str) and resumo.strip() else None,
        "pontos_fortes": verdict.get("pontos_fortes") if isinstance(verdict.get("pontos_fortes"), list) else [],
        "riscos": verdict.get("riscos") if isinstance(verdict.get("riscos"), list) else [],
        "insights": [i for i in insights if i is not None],
    }


def _call_llm(llm, payload: dict, rules_result: dict) -> dict | None:
    if llm is None:
        LLM_REQUESTS.labels(result="disabled").inc()
        return None
    try:
        verdict = llm.analyze(payload, rules_result)
    except Exception:
        logger.exception("LLM falhou; seguindo só com as regras")
        return None
    normalized = _normalize_verdict(verdict)
    if verdict is not None and normalized is None:
        logger.warning("veredito do LLM inválido; seguindo só com as regras")
    return normalized


def run_analysis(
    payload: dict,
    llm: LlmAnalyzerProtocol | None = None,
    analyzer: BusinessAnalyzer | None = None,
) -> AnalysisOutcome:
    """Roda as regras (sempre) e o LLM (se houver), mescla e decide o status."""
    analyzer = analyzer or RuleBasedAnalyzer()
    rules = analyzer.analyze(payload)
    verdict = _call_llm(llm, payload, rules)

    resumo = rules["resumo"]
    pontos_fortes = _dedupe(rules["pontos_fortes"])
    riscos = _dedupe(rules["riscos"])
    insights = list(rules["insights"])
    gerado_por = GERADO_POR_REGRAS
    if verdict is not None:
        gerado_por = GERADO_POR_LLM
        resumo = verdict["resumo"] or resumo
        pontos_fortes = _dedupe(pontos_fortes, verdict["pontos_fortes"])
        riscos = _dedupe(riscos, verdict["riscos"])
        existentes = {(i["area"], i["texto"].casefold()) for i in insights}
        for insight in verdict["insights"]:
            chave = (insight["area"], insight["texto"].casefold())
            if chave not in existentes:
                existentes.add(chave)
                insights.append(insight)

    motivos = list(rules.get("regras_criticas") or [])
    if str(payload.get("segmento") or "").lower() == "outro":
        motivos.insert(0, "SEGMENTO_OUTRO")
    if verdict is not None:
        if verdict["enquadramento"] == "ESPECIFICO":
            motivos.append("LLM_ESPECIFICO")
        if verdict["confianca"] < CONFIANCA_MINIMA:
            motivos.append("LLM_BAIXA_CONFIANCA")
    status = STATUS_ANALISE_MANUAL if motivos else STATUS_APROVADO
    segmento = str(payload.get("segmento") or "").lower()
    BUSINESS_ANALYSES.labels(
        status=status, gerado_por=gerado_por, segmento=segmento if segmento in SEGMENTOS else "outro"
    ).inc()

    body = {
        "customer_id": payload.get("customer_id"),
        "status": status,
        "score": rules["score"],
        "faixa_risco": rules["faixa_risco"],
        "resumo": resumo,
        "gerado_por": gerado_por,
        "atualizado_em": None,
        "kpis": rules["kpis"],
        "insights": insights,
        "oportunidades": rules["oportunidades"],
        "pontos_fortes": pontos_fortes,
        "riscos": riscos,
    }
    return AnalysisOutcome(
        status=status,
        score=rules["score"],
        faixa_risco=rules["faixa_risco"],
        gerado_por=gerado_por,
        body=body,
        motivos_revisao=motivos,
    )


def descricao_motivo(codigo: str) -> str:
    return REGRAS_CRITICAS.get(codigo) or MOTIVOS_ROTEAMENTO.get(codigo, codigo)
