"""Группы коррелированных признаков.

Noisy-OR считает причины независимыми. У неоплаченной услуги это не так: «ждёт оплаты»,
«не хватит денег» и «платежи не проходят» часто описывают один и тот же факт (денег на
продление нет), и без группировки он входит в вероятность три раза.

Группа объединяет такие признаки: внутри группы силы складываются тем же noisy-OR, но вклад
группы целиком ограничен потолком cap. Наружу группа выходит одной причиной, дальше группы
и одиночные признаки складываются обычным noisy-OR. Если ни один потолок не достигнут,
результат совпадает с моделью без групп.

Разложение по причинам остаётся точным: вероятность сначала делится между группами и
одиночными признаками пропорционально −ln(1 − p_b), затем доля группы делится между её
признаками пропорционально −ln(1 − s_i). Сумма вкладов по-прежнему равна p.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .types import GroupSetting

LOG_CAP = 50.0


@dataclass(frozen=True, slots=True)
class GroupSpec:
    code: str
    title: str
    description: str
    features: tuple[str, ...]
    default_cap: float
    default_enabled: bool


GROUPS: dict[str, GroupSpec] = {
    "payment": GroupSpec(
        code="payment",
        title="Оплата",
        description=(
            "«Ждёт оплаты», «не хватит денег» и «платежи не проходят» часто говорят об одном: "
            "денег на продление нет. В группе их вклад складывается, но не выше потолка."
        ),
        features=("prolong_wait", "insufficient_funds", "failed_payments"),
        default_cap=85.0,
        default_enabled=False,
    ),
    "web": GroupSpec(
        code="web",
        title="Поведение на сайте",
        description=(
            "Три признака из Яндекс Метрики: страница отмены, давно нет в панели, визитов стало "
            "меньше. Редкие заходы видны сразу в двух последних, поэтому их вклад ограничен потолком."
        ),
        features=("cancel_page_views", "panel_absence", "web_activity_drop"),
        default_cap=50.0,
        default_enabled=False,
    ),
}


def default_groups() -> dict[str, GroupSetting]:
    return {code: GroupSetting(cap=g.default_cap, enabled=g.default_enabled) for code, g in GROUPS.items()}


def group_of(code: str, groups: Mapping[str, GroupSetting]) -> str | None:
    """Код включённой группы, в которую входит признак, или None."""
    for group_code, spec in GROUPS.items():
        entry = groups.get(group_code)
        if entry is not None and entry["enabled"] and code in spec.features:
            return group_code
    return None


def _noisy_or(strengths: Sequence[float]) -> float:
    survive = 1.0
    for s in strengths:
        survive *= 1.0 - s
    return 1.0 - survive


def _log_weight(p: float) -> float:
    if p >= 1.0:
        return LOG_CAP
    return min(-math.log1p(-p), LOG_CAP)


def _split(total: float, parts: Sequence[float]) -> list[float]:
    logs = [_log_weight(p) for p in parts]
    norm = sum(logs)
    if norm <= 0:
        return [0.0 for _ in parts]
    return [total * log / norm for log in logs]


def combine(
    strengths: Sequence[float],
    codes: Sequence[str],
    groups: Mapping[str, GroupSetting],
) -> tuple[float, list[float], list[str | None]]:
    """Вероятность, вклады по признакам (в том же порядке) и группа каждого признака."""
    member_of = [group_of(code, groups) for code in codes]
    buckets: dict[str, list[int]] = {}
    order: list[str] = []
    for i, group in enumerate(member_of):
        key = group if group is not None else f"#{i}"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(i)

    bucket_p: list[float] = []
    for key in order:
        members = [strengths[i] for i in buckets[key]]
        p = _noisy_or(members)
        if not key.startswith("#"):
            p = min(p, groups[key]["cap"] / 100.0)
        bucket_p.append(p)

    probability = _noisy_or(bucket_p)
    contributions = [0.0] * len(strengths)
    for key, share in zip(order, _split(probability, bucket_p), strict=True):
        members = buckets[key]
        for i, part in zip(members, _split(share, [strengths[i] for i in members]), strict=True):
            contributions[i] = part
    return probability, contributions, member_of


def probability(
    strengths: Sequence[float], codes: Sequence[str], groups: Mapping[str, GroupSetting]
) -> float:
    return combine(strengths, codes, groups)[0]
