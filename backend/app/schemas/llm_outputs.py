"""Versioned, strict contracts for business-facing model output.

These models are deliberately separate from API DTOs.  They validate the
provider payload at the task step boundary before anything is persisted or
rendered by a product page.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


NonBlankText = Annotated[str, Field(min_length=1)]
Score100 = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Score10 = Annotated[float, Field(ge=0, le=10, allow_inf_nan=False)]
PositiveNumber = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class _StrictOutput(BaseModel):
    """Shared strictness and the durable schema version marker."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )
    schema_version: ClassVar[str] = "v1"


class SupportResistance(_StrictOutput):
    type: Literal["支撑", "阻力"]
    price: PositiveNumber
    strength: NonBlankText


class TechnicalAnalysisOutput(_StrictOutput):
    trend: NonBlankText
    score: Score100
    short_trend: NonBlankText
    mid_trend: NonBlankText
    long_trend: NonBlankText
    support_resistance: list[SupportResistance]
    breakout_prob: Score100
    indicator_readings: NonBlankText
    pattern: NonBlankText


class FundamentalAnalysisOutput(_StrictOutput):
    financial_health: NonBlankText
    profitability: NonBlankText
    valuation: NonBlankText
    score: Score10
    detail: NonBlankText


class CapitalAnalysisOutput(_StrictOutput):
    main_flow: NonBlankText
    flow_trend: NonBlankText
    score: Score10
    detail: NonBlankText


class NewsAnalysisOutput(_StrictOutput):
    sentiment_rating: Literal["利好", "利空", "中性偏利好", "中性偏利空", "中性"]
    key_news: list[NonBlankText]
    impact: NonBlankText


class SentimentAnalysisOutput(_StrictOutput):
    sentiment_score: Score100
    indicators: NonBlankText
    assessment: NonBlankText


class ChiefDecisionOutput(_StrictOutput):
    rating: Literal["买入", "持有", "卖出"]
    target_price: PositiveNumber | None = None
    stop_loss: PositiveNumber | None = None
    confidence: Score100
    entry_range: NonBlankText
    take_profit: NonBlankText
    holding_period: NonBlankText
    position_size: NonBlankText
    risk_warning: NonBlankText
    key_watchpoints: list[NonBlankText]
    meeting_summary: NonBlankText


class _MainForceAnalystOutput(_StrictOutput):
    focus_stocks: list[NonBlankText]
    analysis: NonBlankText
    score: Score10


class MainForceCapitalOutput(_MainForceAnalystOutput):
    flow_concentration: NonBlankText


class MainForceIndustryOutput(_MainForceAnalystOutput):
    sector_trend: NonBlankText


class MainForceFundamentalOutput(_MainForceAnalystOutput):
    health_rating: NonBlankText


class MainForceTechnicalOutput(_MainForceAnalystOutput):
    pattern: NonBlankText


class MainForceQuantOutput(_MainForceAnalystOutput):
    quant_signals: list[NonBlankText]


class RecommendedCompany(_StrictOutput):
    code: NonBlankText
    name: NonBlankText
    buy_range: NonBlankText
    sell_range: NonBlankText
    confidence: Score100
    position: NonBlankText
    logic: NonBlankText


class ExcludedCompany(_StrictOutput):
    code: NonBlankText
    name: NonBlankText
    reason: NonBlankText


class MainForceResearcherOutput(_StrictOutput):
    companies: list[RecommendedCompany]
    excluded: list[ExcludedCompany]
    meeting_summary: NonBlankText


class SectorMacroOutput(_StrictOutput):
    report: NonBlankText
    score: Score10


class SectorDiagnosisItem(_StrictOutput):
    name: NonBlankText
    health: NonBlankText
    trend: NonBlankText


class SectorDiagnosisOutput(_StrictOutput):
    sectors: list[SectorDiagnosisItem]
    score: Score10


class SectorCapitalOutput(_StrictOutput):
    inflow_sectors: list[NonBlankText]
    outflow_sectors: list[NonBlankText]
    report: NonBlankText
    score: Score10


class SectorSentimentOutput(_StrictOutput):
    sentiment_score: Score100
    width: NonBlankText
    assessment: NonBlankText


class SectorRecommendation(_StrictOutput):
    name: NonBlankText
    confidence: Score10
    logic: NonBlankText
    risk: NonBlankText | None = None


class SectorChiefOutput(_StrictOutput):
    bull_sectors: list[SectorRecommendation]
    bear_sectors: list[SectorRecommendation]
    neutral_sectors: list[SectorRecommendation]
    operation_advice: NonBlankText
    risk_triggers: NonBlankText
    key_indicators: list[NonBlankText]


# Explicit aliases preserve the naming convention used by older callers while
# the concise names above match the prompt and task plan.
MainForceCapitalAnalysisOutput = MainForceCapitalOutput
MainForceIndustryAnalysisOutput = MainForceIndustryOutput
MainForceFundamentalAnalysisOutput = MainForceFundamentalOutput
MainForceTechnicalAnalysisOutput = MainForceTechnicalOutput
MainForceQuantAnalysisOutput = MainForceQuantOutput
MainForceResearcherAnalysisOutput = MainForceResearcherOutput
SectorMacroAnalysisOutput = SectorMacroOutput
SectorDiagnosisAnalysisOutput = SectorDiagnosisOutput
SectorCapitalAnalysisOutput = SectorCapitalOutput
SectorSentimentAnalysisOutput = SectorSentimentOutput
SectorChiefAnalysisOutput = SectorChiefOutput


__all__ = [
    "CapitalAnalysisOutput",
    "ChiefDecisionOutput",
    "ExcludedCompany",
    "FundamentalAnalysisOutput",
    "MainForceCapitalAnalysisOutput",
    "MainForceCapitalOutput",
    "MainForceFundamentalAnalysisOutput",
    "MainForceFundamentalOutput",
    "MainForceIndustryAnalysisOutput",
    "MainForceIndustryOutput",
    "MainForceQuantAnalysisOutput",
    "MainForceQuantOutput",
    "MainForceResearcherAnalysisOutput",
    "MainForceResearcherOutput",
    "MainForceTechnicalAnalysisOutput",
    "MainForceTechnicalOutput",
    "NewsAnalysisOutput",
    "RecommendedCompany",
    "SectorCapitalAnalysisOutput",
    "SectorCapitalOutput",
    "SectorChiefAnalysisOutput",
    "SectorChiefOutput",
    "SectorDiagnosisAnalysisOutput",
    "SectorDiagnosisOutput",
    "SectorMacroAnalysisOutput",
    "SectorMacroOutput",
    "SectorRecommendation",
    "SectorSentimentAnalysisOutput",
    "SectorSentimentOutput",
    "SentimentAnalysisOutput",
    "SupportResistance",
    "TechnicalAnalysisOutput",
]
