"""`RuleBasedAnalyzer`: análise determinística e explicável a partir das respostas do onboarding.

Sempre roda (com ou sem LLM). Respostas opcionais ausentes não quebram nada: o KPI fica com
`valor: null` e a regra correspondente não pontua.
"""
import math
import re

from app.analysis.base import BusinessAnalyzer
from app.analysis.benchmarks import SEGMENTO_NOMES, benchmark_for
from app.strategies.base import ScoreStrategy

# --- Precificação (PORTAL_CLIENTE.md, seção 5) ------------------------------------------------

TAXA_BASE = {"BAIXO": 1.69, "MEDIO": 2.49, "ALTO": 3.49}  # MUITO_ALTO: nega (sem oferta)
AJUSTE_PRODUTO = {
    "ANTECIPACAO_RECEBIVEIS": -0.4,
    "FINANCIAMENTO_ESTOQUE": -0.2,
    "CAPITAL_DE_GIRO": 0.0,
    "ALAVANCAGEM": 0.3,
    "CONSOLIDACAO_DIVIDAS": 0.0,
}
# Prazos máximos iguais aos do simulador do Transactions (CreditProduct): a oportunidade sugerida aqui
# vira link para /app/credito, e um prazo maior que o do simulador seria recusado lá.
PRAZO_MAX = {
    "ANTECIPACAO_RECEBIVEIS": 6,
    "CAPITAL_DE_GIRO": 24,
    "ALAVANCAGEM": 36,
    "FINANCIAMENTO_ESTOQUE": 18,
    "CONSOLIDACAO_DIVIDAS": 24,
}
LIMITE_ANTECIPACAO = 0.80  # dos recebíveis de cartão previstos
LIMITE_ESTOQUE = 0.60  # do valor de estoque
LIMITE_ALAVANCAGEM = 3.0  # vezes a receita mensal
CAPACIDADE_PAGAMENTO = 0.25  # parcela total máxima sobre a receita mensal

# --- Regras críticas (vão para a análise manual) ---------------------------------------------

REGRAS_CRITICAS = {
    "RECEITA_INVALIDA": "Faturamento mensal ausente, zero ou negativo",
    "DIVIDA_EXCESSIVA": "Dívidas totais acima de 3x o faturamento mensal",
    "PARCELAS_EXCESSIVAS": "Parcelas de dívidas acima de 50% do faturamento mensal",
    "MARGEM_NEGATIVA": "Margem bruta negativa",
    "RISCO_MUITO_ALTO": "Score do negócio na faixa MUITO_ALTO",
}


def taxa_mensal(faixa: str, produto: str) -> float | None:
    """Taxa mensal (%) do produto para a faixa; `None` quando a faixa nega crédito."""
    base = TAXA_BASE.get(faixa)
    if base is None:
        return None
    return round(base + AJUSTE_PRODUTO[produto], 2)


def valor_presente(parcela: float, taxa_pct: float, meses: int) -> float:
    """Valor financiável por uma parcela fixa (tabela Price)."""
    i = taxa_pct / 100
    return parcela * (1 - (1 + i) ** -meses) / i


def parcela_price(valor: float, taxa_pct: float, meses: int) -> float:
    i = taxa_pct / 100
    return valor * i / (1 - (1 + i) ** -meses)


# --- Normalização das respostas --------------------------------------------------------------


MAX_VALOR = 1e12  # acima disso a resposta é absurda (e pode estourar float nos cálculos)


def _finito(value: float) -> float | None:
    return value if math.isfinite(value) and abs(value) <= MAX_VALOR else None


_MILHAR = re.compile(r"^-?[1-9]\d{0,2}(\.\d{3})+$")


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _finito(float(value))
    if isinstance(value, str):
        text = value.strip().replace("R$", "").replace("%", "").replace(" ", "")
        if not text:
            return None
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        elif _MILHAR.match(text):  # "182.000" = 182 mil (separador de milhar brasileiro)
            text = text.replace(".", "")
        else:
            text = text.replace(",", ".")
        try:
            parsed = float(text)
        except ValueError:
            return None
        return _finito(parsed)
    return None


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"sim", "s", "true", "1", "yes", "y"}
    return False


def _sazonalidade(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if "alta" in text or "forte" in text:
        return "alta"
    if "med" in text or "méd" in text or "moderada" in text:
        return "media"
    if "baixa" in text or "nenhuma" in text or "sem" in text or "estavel" in text or "estável" in text:
        return "baixa"
    return None


def _brl(value: float) -> str:
    return "R$ " + f"{value:,.0f}".replace(",", ".")


def _arred(value: float, passo: int = 100) -> int:
    """Arredonda valores de oferta para baixo, em múltiplos de `passo`."""
    return int(max(0.0, value) // passo * passo)


def _fmt(value: float, casas: int = 1) -> str:
    return f"{value:.{casas}f}".replace(".", ",")


# --- KPIs -------------------------------------------------------------------------------------

# id: (label, unidade, maior_e_melhor)
KPI_META = {
    "receita_mensal": ("Receita mensal", "BRL", True),
    "margem_bruta_pct": ("Margem bruta", "%", True),
    "ticket_medio": ("Ticket médio", "BRL", None),
    "prazo_medio_recebimento_dias": ("Prazo médio de recebimento", "dias", False),
    "prazo_medio_pagamento_dias": ("Prazo médio de pagamento", "dias", True),
    "ciclo_caixa_dias": ("Ciclo de caixa", "dias", False),
    "endividamento_sobre_receita": ("Endividamento sobre a receita", "x", False),
    "comprometimento_parcelas_pct": ("Comprometimento com parcelas", "%", False),
    "cobertura_estoque_dias": ("Cobertura de estoque", "dias", False),
}


def _leitura(valor: float | None, bench: float | None, maior_e_melhor: bool | None) -> str:
    if valor is None:
        return "não informado"
    if bench is None or bench == 0:
        if valor == 0:
            return "em linha com a média do segmento"
        return "acima da média do segmento" if valor > 0 else "abaixo da média do segmento"
    tolerancia = abs(bench) * 0.10
    if abs(valor - bench) <= tolerancia:
        return "em linha com a média do segmento"
    acima = valor > bench
    posicao = "acima da média do segmento" if acima else "abaixo da média do segmento"
    if maior_e_melhor is None:
        return posicao
    favoravel = acima == maior_e_melhor
    return f"{posicao} ({'favorável' if favoravel else 'desfavorável'})"


def _kpi(kpi_id: str, valor: float | None, bench: float | None) -> dict:
    label, unidade, maior_e_melhor = KPI_META[kpi_id]
    casas = 0 if unidade == "BRL" else (2 if unidade == "x" else 1)
    return {
        "id": kpi_id,
        "label": label,
        "valor": None if valor is None else round(valor, casas) if casas else round(valor),
        "unidade": unidade,
        "benchmark_segmento": None if bench is None else round(bench, casas) if casas else round(bench),
        "leitura": _leitura(valor, bench, maior_e_melhor),
    }


class RuleBasedAnalyzer(BusinessAnalyzer):
    """Regras explicáveis: KPIs vs. benchmark do segmento → score, faixa, insights e ofertas."""

    name = "regras"

    def analyze(self, payload: dict) -> dict:
        ctx = self._context(payload)
        kpis = self._kpis(ctx)
        score = self._score(ctx)
        faixa = ScoreStrategy._classify(score)
        ctx["faixa"] = faixa
        criticas = self._regras_criticas(ctx, faixa)
        oportunidades = self._oportunidades(ctx, faixa)
        pontos_fortes, riscos = self._pontos_fortes_e_riscos(ctx, criticas)
        insights = self._insights(ctx, faixa, oportunidades)
        return {
            "score": score,
            "faixa_risco": faixa,
            "resumo": self._resumo(ctx, score, faixa, oportunidades),
            "kpis": kpis,
            "insights": insights,
            "oportunidades": oportunidades,
            "pontos_fortes": pontos_fortes,
            "riscos": riscos,
            "regras_criticas": criticas,
        }

    # -- contexto ------------------------------------------------------------------------------

    @staticmethod
    def _context(payload: dict) -> dict:
        respostas = payload.get("respostas") or {}
        if not isinstance(respostas, dict):
            respostas = {}
        empresa = payload.get("empresa") or {}
        if not isinstance(empresa, dict):
            empresa = {}
        segmento = str(payload.get("segmento") or "outro").lower()
        bench = benchmark_for(segmento)

        receita = _num(respostas.get("faturamento_mensal"))
        margem = _num(respostas.get("margem_bruta_pct"))
        pmr = _num(respostas.get("prazo_medio_recebimento_dias"))
        pmp = _num(respostas.get("prazo_medio_pagamento_dias"))
        dividas = _num(respostas.get("dividas_total"))
        parcela = _num(respostas.get("parcela_dividas_mensal"))
        folha = _num(respostas.get("folha_mensal"))
        ticket = _num(respostas.get("ticket_medio"))
        pct_cartao = _num(respostas.get("pct_cartao_credito"))
        valor_estoque = _num(respostas.get("valor_estoque"))
        possui_estoque = _bool(respostas.get("possui_estoque")) or bool(
            valor_estoque and valor_estoque > 0 and "possui_estoque" not in respostas
        )
        receita_ok = receita is not None and receita > 0

        cobertura = None
        if possui_estoque and valor_estoque is not None and receita_ok:
            margem_ref = margem if margem is not None else bench["margem_bruta_pct"]
            cmv_mensal = receita * max(0.05, 1 - margem_ref / 100)
            cobertura = valor_estoque / (cmv_mensal / 30)

        ciclo = None
        if pmr is not None and pmp is not None:
            ciclo = pmr + (cobertura or 0.0) - pmp

        return {
            "segmento": segmento,
            "segmento_nome": SEGMENTO_NOMES.get(segmento, segmento),
            "empresa": empresa.get("nome_fantasia") or empresa.get("razao_social") or "A empresa",
            "bench": bench,
            "receita": receita,
            "receita_ok": receita_ok,
            "margem": margem,
            "pmr": pmr,
            "pmp": pmp,
            "dividas": dividas,
            "parcela": parcela,
            "folha": folha,
            "ticket": ticket,
            "pct_cartao": pct_cartao,
            "possui_estoque": possui_estoque,
            "valor_estoque": valor_estoque,
            "cobertura": cobertura,
            "ciclo": ciclo,
            "endividamento": dividas / receita if receita_ok and dividas is not None else None,
            "comprometimento": parcela / receita * 100 if receita_ok and parcela is not None else None,
            "folha_pct": folha / receita * 100 if receita_ok and folha is not None else None,
            "sazonalidade": _sazonalidade(respostas.get("sazonalidade")),
            "mrr": _num(respostas.get("mrr")),
            "churn": _num(respostas.get("churn_mensal_pct")),
        }

    def _kpis(self, ctx: dict) -> list[dict]:
        b = ctx["bench"]
        kpis = [
            _kpi("receita_mensal", ctx["receita"], b["receita_mensal"]),
            _kpi("margem_bruta_pct", ctx["margem"], b["margem_bruta_pct"]),
            _kpi("ticket_medio", ctx["ticket"], b["ticket_medio"]),
            _kpi("prazo_medio_recebimento_dias", ctx["pmr"], b["prazo_medio_recebimento_dias"]),
            _kpi("prazo_medio_pagamento_dias", ctx["pmp"], b["prazo_medio_pagamento_dias"]),
            _kpi("ciclo_caixa_dias", ctx["ciclo"], b["ciclo_caixa_dias"]),
            _kpi("endividamento_sobre_receita", ctx["endividamento"], b["endividamento_sobre_receita"]),
            _kpi("comprometimento_parcelas_pct", ctx["comprometimento"], b["comprometimento_parcelas_pct"]),
        ]
        if ctx["possui_estoque"]:
            kpis.append(_kpi("cobertura_estoque_dias", ctx["cobertura"], b["cobertura_estoque_dias"]))
        return kpis

    # -- score ---------------------------------------------------------------------------------

    @staticmethod
    def _score(ctx: dict) -> int:
        """Base 500 + pontos por regra; limitado a 0–1000."""
        b = ctx["bench"]
        pontos = 500.0
        if not ctx["receita_ok"]:
            return 0 if ctx["receita"] is not None and ctx["receita"] < 0 else 150
        ratio = ctx["receita"] / b["receita_mensal"]
        pontos += 80 if ratio >= 1.5 else 50 if ratio >= 1 else 10 if ratio >= 0.5 else -40

        margem = ctx["margem"]
        if margem is not None:
            ref = b["margem_bruta_pct"]
            pontos += 80 if margem >= ref + 5 else 50 if margem >= ref else 0 if margem >= ref - 10 else -60
            if margem < 0:
                pontos -= 150

        endiv = ctx["endividamento"]
        if endiv is not None:
            pontos += 120 if endiv <= 0.5 else 70 if endiv <= 1 else 0 if endiv <= 2 else -100 if endiv <= 3 else -200

        comp = ctx["comprometimento"]
        if comp is not None:
            pontos += 80 if comp <= 10 else 40 if comp <= 20 else -20 if comp <= 30 else -100

        ciclo = ctx["ciclo"]
        if ciclo is not None:
            ref = max(b["ciclo_caixa_dias"], 1)
            pontos += 60 if ciclo <= 0 else 30 if ciclo <= ref else -20 if ciclo <= 2 * ref else -60

        folha_pct = ctx["folha_pct"]
        if folha_pct is not None:
            pontos += 20 if folha_pct <= 30 else 0 if folha_pct <= 50 else -40

        pontos += {"alta": -30, "media": -10, "baixa": 10}.get(ctx["sazonalidade"], 0)

        if ctx["cobertura"] is not None and b["cobertura_estoque_dias"] > 0:
            if ctx["cobertura"] > 2 * b["cobertura_estoque_dias"]:
                pontos -= 30

        if ctx["churn"] is not None and ctx["churn"] > 5:
            pontos -= 40

        return int(round(max(0.0, min(1000.0, pontos))))

    @staticmethod
    def _regras_criticas(ctx: dict, faixa: str) -> list[str]:
        criticas = []
        if not ctx["receita_ok"]:
            criticas.append("RECEITA_INVALIDA")
        else:
            if ctx["dividas"] is not None and ctx["dividas"] > 3 * ctx["receita"]:
                criticas.append("DIVIDA_EXCESSIVA")
            if ctx["parcela"] is not None and ctx["parcela"] > 0.5 * ctx["receita"]:
                criticas.append("PARCELAS_EXCESSIVAS")
        if ctx["margem"] is not None and ctx["margem"] < 0:
            criticas.append("MARGEM_NEGATIVA")
        if faixa == "MUITO_ALTO":
            criticas.append("RISCO_MUITO_ALTO")
        return criticas

    # -- oportunidades -------------------------------------------------------------------------

    @staticmethod
    def _recebiveis_cartao(ctx: dict) -> float:
        """Recebíveis de cartão em aberto estimados: vendas no crédito x prazo de recebimento."""
        if not ctx["receita_ok"]:
            return 0.0
        pct = ctx["pct_cartao"] if ctx["pct_cartao"] is not None else ctx["bench"]["pct_cartao_credito"]
        pct = max(0.0, min(100.0, pct))
        prazo = ctx["pmr"] if ctx["pmr"] is not None and ctx["pmr"] > 0 else 30.0
        return ctx["receita"] * pct / 100 * max(prazo, 30.0) / 30

    def _oportunidades(self, ctx: dict, faixa: str) -> list[dict]:
        if faixa not in TAXA_BASE or not ctx["receita_ok"]:
            return []
        receita = ctx["receita"]
        ofertas: list[dict] = []

        def oferta(tipo, titulo, descricao, valor, racional):
            valor = _arred(valor)
            if valor <= 0:
                return
            ofertas.append({
                "tipo": tipo,
                "titulo": titulo,
                "descricao": descricao,
                "valor_maximo": valor,
                "taxa_mensal": taxa_mensal(faixa, tipo),
                "prazo_max_meses": PRAZO_MAX[tipo],
                "racional": racional,
            })

        recebiveis = self._recebiveis_cartao(ctx)
        if recebiveis > 0:
            oferta(
                "ANTECIPACAO_RECEBIVEIS",
                "Antecipação de recebíveis",
                "Receba hoje as vendas no cartão que só cairiam nas próximas semanas.",
                LIMITE_ANTECIPACAO * recebiveis,
                f"Até 80% dos {_brl(recebiveis)} estimados em recebíveis de cartão em aberto.",
            )

        dias = ctx["ciclo"] if ctx["ciclo"] is not None else ctx["bench"]["ciclo_caixa_dias"]
        necessidade = min(receita / 30 * max(dias, 15.0), receita)
        oferta(
            "CAPITAL_DE_GIRO",
            "Capital de giro",
            "Reforço de caixa para cobrir o intervalo entre pagar fornecedores e receber clientes.",
            necessidade,
            f"Receita diária de {_brl(receita / 30)} x {max(dias, 15.0):.0f} dias de ciclo de caixa, "
            "limitado a 1x a receita mensal.",
        )

        if ctx["possui_estoque"] and ctx["valor_estoque"] and ctx["valor_estoque"] > 0:
            oferta(
                "FINANCIAMENTO_ESTOQUE",
                "Financiamento de estoque",
                "Compre mais mercadoria com o próprio estoque como referência de limite.",
                LIMITE_ESTOQUE * ctx["valor_estoque"],
                f"Até 60% do estoque declarado de {_brl(ctx['valor_estoque'])}.",
            )

        endiv = ctx["endividamento"] or 0.0
        comp = ctx["comprometimento"] or 0.0
        if faixa in ("BAIXO", "MEDIO") and endiv <= 1.5 and comp <= 20:
            taxa = taxa_mensal(faixa, "ALAVANCAGEM")
            folga = max(0.0, CAPACIDADE_PAGAMENTO * receita - (ctx["parcela"] or 0.0))
            capacidade = valor_presente(folga, taxa, PRAZO_MAX["ALAVANCAGEM"])
            oferta(
                "ALAVANCAGEM",
                "Crédito para crescer",
                "Investimento em expansão: nova unidade, equipamentos ou marketing.",
                min(capacidade, LIMITE_ALAVANCAGEM * receita),
                f"Parcela de até {_brl(folga)} (25% da receita menos as parcelas atuais) em "
                f"{PRAZO_MAX['ALAVANCAGEM']} meses, limitado a 3x a receita mensal.",
            )

        dividas = ctx["dividas"] or 0.0
        if dividas > 0 and (comp > 15 or endiv > 1):
            taxa = taxa_mensal(faixa, "CONSOLIDACAO_DIVIDAS")
            nova = parcela_price(dividas, taxa, PRAZO_MAX["CONSOLIDACAO_DIVIDAS"])
            atual = ctx["parcela"]
            racional = f"Junta {_brl(dividas)} em dívidas em {PRAZO_MAX['CONSOLIDACAO_DIVIDAS']} parcelas de {_brl(nova)}"
            racional += f" (hoje: {_brl(atual)} por mês)." if atual else "."
            oferta(
                "CONSOLIDACAO_DIVIDAS",
                "Consolidação de dívidas",
                "Troque várias dívidas caras por uma parcela única e mais longa.",
                min(dividas, LIMITE_ALAVANCAGEM * receita),
                racional,
            )
        return ofertas

    # -- textos --------------------------------------------------------------------------------

    @staticmethod
    def _pontos_fortes_e_riscos(ctx: dict, criticas: list[str]) -> tuple[list[str], list[str]]:
        b = ctx["bench"]
        fortes: list[str] = []
        riscos: list[str] = [REGRAS_CRITICAS[c] for c in criticas]
        if ctx["receita_ok"] and ctx["receita"] >= b["receita_mensal"]:
            fortes.append(f"Receita mensal de {_brl(ctx['receita'])}, acima da média do segmento")
        if ctx["margem"] is not None:
            if ctx["margem"] >= b["margem_bruta_pct"]:
                fortes.append(f"Margem bruta de {_fmt(ctx['margem'])}% acima da média do segmento")
            elif ctx["margem"] < b["margem_bruta_pct"] - 10:
                riscos.append(f"Margem bruta de {_fmt(ctx['margem'])}% bem abaixo do segmento")
        if ctx["endividamento"] is not None:
            if ctx["endividamento"] <= 0.5:
                fortes.append("Baixo endividamento em relação à receita")
            elif ctx["endividamento"] > 2 and "DIVIDA_EXCESSIVA" not in criticas:
                riscos.append(f"Dívidas equivalem a {_fmt(ctx['endividamento'])}x a receita mensal")
        if ctx["comprometimento"] is not None:
            if ctx["comprometimento"] <= 10:
                fortes.append("Parcelas de dívidas comprometem pouco da receita")
            elif ctx["comprometimento"] > 30 and "PARCELAS_EXCESSIVAS" not in criticas:
                riscos.append(f"Parcelas comprometem {_fmt(ctx['comprometimento'])}% da receita")
        if ctx["ciclo"] is not None:
            if ctx["ciclo"] <= 0:
                fortes.append("Ciclo de caixa negativo: recebe antes de pagar")
            elif ctx["ciclo"] > 2 * max(b["ciclo_caixa_dias"], 1):
                riscos.append(f"Ciclo de caixa longo ({ctx['ciclo']:.0f} dias)")
        if ctx["folha_pct"] is not None and ctx["folha_pct"] > 50:
            riscos.append(f"Folha consome {_fmt(ctx['folha_pct'])}% da receita")
        if ctx["sazonalidade"] == "alta":
            riscos.append("Sazonalidade alta: receita oscila ao longo do ano")
        if ctx["cobertura"] is not None and b["cobertura_estoque_dias"] > 0:
            if ctx["cobertura"] > 2 * b["cobertura_estoque_dias"]:
                riscos.append(f"Estoque cobre {ctx['cobertura']:.0f} dias de vendas: capital parado")
        if ctx["churn"] is not None and ctx["churn"] > 5:
            riscos.append(f"Churn mensal de {_fmt(ctx['churn'])}%")
        return fortes, riscos

    def _insights(self, ctx: dict, faixa: str, oportunidades: list[dict]) -> list[dict]:
        b = ctx["bench"]
        insights: list[dict] = []

        def add(area, titulo, texto, severidade="info"):
            insights.append({"area": area, "titulo": titulo, "texto": texto, "severidade": severidade})

        ofertas = {o["tipo"]: o for o in oportunidades}

        # geral
        if ctx["receita_ok"]:
            if ctx["margem"] is not None:
                lucro_bruto = ctx["receita"] * ctx["margem"] / 100
                add("geral", "Lucro bruto estimado",
                    f"Com margem de {_fmt(ctx['margem'])}%, sobram cerca de {_brl(lucro_bruto)} por mês "
                    "antes das despesas fixas.",
                    "info" if ctx["margem"] >= b["margem_bruta_pct"] - 10 else "atencao")
            else:
                add("geral", "Informe sua margem",
                    "Sem a margem bruta não dá para estimar o lucro do negócio; complete o cadastro no ErpIa.",
                    "atencao")
        else:
            add("geral", "Faturamento não informado",
                "Sem um faturamento mensal válido a análise automática fica limitada.", "atencao")

        # vendas
        if ctx["pct_cartao"] is not None and ctx["pct_cartao"] >= 40:
            add("vendas", "Vendas concentradas no crédito",
                f"{_fmt(ctx['pct_cartao'], 0)}% das vendas no cartão de crédito alongam o prazo de recebimento.",
                "atencao")
        elif ctx["ticket"] is not None:
            add("vendas", "Ticket médio",
                f"Ticket médio de {_brl(ctx['ticket'])}; a média do segmento é {_brl(b['ticket_medio'])}.")
        elif ctx["receita_ok"]:
            add("vendas", "Receita frente ao segmento",
                f"Receita de {_brl(ctx['receita'])} contra {_brl(b['receita_mensal'])} na média do segmento.")

        # recebíveis
        if ctx["pmr"] is not None:
            sev = "atencao" if ctx["pmr"] > b["prazo_medio_recebimento_dias"] * 1.1 else "info"
            texto = (f"Você leva {ctx['pmr']:.0f} dias para receber; a média do segmento é "
                     f"{b['prazo_medio_recebimento_dias']:.0f} dias.")
            if "ANTECIPACAO_RECEBIVEIS" in ofertas:
                sev = "oportunidade"
                texto += (f" Dá para antecipar até {_brl(ofertas['ANTECIPACAO_RECEBIVEIS']['valor_maximo'])}.")
            add("recebiveis", "Prazo de recebimento", texto, sev)
        elif "ANTECIPACAO_RECEBIVEIS" in ofertas:
            add("recebiveis", "Recebíveis de cartão",
                f"Até {_brl(ofertas['ANTECIPACAO_RECEBIVEIS']['valor_maximo'])} podem ser antecipados.",
                "oportunidade")

        # pagamentos
        if ctx["pmr"] is not None and ctx["pmp"] is not None:
            gap = ctx["pmr"] - ctx["pmp"]
            if gap > 0:
                add("pagamentos", "Paga antes de receber",
                    f"Você paga fornecedores {gap:.0f} dias antes de receber dos clientes; negociar prazo "
                    "maior alivia o caixa.", "atencao")
            else:
                add("pagamentos", "Prazo de pagamento saudável",
                    f"Você recebe {abs(gap):.0f} dias antes de pagar fornecedores.")
        elif ctx["folha_pct"] is not None:
            add("pagamentos", "Peso da folha",
                f"A folha representa {_fmt(ctx['folha_pct'])}% da receita mensal.",
                "atencao" if ctx["folha_pct"] > 50 else "info")

        # estoque
        if ctx["possui_estoque"] and ctx["cobertura"] is not None:
            ref = b["cobertura_estoque_dias"]
            if ref > 0 and ctx["cobertura"] > 2 * ref:
                add("estoque", "Estoque acima do necessário",
                    f"O estoque cobre {ctx['cobertura']:.0f} dias de vendas (segmento: {ref:.0f}); há capital parado.",
                    "atencao")
            elif "FINANCIAMENTO_ESTOQUE" in ofertas:
                add("estoque", "Financie o estoque",
                    f"Estoque cobre {ctx['cobertura']:.0f} dias de vendas; até "
                    f"{_brl(ofertas['FINANCIAMENTO_ESTOQUE']['valor_maximo'])} disponíveis para reposição.",
                    "oportunidade")
            else:
                add("estoque", "Cobertura de estoque", f"O estoque cobre {ctx['cobertura']:.0f} dias de vendas.")

        # pendências
        if ctx["comprometimento"] is not None:
            alto = ctx["comprometimento"] > b["comprometimento_parcelas_pct"]
            texto = f"As parcelas de dívidas consomem {_fmt(ctx['comprometimento'])}% da receita mensal."
            if alto and "CONSOLIDACAO_DIVIDAS" in ofertas:
                texto += " Consolidar as dívidas reduz a parcela."
            add("pendencias", "Parcelas de dívidas", texto, "atencao" if alto else "info")
        elif ctx["dividas"] is not None and ctx["dividas"] > 0:
            add("pendencias", "Dívidas em aberto",
                f"{_brl(ctx['dividas'])} em dívidas declaradas; informe a parcela mensal para uma leitura melhor.",
                "atencao")

        # crédito
        if oportunidades:
            melhor = max(oportunidades, key=lambda o: o["valor_maximo"])
            add("credito", "Crédito pré-aprovado",
                f"{melhor['titulo']} de até {_brl(melhor['valor_maximo'])} a partir de "
                f"{_fmt(melhor['taxa_mensal'], 2)}% ao mês.", "oportunidade")
        elif ctx["receita_ok"]:
            add("credito", "Sem oferta de crédito no momento",
                f"Na faixa de risco {faixa} não há oferta automática; melhorar margem e reduzir dívidas "
                "aumenta o score.", "atencao")
        return insights

    @staticmethod
    def _resumo(ctx: dict, score: int, faixa: str, oportunidades: list[dict]) -> str:
        texto = (f"{ctx['empresa']} ({ctx['segmento_nome']}): score do negócio {score} "
                 f"(risco {faixa}) pela análise automática do ErpIa.")
        if ctx["receita_ok"]:
            texto += f" Receita mensal de {_brl(ctx['receita'])}"
            if ctx["margem"] is not None:
                texto += f" com margem bruta de {_fmt(ctx['margem'])}%"
            texto += "."
        if oportunidades:
            total = sum(o["valor_maximo"] for o in oportunidades)
            texto += f" {len(oportunidades)} oportunidades de crédito somando até {_brl(total)}."
        return texto
