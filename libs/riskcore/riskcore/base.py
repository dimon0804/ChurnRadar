from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .context import AccountContext, ServiceContext
from .types import FeatureLevel


@dataclass(frozen=True, slots=True)
class Signal:
    """Сработавший признак: сила 0..1 и объяснение для маркетолога.

    facts — уже отформатированные значения (имя услуги, дата, сумма), которыми
    признак потом подставляет цифры в рекомендацию.
    """

    value: float
    explanation: str
    facts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError(f"Сила сигнала должна быть в [0, 1], получено {self.value}")


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Действие для маркетолога.

    Признак заполняет code, title и detail. Остальное дописывает модель: к какой услуге
    относится действие, сколько ожидаемых потерь оно может сберечь (евроценты) и является
    ли оно главным для клиента.
    """

    code: str
    title: str
    detail: str | None = None
    service_id: int | None = None
    expected_saving: int | None = None
    primary: bool = False

    def to_api(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "title": self.title}
        if self.detail:
            data["detail"] = self.detail
        if self.service_id is not None:
            data["service_id"] = self.service_id
        if self.expected_saving is not None:
            data["expected_saving"] = self.expected_saving
        if self.primary:
            data["primary"] = True
        return data


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    code: str
    title: str
    description: str
    group: str
    level: FeatureLevel
    default_weight: float
    min: float = 0.0
    max: float = 100.0


class Feature[C: (ServiceContext, AccountContext)](ABC):
    code: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    group: ClassVar[str]
    level: ClassVar[FeatureLevel]
    default_weight: ClassVar[float]

    @abstractmethod
    def compute(self, ctx: C) -> Signal | None:
        """Сигнал по контексту или None, если признак не применим или не сработал."""

    def recommend(self, signal: Signal) -> Recommendation | None:
        return None

    @property
    def spec(self) -> FeatureSpec:
        return FeatureSpec(
            code=self.code,
            title=self.title,
            description=self.description,
            group=self.group,
            level=self.level,
            default_weight=self.default_weight,
        )


class ServiceFeature(Feature[ServiceContext]):
    level: ClassVar[FeatureLevel] = "service"


class AccountFeature(Feature[AccountContext]):
    level: ClassVar[FeatureLevel] = "account"
